import logging
from typing import Dict, Any
from workflows.base_workflow import BaseWorkflow
from workflows.errors import RetryableError, HardError
from cloudinary_manager import get_cloudinary_manager

logger = logging.getLogger(__name__)

# Pose prompt keys (image) in order; pose 1 is the hero/intro.
POSE_KEYS = [
    "pose_1_hand_cover",
    "pose_2_hands_behind_head",
    "pose_3_side_profile",
    "pose_4_looking_down",
    "pose_5_walking",
]

# Matching image-to-video motion prompt keys, same order.
VIDEO_KEYS = [
    "video_1_hand_cover",
    "video_2_hands_behind_head",
    "video_3_side_profile",
    "video_4_looking_down",
    "video_5_walking",
]

# 3-second clips: 72 frames @ 24fps (deAPI override bypasses the 8s floor).
VIDEO_DURATION_SEC = 3
VIDEO_FRAMES = 72
VIDEO_FPS = 24


class AnimeEditWorkflow(BaseWorkflow):
    async def step_upload(self, input_file: Any, step_config: Dict) -> Dict[str, Any]:
        """Upload the user's image (Image A). Mirrors the CSK / speed-ramp pattern."""
        try:
            logger.info("Processing image upload step")

            if isinstance(input_file, dict):
                image_url = input_file.get('image_url')
                if not image_url:
                    raise HardError("No image_url found in input data")
                return {
                    "image_url": image_url, "public_id": None,
                    "format": 'jpg', "width": None, "height": None,
                }

            if isinstance(input_file, str) and (input_file.startswith('http://') or input_file.startswith('https://')):
                return {
                    "image_url": input_file, "public_id": None,
                    "format": 'jpg', "width": None, "height": None,
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
        """Generate 5 cinematic poses from the user's image (single-image mode)."""
        from multi_endpoint_manager import get_endpoint_manager

        logger.info("Anime Edit: generating 5 poses")
        endpoint_manager = get_endpoint_manager()

        model = step_config.get('model', step_config.get('default_model', 'gpt-image-2-ondemand'))
        provider = step_config.get('provider', 'vision-ondemand')
        user_face_url = input_data['image_url']

        output: Dict[str, Any] = {"original_image": user_face_url}
        succeeded = 0
        last_transient = None

        for idx, pose_key in enumerate(POSE_KEYS, start=1):
            prompt = self.config['default_prompts'].get(pose_key)
            if not prompt:
                logger.warning(f"Missing prompt '{pose_key}', skipping pose {idx}")
                continue
            try:
                logger.info(f"Generating pose {idx}/5 ({pose_key})")
                result = await endpoint_manager.generate_image(
                    prompt=prompt,
                    model=model,
                    provider_key=provider,
                    input_image_url=user_face_url,
                    aspect_ratio=self.requested_aspect_ratio or '9:16',
                    job_id=self.job_id,
                )
                pose_url = result.get('image_url') or result.get('url')

                if not pose_url and result.get('is_base64') and result.get('data'):
                    import base64 as _b64
                    cloudinary = get_cloudinary_manager()
                    up = cloudinary.upload_image_from_bytes(
                        _b64.b64decode(result['data']), f"anime_pose_{idx}.jpg", folder_name="workflow-edited")
                    if up.get('success') is False:
                        raise Exception(f"Cloudinary upload failed: {up.get('error')}")
                    pose_url = up.get('secure_url') or up.get('url')

                if not pose_url and result.get('is_raw_bytes') and result.get('data'):
                    cloudinary = get_cloudinary_manager()
                    up = cloudinary.upload_image_from_bytes(
                        result['data'], f"anime_pose_{idx}.jpg", folder_name="workflow-edited")
                    if up.get('success') is False:
                        raise Exception(f"Cloudinary upload failed: {up.get('error')}")
                    pose_url = up.get('secure_url') or up.get('url')

                if not pose_url:
                    raise Exception("Generation returned no URL and no image data")

                output[f"pose_{idx}_url"] = pose_url
                succeeded += 1
                logger.info(f"Pose {idx} done: {pose_url}")
            except Exception as e:
                logger.error(f"Pose {idx} ({pose_key}) failed — skipping: {e}")
                _emsg = str(e).lower()
                if any(t in _emsg for t in ['timeout', 'connection', 'network', 'rate', 'busy', '503', '429']):
                    last_transient = e
                continue

        if succeeded == 0:
            if last_transient is not None:
                raise RetryableError(
                    f"All pose generations failed (transient) — will retry: {last_transient}",
                    error_type='timeout', retry_count=0, model=model, provider=provider)
            raise HardError("All pose generations failed")

        output["poses_generated"] = succeeded
        output["model_used"] = model
        # backward-compatible final image for ResultDisplay
        hero = output.get('pose_1_url') or next((output.get(f'pose_{i}_url') for i in range(1, 6) if output.get(f'pose_{i}_url')), None)
        if hero:
            output["image_url"] = hero
        logger.info(f"Pose generation complete: {succeeded}/5")
        return output

    async def step_generate_videos(self, input_data: Dict, step_config: Dict) -> Dict[str, Any]:
        """Animate each pose into a 3-second LTX motion clip (image-to-video)."""
        from multi_endpoint_manager import get_endpoint_manager

        logger.info("Anime Edit: generating 5 motion clips (3s each)")
        endpoint_manager = get_endpoint_manager()

        model = step_config.get('model', step_config.get('default_model', 'ltx2-19b-dist-fp8-deapi'))
        provider = step_config.get('provider', 'cinematic-deapi')

        # pass poses through so all assets remain available to the editor
        output: Dict[str, Any] = dict(input_data)
        succeeded = 0
        last_transient = None

        for idx in range(1, 6):
            pose_url = input_data.get(f'pose_{idx}_url')
            if not pose_url:
                logger.warning(f"No pose_{idx}_url — skipping video {idx}")
                continue
            video_prompt = self.config['default_prompts'].get(VIDEO_KEYS[idx - 1], "Subtle cinematic motion, realistic breathing and blinking, gentle camera movement.")
            try:
                logger.info(f"Generating video {idx}/5 from pose {idx}")
                result = await endpoint_manager.generate_video(
                    prompt=video_prompt,
                    model=model,
                    provider_key=provider,
                    input_image_url=pose_url,
                    duration=VIDEO_DURATION_SEC,
                    aspect_ratio=self.requested_aspect_ratio or '9:16',
                    job_id=self.job_id,
                    video_frames=VIDEO_FRAMES,
                    video_fps=VIDEO_FPS,
                )
                video_url = result.get('video_url') or result.get('url')

                # If deAPI returns raw bytes / base64, upload to Cloudinary as video
                if not video_url and result.get('is_base64') and result.get('data'):
                    import base64 as _b64
                    cloudinary = get_cloudinary_manager()
                    up = cloudinary.upload_video(_b64.b64decode(result['data']), folder_name="workflow-videos") \
                        if hasattr(cloudinary, 'upload_video') else None
                    if up and up.get('success') is not False:
                        video_url = up.get('secure_url') or up.get('url')

                if not video_url:
                    raise Exception("Video generation returned no URL")

                output[f"video_{idx}_url"] = video_url
                succeeded += 1
                logger.info(f"Video {idx} done: {video_url}")
            except Exception as e:
                logger.error(f"Video {idx} failed — skipping: {e}")
                _emsg = str(e).lower()
                if any(t in _emsg for t in ['timeout', 'connection', 'network', 'rate', 'busy', '503', '429']):
                    last_transient = e
                continue

        if succeeded == 0:
            if last_transient is not None:
                raise RetryableError(
                    f"All video generations failed (transient) — will retry: {last_transient}",
                    error_type='timeout', retry_count=0, model=model, provider=provider)
            raise HardError("All video generations failed")

        output["videos_generated"] = succeeded
        # keep a final image for ResultDisplay; surface first video too
        first_video = next((output.get(f'video_{i}_url') for i in range(1, 6) if output.get(f'video_{i}_url')), None)
        if first_video:
            output["video_url"] = first_video
        logger.info(f"Video generation complete: {succeeded}/5")
        return output
