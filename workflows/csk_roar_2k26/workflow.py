import logging
from typing import Dict, Any
from workflows.base_workflow import BaseWorkflow
from workflows.errors import RetryableError, HardError
from cloudinary_manager import get_cloudinary_manager
from workflows import notify_error, ErrorType

logger = logging.getLogger(__name__)

class CskRoar2k26Workflow(BaseWorkflow):
    async def step_upload(self, input_file: Any, step_config: Dict) -> Dict[str, Any]:
        try:
            logger.info("Processing image upload step")

            # If input_data is a dict (e.g. from app.py with gender_version), extract image_url
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
                    "gender_version": input_file.get('gender_version')
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

        logger.info("Starting CSK Roar 2K26 face swap step")
        endpoint_manager = get_endpoint_manager()

        model = step_config.get('model', step_config.get('default_model', 'nano-banana-ondemand'))
        provider = step_config.get('provider', 'vision-ondemand')

        # Select prompt based on gender_version passed from upload step
        gender_version = input_data.get('gender_version', 'male')
        prompt_key = f'image_edit_{gender_version}'
        prompt = self.config['default_prompts'].get(prompt_key)
        if not prompt:
            # Fallback to default if versioned prompt not found
            prompt = self.config['default_prompts'].get('image_edit', self.config['default_prompts'].get('image_edit_male'))
            logger.warning(f"Prompt key '{prompt_key}' not found, using fallback")

        logger.info(f"Using gender_version: {gender_version}, prompt_key: {prompt_key}")

        # Image A = user's face (identity source), Image B = CSK reference image (base scene)
        user_face_url = input_data['image_url']
        reference_image_b = self.config.get('reference_image_b', 'https://res.cloudinary.com/dnagl4r2t/image/upload/v1776050394/csk_final1__1_cx0px8.png')

        logger.info(f"Using model: {model}, provider: {provider}")
        logger.info(f"Image A (user face/identity): {user_face_url}")
        logger.info(f"Image B (CSK reference/base): {reference_image_b}")
        logger.info(f"Prompt: {prompt[:100]}...")

        # IMPORTANT: Order matters for Nano Banana PRO direct call!
        # First URL = base image (scene to edit) = CSK reference
        # Second URL = face identity source = user's face
        # This matches the prompt: "Use Image B (first) as base, Image A (second) as face"
        generation_params = {
            'prompt': prompt,
            'model': model,
            'provider_key': provider,
            'input_image_url': reference_image_b,      # FIRST = base scene (CSK)
            'reference_image_url': user_face_url,      # SECOND = face identity (user)
            'aspect_ratio': '1:1',
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

    async def step_upscale(self, input_data: Dict, step_config: Dict) -> Dict[str, Any]:
        from multi_endpoint_manager import get_endpoint_manager

        logger.info("Starting upscale step with Clipdrop")
        endpoint_manager = get_endpoint_manager()

        model = step_config.get('model', step_config.get('default_model', 'clipdrop-upscale'))
        provider = step_config.get('provider', 'vision-clipdrop')
        prompt = self.config['default_prompts']['upscale']

        logger.info(f"Using model: {model}, provider: {provider}")
        logger.info(f"Prompt: {prompt}")

        generation_params = {
            'prompt': prompt,
            'model': model,
            'provider_key': provider,
            'input_image_url': input_data['edited_image_url'],
            'aspect_ratio': '1:1',
            'job_id': self.job_id
        }

        result = await endpoint_manager.generate_image(**generation_params)

        if result.get('is_raw_bytes'):
            logger.info("Upscale returned raw bytes, uploading to Cloudinary")

            image_bytes = result.get('data')
            image_size_mb = len(image_bytes) / (1024 * 1024)
            logger.info(f"Upscaled image size: {image_size_mb:.2f} MB")

            max_upload_size_mb = 10
            if image_size_mb > max_upload_size_mb:
                logger.info(f"Image size ({image_size_mb:.2f} MB) exceeds {max_upload_size_mb}MB upload limit")
                logger.info("Resizing image dimensions to reduce file size...")

                from PIL import Image
                from io import BytesIO

                img = Image.open(BytesIO(image_bytes))
                original_width, original_height = img.size
                logger.info(f"Original dimensions: {original_width}x{original_height}")

                target_size_mb = 9.8
                scale_factor = (target_size_mb / image_size_mb) ** 0.5
                new_width = int(original_width * scale_factor)
                new_height = int(original_height * scale_factor)

                logger.info(f"Resizing to: {new_width}x{new_height}")
                img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)

                output = BytesIO()
                img.save(output, format='JPEG', quality=95, optimize=True)
                image_bytes = output.getvalue()

                final_size_mb = len(image_bytes) / (1024 * 1024)
                logger.info(f"Resized image size: {final_size_mb:.2f} MB")

            eager_transformations = [{"quality": "auto", "fetch_format": "auto"}]

            cloudinary = get_cloudinary_manager()
            upload_result = cloudinary.upload_image_from_bytes(
                image_bytes,
                "upscaled_image.jpg",
                folder_name="workflow-upscaled",
                eager=eager_transformations
            )

            if upload_result.get('success') is False:
                raise HardError(f"Failed to upload upscaled image to Cloudinary: {upload_result.get('error')}")

            upscaled_url = upload_result.get('secure_url')
            logger.info(f"Upscaled image uploaded to Cloudinary: {upscaled_url}")
        else:
            upscaled_url = result.get('image_url') or result.get('url')

        return {
            "edited_image_url": upscaled_url,
            "upscaled_image_url": upscaled_url,
            "model_used": model,
            "prompt": prompt,
            "input_image": input_data['edited_image_url'],
            "original_image": input_data.get('original_image')
        }
