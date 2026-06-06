import logging
from typing import Dict, Any
from workflows.base_workflow import BaseWorkflow
from workflows.errors import RetryableError, HardError
from cloudinary_manager import get_cloudinary_manager

logger = logging.getLogger(__name__)

# Pose prompt keys in render order. pose_1 is the hero shot (also upscaled).
POSE_KEYS = [
    "pose_1_hero",
    "pose_2_walking",
    "pose_3_closeup",
    "pose_4_low_angle",
    "pose_5_motion_turn",
]


class SpeedRampEditWorkflow(BaseWorkflow):
    async def step_upload(self, input_file: Any, step_config: Dict) -> Dict[str, Any]:
        """Upload the user's image (Image A). Mirrors CSK upload handling."""
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
                }

            if isinstance(input_file, str) and (input_file.startswith('http://') or input_file.startswith('https://')):
                logger.info(f"Image already uploaded: {input_file}")
                return {
                    "image_url": input_file,
                    "public_id": None,
                    "format": 'jpg',
                    "width": None,
                    "height": None,
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
                "height": result.get('height'),
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

    async def step_generate_poses(self, input_data: Dict, step_config: Dict) -> Dict[str, Any]:
        """Generate 5 cinematic poses from the user's image (Image A only, single-image mode).

        Each pose is independent: a failure on one pose is logged and skipped so the
        reel can still be built from the remaining poses. Only raises HardError if
        fewer than 2 poses succeed (not enough material for an edit).
        """
        from multi_endpoint_manager import get_endpoint_manager

        logger.info("Starting Speed Ramp Edit pose generation (5 poses)")
        endpoint_manager = get_endpoint_manager()

        model = step_config.get('model', step_config.get('default_model', 'nano-banana-ondemand'))
        provider = step_config.get('provider', 'vision-ondemand')

        user_face_url = input_data['image_url']
        logger.info(f"Using model: {model}, provider: {provider}")
        logger.info(f"Image A (user face/identity): {user_face_url}")

        output: Dict[str, Any] = {"original_image": user_face_url}
        succeeded = 0
        last_transient_error = None

        for idx, pose_key in enumerate(POSE_KEYS, start=1):
            prompt = self.config['default_prompts'].get(pose_key)
            if not prompt:
                logger.warning(f"Prompt '{pose_key}' missing from config — skipping pose {idx}")
                continue

            try:
                logger.info(f"Generating pose {idx}/5 ({pose_key})")
                generation_params = {
                    'prompt': prompt,
                    'model': model,
                    'provider_key': provider,
                    'input_image_url': user_face_url,   # Image A = single facial reference
                    'aspect_ratio': '9:16',             # vertical, matches reel output
                    'job_id': self.job_id,
                }

                result = await endpoint_manager.generate_image(**generation_params)
                pose_url = result.get('image_url') or result.get('url')

                # Handle base64 result → upload to Cloudinary (same as CSK)
                if not pose_url and result.get('is_base64') and result.get('data'):
                    logger.info(f"Pose {idx} returned base64 — uploading to Cloudinary")
                    import base64 as _b64
                    cloudinary = get_cloudinary_manager()
                    image_bytes = _b64.b64decode(result['data'])
                    upload_result = cloudinary.upload_image_from_bytes(
                        image_bytes,
                        f"speed_ramp_pose_{idx}.jpg",
                        folder_name="workflow-edited",
                    )
                    if upload_result.get('success') is False:
                        raise Exception(f"Cloudinary upload failed: {upload_result.get('error')}")
                    pose_url = upload_result.get('secure_url') or upload_result.get('url')

                # Handle raw bytes result → upload to Cloudinary
                if not pose_url and result.get('is_raw_bytes') and result.get('data'):
                    logger.info(f"Pose {idx} returned raw bytes — uploading to Cloudinary")
                    cloudinary = get_cloudinary_manager()
                    upload_result = cloudinary.upload_image_from_bytes(
                        result['data'],
                        f"speed_ramp_pose_{idx}.jpg",
                        folder_name="workflow-edited",
                    )
                    if upload_result.get('success') is False:
                        raise Exception(f"Cloudinary upload failed: {upload_result.get('error')}")
                    pose_url = upload_result.get('secure_url') or upload_result.get('url')

                if not pose_url:
                    raise Exception("Generation returned no URL and no image data")

                output[f"pose_{idx}_url"] = pose_url
                succeeded += 1
                logger.info(f"Pose {idx} done: {pose_url}")

            except Exception as e:
                logger.error(f"Pose {idx} ({pose_key}) failed — skipping: {e}")
                _emsg = str(e).lower()
                if any(t in _emsg for t in ['timeout', 'connection', 'network', 'rate', 'busy', '503', '429']):
                    last_transient_error = e
                continue

        if succeeded == 0:
            # Nothing generated. If failures looked transient, let the retry manager retry.
            if last_transient_error is not None:
                raise RetryableError(
                    f"All pose generations failed (transient) — will retry: {last_transient_error}",
                    error_type='timeout', retry_count=0, model=model, provider=provider
                )
            raise HardError("All pose generations failed")

        if succeeded < 2:
            logger.warning(f"Only {succeeded} pose generated — reel will have limited material")

        output["poses_generated"] = succeeded
        output["model_used"] = model

        # Backward-compatible final image key so ResultDisplay shows something
        # (use the hero pose, or the first pose that succeeded).
        hero_url = output.get('pose_1_url')
        if not hero_url:
            for idx in range(1, 6):
                if output.get(f'pose_{idx}_url'):
                    hero_url = output[f'pose_{idx}_url']
                    break
        if hero_url:
            output["image_url"] = hero_url
            output["edited_image_url"] = hero_url

        logger.info(f"Pose generation complete: {succeeded}/5 succeeded")
        return output
