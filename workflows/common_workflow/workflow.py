import logging
from typing import Dict, Any
from workflows.base_workflow import BaseWorkflow
from workflows.errors import RetryableError, HardError
from cloudinary_manager import get_cloudinary_manager
from workflows import notify_error, ErrorType

logger = logging.getLogger(__name__)


class CommonWorkflowWorkflow(BaseWorkflow):
    """
    Gallery Remix — a generic face swap that works on ANY image the user picked
    from the spherical gallery. Unlike the team workflows (CSK/MI/RCB) which bake
    a fixed `reference_image_b` into config.json, here the BASE SCENE is dynamic:
    it is the gallery card's image_url, sent by the frontend in the request payload
    (sourced from the static pool.json). This workflow NEVER reads the image list or
    the reference image from Supabase — the reference arrives only via the payload.

    Pipeline:
      upload  -> pass through the uploaded face + the gallery reference + gender
      image_edit -> swap the uploaded identity onto the gallery base scene
    """

    async def step_upload(self, input_file: Any, step_config: Dict) -> Dict[str, Any]:
        try:
            logger.info("Processing image upload step (common-workflow)")

            # app.py sends a dict here: the uploaded face (image_url), the gallery
            # reference scene (reference_image_url), and the gender_version.
            if isinstance(input_file, dict):
                image_url = input_file.get('image_url')
                if not image_url:
                    raise HardError("No image_url (uploaded face) found in input data")

                reference_image_url = input_file.get('reference_image_url')
                if not reference_image_url:
                    # The base scene MUST come from the frontend (gallery/pool.json).
                    raise HardError("No reference_image_url (gallery image) found in input data")

                logger.info(f"Face (Image A): {image_url}")
                logger.info(f"Gallery reference scene (base): {reference_image_url}")
                return {
                    "image_url": image_url,
                    "reference_image_url": reference_image_url,
                    "public_id": None,
                    "format": 'jpg',
                    "width": None,
                    "height": None,
                    "gender_version": input_file.get('gender_version')
                }

            # A bare string means no reference image was supplied — this workflow
            # cannot run without a gallery base scene.
            raise HardError("Gallery Remix requires a reference image selected from the gallery")
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

    async def step_image_edit(self, input_data: Dict, step_config: Dict) -> Dict[str, Any]:
        from multi_endpoint_manager import get_endpoint_manager

        logger.info("Starting Gallery Remix face swap step")
        endpoint_manager = get_endpoint_manager()

        model = step_config.get('model', step_config.get('default_model', 'nano-banana-ondemand'))
        provider = step_config.get('provider', 'vision-ondemand')

        # Select prompt based on gender_version passed from the upload step.
        gender_version = input_data.get('gender_version') or 'male'
        prompt_key = f'image_edit_{gender_version}'
        prompt = self.config['default_prompts'].get(prompt_key)
        if not prompt:
            prompt = self.config['default_prompts'].get('image_edit', self.config['default_prompts'].get('image_edit_male'))
            logger.warning(f"Prompt key '{prompt_key}' not found, using fallback")

        logger.info(f"Using gender_version: {gender_version}, prompt_key: {prompt_key}")

        # Image A = user's uploaded face (identity source).
        # Base scene = the gallery image the user clicked (from pool.json), NOT a
        # fixed config image and NOT fetched from Supabase.
        user_face_url = input_data['image_url']
        base_scene_url = input_data.get('reference_image_url')
        if not base_scene_url:
            raise HardError("Missing reference_image_url at image_edit step")

        logger.info(f"Using model: {model}, provider: {provider}")
        logger.info(f"Base scene (gallery image): {base_scene_url}")
        logger.info(f"Image A (user face/identity): {user_face_url}")
        logger.info(f"Prompt: {prompt[:100]}...")

        # We want a BRAND-NEW image that RE-RENDERS the real user (identity, body,
        # skin tone) into the STYLE of the gallery image — NOT a head-swap that
        # pastes the user's face onto the gallery scene.
        #
        # Nano Banana EDITS the FIRST image in the array (it is the base canvas).
        # So the user's photo MUST be first (keep the real person) and the gallery
        # image is the SECOND/style reference (apply its look, scene, lighting).
        # This is the opposite order from the team workflows (CSK/MI/RCB), which
        # intentionally do a face-swap onto a fixed scene.
        generation_params = {
            'prompt': prompt,
            'model': model,
            'provider_key': provider,
            'input_image_url': user_face_url,         # FIRST = base canvas (USER identity/body/skin)
            'reference_image_url': base_scene_url,    # SECOND = style/scene reference (gallery)
            'aspect_ratio': '1:1',
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
