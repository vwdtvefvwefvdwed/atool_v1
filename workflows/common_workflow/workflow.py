import logging
from typing import Dict, Any
from workflows.base_workflow import BaseWorkflow
from workflows.errors import RetryableError, HardError
from cloudinary_manager import get_cloudinary_manager
from workflows import notify_error, ErrorType

logger = logging.getLogger(__name__)


class CommonWorkflowWorkflow(BaseWorkflow):
    """
    Gallery Remix — regenerates the USER into the scene of any gallery card.

    NO REFERENCE IMAGE is used anywhere in this workflow. The frontend sends
    only the selected pool.json image's id; app.py resolves the matching
    per-image PROMPT live from the gallery Supabase project (prompt_store.py,
    GALLERY_SUPABASE_URL) and passes it in as `reference_prompt`. Generation
    inputs are exactly two things:

        1. the user's uploaded photo (identity), and
        2. the composed prompt (gender identity template + per-image scene prompt).

    This workflow itself never queries a database and never forwards a gallery
    image to the model.

    Pipeline:
      upload     -> pass through the uploaded face + gender + resolved prompt
      image_edit -> single-image edit: re-render the user into the scene
    """

    async def step_upload(self, input_file: Any, step_config: Dict) -> Dict[str, Any]:
        try:
            logger.info("Processing image upload step (common-workflow)")

            # app.py sends a dict here: the uploaded face (image_url), the
            # gender_version, and the per-image prompt resolved by id
            # (reference_prompt; may be None -> default template only).
            if isinstance(input_file, dict):
                image_url = input_file.get('image_url')
                if not image_url:
                    raise HardError("No image_url (uploaded face) found in input data")

                reference_prompt = input_file.get('reference_prompt')
                logger.info(f"Face (user identity): {image_url}")
                logger.info(
                    "Per-image scene prompt: "
                    + (f"{reference_prompt[:80]}..." if reference_prompt else "none (default template only)")
                )
                return {
                    "image_url": image_url,
                    "reference_prompt": reference_prompt,
                    "public_id": None,
                    "format": 'jpg',
                    "width": None,
                    "height": None,
                    "gender_version": input_file.get('gender_version'),
                    # User-requested output ratio (validated in app.py) — the
                    # On-Demand chat orchestrator honours it via a prompt hint.
                    "aspect_ratio": input_file.get('aspect_ratio') or '1:1'
                }

            # A bare string is just the uploaded face — run with the default
            # template (legacy requests from old cached frontends).
            if isinstance(input_file, str) and input_file:
                logger.info("Legacy input (bare URL) — no per-image prompt")
                return {
                    "image_url": input_file,
                    "reference_prompt": None,
                    "public_id": None,
                    "format": 'jpg',
                    "width": None,
                    "height": None,
                    "gender_version": None,
                    "aspect_ratio": '1:1'
                }

            raise HardError("Gallery Remix requires an uploaded face image")
        except HardError:
            raise
        except Exception as e:
            logger.error(f"Failed in upload step: {e}")
            _emsg = str(e).lower()
            if any(t in _emsg for t in ['cloudinary', 'timeout', 'connection', 'network', 'upload', 'httpsconnectionpool']):
                raise RetryableError(
                    f"Transient upload error — will retry: {e}",
                    error_type='timeout', retry_count=0, model='upload', provider='cloudinary'
                )
            raise HardError(f"Failed to process input: {e}")

    def _compose_prompt(self, template: str, scene_prompt: str) -> str:
        """Final prompt = gender identity template + the per-image scene prompt
        resolved live from the gallery project. The template stays authoritative for
        identity-preservation rules; the scene prompt supplies the look/scene of
        the gallery card the user picked. Kept in ONE place so prompt tuning
        never touches pipeline logic."""
        if not scene_prompt:
            return template
        return (
            f"{template}\n\n"
            "SCENE / STYLE DESCRIPTION (recreate this scene around the person):\n"
            f"{scene_prompt}"
        )

    async def step_image_edit(self, input_data: Dict, step_config: Dict) -> Dict[str, Any]:
        from multi_endpoint_manager import get_endpoint_manager

        logger.info("Starting Gallery Remix generation step")
        endpoint_manager = get_endpoint_manager()

        model = step_config.get('model', step_config.get('default_model', 'gpt-image-2-ondemand'))
        provider = step_config.get('provider', 'vision-ondemand')

        # Select the identity template based on gender_version from the upload step.
        gender_version = input_data.get('gender_version') or 'male'
        prompt_key = f'image_edit_{gender_version}'
        template = self.config['default_prompts'].get(prompt_key)
        if not template:
            template = self.config['default_prompts'].get('image_edit', self.config['default_prompts'].get('image_edit_male'))
            logger.warning(f"Prompt key '{prompt_key}' not found, using fallback")

        # Compose with the per-image prompt (may be None -> template only).
        scene_prompt = input_data.get('reference_prompt')
        prompt = self._compose_prompt(template, scene_prompt)

        logger.info(f"Using gender_version: {gender_version}, prompt_key: {prompt_key}")
        logger.info(f"Per-image scene prompt applied: {bool(scene_prompt)}")

        # SINGLE-IMAGE generation: the ONLY image sent to the model is the
        # user's uploaded photo (identity/base canvas). The gallery image is
        # never forwarded — its scene is reproduced from the prompt alone.
        user_face_url = input_data['image_url']

        logger.info(f"Using model: {model}, provider: {provider}")
        logger.info(f"Input image (user face/identity): {user_face_url}")
        logger.info(f"Prompt: {prompt[:100]}...")

        # User-requested output ratio (from the upload step; defaults to 1:1).
        # Verified live (2026-07): the On-Demand chat orchestrator honours the
        # ratio hint — 1:1 / 16:9 / 3:2 exact; 9:16 clamps to 1024x1536 (≈2:3).
        aspect_ratio = input_data.get('aspect_ratio') or self.requested_aspect_ratio or '1:1'
        logger.info(f"Requested aspect ratio: {aspect_ratio}")

        generation_params = {
            'prompt': prompt,
            'model': model,
            'provider_key': provider,
            'input_image_url': user_face_url,  # the ONLY image: user identity/base canvas
            'aspect_ratio': aspect_ratio,
            'job_id': self.job_id
        }

        result = await endpoint_manager.generate_image(**generation_params)

        edited_url = result.get('image_url') or result.get('url')

        if not edited_url and result.get('is_base64') and result.get('data'):
            logger.info("img2img returned base64 — uploading to Cloudinary")
            import base64 as _b64
            from io import BytesIO  # noqa: F401
            cloudinary = get_cloudinary_manager()
            image_bytes = _b64.b64decode(result['data'])
            upload_result = cloudinary.upload_image_from_bytes(
                image_bytes,
                "aicc_edited.jpg",
                folder_name="workflow-edited"
            )
            if upload_result.get('success') is False:
                raise HardError(f"Failed to upload base64 result to Cloudinary: {upload_result.get('error')}")
            edited_url = upload_result.get('secure_url') or upload_result.get('url')
            logger.info(f"Uploaded base64 result to Cloudinary: {edited_url}")

        if not edited_url:
            raise HardError("Image edit step returned no URL and no base64 data")

        return {
            "edited_image_url": edited_url,
            "model_used": model,
            "prompt": prompt,
            "original_image": user_face_url
        }
