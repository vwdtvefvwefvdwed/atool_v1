import logging
from typing import Dict, Any
from workflows.base_workflow import BaseWorkflow
from workflows.errors import RetryableError, HardError
from cloudinary_manager import get_cloudinary_manager
from workflows import notify_error, ErrorType

logger = logging.getLogger(__name__)

class KnightStyleImgToImgWorkflow(BaseWorkflow):
    async def step_upload(self, input_file: Any, step_config: Dict) -> Dict[str, Any]:
        try:
            logger.info("Processing image upload step")
            
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
        except Exception as e:
            logger.error(f"Failed to upload image: {e}")
            raise HardError(f"Failed to upload image: {e}")
    
    async def step_image_edit(self, input_data: Dict, step_config: Dict) -> Dict[str, Any]:
        from multi_endpoint_manager import get_endpoint_manager
        
        logger.info("Starting Knight style image edit step")
        endpoint_manager = get_endpoint_manager()
        
        model = step_config.get('model', step_config.get('default_model', 'nano-banana-pro-leonardo'))
        provider = step_config.get('provider', 'vision-leonardo')
        prompt = self.config['default_prompts']['image_edit']
        
        logger.info(f"Using model: {model}, provider: {provider}")
        logger.info(f"Prompt: {prompt[:100]}...")
        
        generation_params = {
            'prompt': prompt,
            'model': model,
            'provider_key': provider,
            'input_image_url': input_data['image_url'],
            'aspect_ratio': '1:1'
        }
        
        result = await endpoint_manager.generate_image(**generation_params)
        
        return {
            "edited_image_url": result.get('image_url') or result.get('url'),
            "model_used": model,
            "prompt": prompt,
            "original_image": input_data['image_url']
        }
    
    async def step_upscale(self, input_data: Dict, step_config: Dict) -> Dict[str, Any]:
        from multi_endpoint_manager import get_endpoint_manager
        
        logger.info("Starting upscale step with Stability AI")
        endpoint_manager = get_endpoint_manager()
        
        model = step_config.get('model', step_config.get('default_model', 'stability-upscale-fast'))
        provider = step_config.get('provider', 'vision-stabilityai')
        prompt = self.config['default_prompts']['upscale']
        
        logger.info(f"Using model: {model}, provider: {provider}")
        logger.info(f"Prompt: {prompt}")
        
        generation_params = {
            'prompt': prompt,
            'model': model,
            'provider_key': provider,
            'input_image_url': input_data['edited_image_url'],
            'aspect_ratio': '1:1'
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
