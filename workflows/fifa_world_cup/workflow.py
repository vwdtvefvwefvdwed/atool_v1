import logging
from typing import Dict, Any
from workflows.base_workflow import BaseWorkflow
from workflows.errors import RetryableError, HardError
from cloudinary_manager import get_cloudinary_manager
from workflows import notify_error, ErrorType

logger = logging.getLogger(__name__)

# Players supported by FIFA Legend Mode. Used to validate the incoming
# `player` parameter before building the prompt key.
SUPPORTED_PLAYERS = {'ronaldo', 'messi', 'neymar', 'ochoa', 'christian'}


class FifaWorldCupWorkflow(BaseWorkflow):
    async def step_upload(self, input_file: Any, step_config: Dict) -> Dict[str, Any]:
        try:
            logger.info("Processing image upload step")

            # If input_data is a dict (from app.py with gender_version/player), extract image_url
            if isinstance(input_file, dict):
                image_url = input_file.get('image_url')
                if not image_url:
                    raise HardError("No image_url found in input data")
                logger.info(f"Image already uploaded (dict input): {image_url}")
                return {
                    "image_url": image_url,
                    "public_id": None,
                    "format": 'jpg',
                    "width": None,
                    "height": None,
                    "gender_version": input_file.get('gender_version'),
                    "player": input_file.get('player')
                }

            if isinstance(input_file, str) and (input_file.startswith('http://') or input_file.startswith('https://')):
                logger.info(f"Image already uploaded: {input_file}")
                return {
                    "image_url": input_file,
                    "public_id": None,
                    "format": 'jpg',
                    "width": None,
                    "height": None
                }

            cloudinary = get_cloudinary_manager()

            if isinstance(input_file, str):
                result = cloudinary.upload_image_from_url(input_file)
            else:
                result = cloudinary.upload_image(input_file)

            if result.get('success') is False:
                raise HardError(result.get('error', 'Upload failed'))

            return {
                "image_url": result.get('secure_url'),
                "public_id": result.get('public_id'),
                "format": result.get('format', 'jpg'),
                "width": result.get('width'),
                "height": result.get('height')
            }
        except HardError:
            raise
        except Exception as e:
            logger.error(f"Failed to upload image: {e}")
            _emsg = str(e).lower()
            if any(t in _emsg for t in ['cloudinary', 'timeout', 'connection', 'network', 'upload', 'httpsconnectionpool']):
                raise RetryableError(
                    f"Transient upload error — will retry: {e}",
                    error_type='timeout', retry_count=0, model='upload', provider='cloudinary'
                )
            raise HardError(f"Failed to upload image: {e}")

    async def step_image_edit(self, input_data: Dict, step_config: Dict) -> Dict[str, Any]:
        from multi_endpoint_manager import get_endpoint_manager

        logger.info("Starting FIFA Legend Mode image edit step")
        endpoint_manager = get_endpoint_manager()

        model = step_config.get('model', step_config.get('default_model', 'gpt-image-2-ondemand'))
        provider = step_config.get('provider', 'vision-ondemand')

        # Select prompt based on player + gender_version passed from upload step
        player = (input_data.get('player') or 'ronaldo').lower()
        if player not in SUPPORTED_PLAYERS:
            logger.warning(f"Unknown player '{player}', falling back to 'ronaldo'")
            player = 'ronaldo'

        gender_version = input_data.get('gender_version') or 'male'
        if gender_version not in ('male', 'female'):
            gender_version = 'male'

        prompt_key = f'image_edit_{player}_{gender_version}'
        prompt = self.config['default_prompts'].get(prompt_key)
        if not prompt:
            # Fallback: try the male version of the same player, then ronaldo male
            fallback_key = f'image_edit_{player}_male'
            prompt = (
                self.config['default_prompts'].get(fallback_key)
                or self.config['default_prompts'].get('image_edit_ronaldo_male')
            )
            logger.warning(f"Prompt key '{prompt_key}' not found, using fallback")

        logger.info(f"Using player: {player}, gender_version: {gender_version}, prompt_key: {prompt_key}")

        # Single-image edit: the user's uploaded face is the only input image.
        user_face_url = input_data['image_url']

        logger.info(f"Using model: {model}, provider: {provider}")
        logger.info(f"Image (user face/identity): {user_face_url}")
        logger.info(f"Prompt: {prompt[:100]}...")

        generation_params = {
            'prompt': prompt,
            'model': model,
            'provider_key': provider,
            'input_image_url': user_face_url,
            'aspect_ratio': '9:16',
            'job_id': self.job_id
        }

        result = await endpoint_manager.generate_image(**generation_params)

        edited_url = result.get('image_url') or result.get('url')

        if not edited_url and result.get('is_base64') and result.get('data'):
            logger.info("img2img returned base64 — uploading to Cloudinary")
            import base64 as _b64
            from io import BytesIO
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
