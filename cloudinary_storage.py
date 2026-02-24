"""
Cloudinary Cloud Storage Integration
Handles uploading images to Cloudinary and generating public URLs
"""

import os
import tempfile
import re
from pathlib import Path
from urllib.parse import quote
from dotenv_vault import load_dotenv
import cloudinary
import cloudinary.uploader
import cloudinary.api

load_dotenv()

def sanitize_for_cloudinary(text):
    """
    Sanitize text for Cloudinary context field by removing emojis and problematic unicode
    while keeping readable text in multiple languages
    """
    if not text:
        return text
    
    # Remove emojis and emoticons (keep regular unicode letters/numbers)
    # This regex removes emoji characters while preserving text in various languages
    emoji_pattern = re.compile(
        "["
        "\U0001F600-\U0001F64F"  # emoticons
        "\U0001F300-\U0001F5FF"  # symbols & pictographs
        "\U0001F680-\U0001F6FF"  # transport & map symbols
        "\U0001F1E0-\U0001F1FF"  # flags (iOS)
        "\U00002500-\U00002BEF"  # chinese char
        "\U00002702-\U000027B0"
        "\U000024C2-\U0001F251"
        "\U0001f926-\U0001f937"
        "\U00010000-\U0010ffff"
        "\u2640-\u2642" 
        "\u2600-\u2B55"
        "\u200d"
        "\u23cf"
        "\u23e9"
        "\u231a"
        "\ufe0f"  # dingbats
        "\u3030"
        "]+",
        flags=re.UNICODE
    )
    
    sanitized = emoji_pattern.sub('', text)
    
    # Replace multiple spaces with single space
    sanitized = re.sub(r'\s+', ' ', sanitized)
    
    # Strip leading/trailing whitespace
    sanitized = sanitized.strip()
    
    return sanitized

class CloudinaryStorage:
    """Cloudinary cloud storage handler for image uploads"""
    
    def __init__(self):
        self.cloud_name = os.getenv("CLOUDINARY_CLOUD_NAME")
        self.api_key = os.getenv("CLOUDINARY_API_KEY")
        self.api_secret = os.getenv("CLOUDINARY_API_SECRET")
        self._configured = False
    
    def configure(self):
        """Configure Cloudinary with credentials from environment"""
        if self._configured:
            return True
        
        if not self.cloud_name or not self.api_key or not self.api_secret:
            raise ValueError("CLOUDINARY_CLOUD_NAME, CLOUDINARY_API_KEY, and CLOUDINARY_API_SECRET must be set in .env file")
        
        try:
            print("[CLOUDINARY] Configuring Cloudinary...")
            cloudinary.config(
                cloud_name=self.cloud_name,
                api_key=self.api_key,
                api_secret=self.api_secret,
                secure=True
            )
            self._configured = True
            print(f"[CLOUDINARY] Configuration successful! Cloud: {self.cloud_name}")
            return True
        except Exception as e:
            print(f"[CLOUDINARY ERROR] Configuration failed: {e}")
            raise Exception(f"Failed to configure Cloudinary: {str(e)}")
    
    def upload_image(self, image_path, folder_name="ai-generated-images", metadata=None):
        """
        Upload an image to Cloudinary and return the public URL
        
        Args:
            image_path: Path to the image file to upload
            folder_name: Folder name in Cloudinary to upload to (default: "ai-generated-images")
            metadata: Optional dict with context metadata (prompt, model, aspect_ratio, etc.)
        
        Returns:
            dict: {
                "success": bool,
                "public_url": str,
                "secure_url": str,
                "file_name": str,
                "public_id": str,
                "error": str (if failed)
            }
        """
        try:
            # Ensure configured
            if not self._configured:
                self.configure()
            
            if not os.path.exists(image_path):
                return {
                    "success": False,
                    "error": f"Image file not found: {image_path}"
                }
            
            file_name = os.path.basename(image_path)
            print(f"[CLOUDINARY] Uploading {file_name} to Cloudinary...")
            
            # Build upload parameters
            upload_params = {
                "folder": folder_name,
                "resource_type": "image",
                "overwrite": False,
                "unique_filename": True
            }
            
            # Add context metadata if provided
            if metadata:
                print(f"[CLOUDINARY] Received metadata: {metadata}")
                
                # Sanitize prompt to remove emojis and problematic unicode
                if 'prompt' in metadata:
                    original_prompt = metadata['prompt']
                    metadata['prompt'] = sanitize_for_cloudinary(original_prompt)
                    if len(metadata['prompt']) != len(original_prompt):
                        print(f"[CLOUDINARY] Sanitized prompt (removed emojis/special chars)")
                    
                    # Truncate prompt if too long (Cloudinary context field limit is ~1024 chars per value)
                    if len(metadata['prompt']) > 1000:
                        metadata['prompt'] = metadata['prompt'][:997] + "..."
                        print(f"[CLOUDINARY] Warning: Prompt truncated to 1000 characters")
                
                # Format: key1=value1|key2=value2
                # Filter out None and empty string values
                context_pairs = []
                for k, v in metadata.items():
                    if v:
                        # Escape pipe and equals characters in values
                        safe_value = str(v).replace('|', '_').replace('=', '-')
                        context_pairs.append(f"{k}={safe_value}")
                
                if context_pairs:
                    context_str = "|".join(context_pairs)
                    # Additional safety check: ensure total context string is under Cloudinary's limit
                    if len(context_str) > 2000:
                        print(f"[CLOUDINARY] Warning: Context string too long ({len(context_str)} chars), truncating...")
                        context_str = context_str[:1997] + "..."
                    upload_params["context"] = context_str
                    print(f"[CLOUDINARY] Context string: {context_str[:100]}...")
                else:
                    print(f"[CLOUDINARY] Warning: All metadata values were empty, no context added")
            
            # Upload to Cloudinary
            upload_result = cloudinary.uploader.upload(image_path, **upload_params)
            
            print(f"[CLOUDINARY] Upload successful!")
            print(f"[CLOUDINARY] Public URL: {upload_result['secure_url']}")
            print(f"[CLOUDINARY] Public ID: {upload_result['public_id']}")
            
            return {
                "success": True,
                "public_url": upload_result['url'],
                "secure_url": upload_result['secure_url'],
                "file_name": file_name,
                "public_id": upload_result['public_id'],
                "width": upload_result.get('width'),
                "height": upload_result.get('height'),
                "format": upload_result.get('format')
            }
            
        except Exception as e:
            print(f"[CLOUDINARY ERROR] Upload failed: {e}")
            import traceback
            traceback.print_exc()
            return {
                "success": False,
                "error": str(e)
            }
    
    def upload_image_from_bytes(self, image_bytes, file_name, folder_name="ai-generated-images", metadata=None):
        """
        Upload an image from bytes to Cloudinary and return the public URL
        
        Args:
            image_bytes: Image data as bytes
            file_name: Name for the uploaded file
            folder_name: Folder name in Cloudinary to upload to
            metadata: Optional dict with context metadata (prompt, model, aspect_ratio, etc.)
        
        Returns:
            dict: Same as upload_image()
        """
        tmp_path = None
        try:
            try:
                with tempfile.NamedTemporaryFile(delete=False, suffix=Path(file_name).suffix) as tmp_file:
                    tmp_file.write(image_bytes)
                    tmp_path = tmp_file.name
            except OSError as e:
                print(f"[CLOUDINARY ERROR] Failed to write temp file for {file_name}: {e}")
                return {
                    "success": False,
                    "error": f"Failed to write temporary file: {str(e)}"
                }

            result = self.upload_image(tmp_path, folder_name, metadata=metadata)
            return result

        finally:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
    
    def upload_video(self, video_path, job_id=None, folder_name="ai-generated-videos", metadata=None):
        """
        Upload a video to Cloudinary and return the public URL
        
        Args:
            video_path: Path to the video file to upload
            job_id: Job ID for naming (optional)
            folder_name: Folder name in Cloudinary to upload to (default: "ai-generated-videos")
            metadata: Optional dict with context metadata (prompt, model, etc.)
        
        Returns:
            str: Secure URL of the uploaded video
        """
        try:
            # Ensure configured
            if not self._configured:
                self.configure()
            
            if not os.path.exists(video_path):
                raise Exception(f"Video file not found: {video_path}")
            
            file_name = os.path.basename(video_path)
            print(f"[CLOUDINARY] Uploading video {file_name} to Cloudinary...")
            
            # Build upload parameters
            upload_params = {
                "folder": folder_name,
                "resource_type": "video",
                "overwrite": False,
                "unique_filename": True
            }
            
            # Add public_id if job_id provided
            if job_id:
                upload_params["public_id"] = f"{folder_name}/video_{job_id}"
            
            # Add context metadata if provided
            if metadata:
                print(f"[CLOUDINARY] Received video metadata: {metadata}")
                
                # Sanitize prompt to remove emojis and problematic unicode
                if 'prompt' in metadata:
                    original_prompt = metadata['prompt']
                    metadata['prompt'] = sanitize_for_cloudinary(original_prompt)
                    if len(metadata['prompt']) != len(original_prompt):
                        print(f"[CLOUDINARY] Sanitized prompt (removed emojis/special chars)")
                    
                    # Truncate prompt if too long (Cloudinary context field limit is ~1024 chars per value)
                    if len(metadata['prompt']) > 1000:
                        metadata['prompt'] = metadata['prompt'][:997] + "..."
                        print(f"[CLOUDINARY] Warning: Prompt truncated to 1000 characters")
                
                # Filter out None and empty string values
                context_pairs = []
                for k, v in metadata.items():
                    if v:
                        # Escape pipe and equals characters in values
                        safe_value = str(v).replace('|', '_').replace('=', '-')
                        context_pairs.append(f"{k}={safe_value}")
                
                if context_pairs:
                    context_str = "|".join(context_pairs)
                    # Additional safety check: ensure total context string is under Cloudinary's limit
                    if len(context_str) > 2000:
                        print(f"[CLOUDINARY] Warning: Context string too long ({len(context_str)} chars), truncating...")
                        context_str = context_str[:1997] + "..."
                    upload_params["context"] = context_str
                    print(f"[CLOUDINARY] Context string: {context_str[:100]}...")
            
            # Upload to Cloudinary
            upload_result = cloudinary.uploader.upload(video_path, **upload_params)
            
            print(f"[CLOUDINARY] Video upload successful!")
            print(f"[CLOUDINARY] Secure URL: {upload_result['secure_url']}")
            print(f"[CLOUDINARY] Public ID: {upload_result['public_id']}")
            print(f"[CLOUDINARY] Duration: {upload_result.get('duration', 'N/A')}s")
            
            return upload_result['secure_url']
            
        except Exception as e:
            print(f"[CLOUDINARY ERROR] Video upload failed: {e}")
            import traceback
            traceback.print_exc()
            raise Exception(f"Failed to upload video to Cloudinary: {str(e)}")
    
    def delete_image(self, public_id):
        """
        Delete an image from Cloudinary
        
        Args:
            public_id: The public_id of the image to delete
        
        Returns:
            dict: {"success": bool, "result": str}
        """
        try:
            if not self._configured:
                self.configure()
            
            print(f"[CLOUDINARY] Deleting image: {public_id}")
            result = cloudinary.uploader.destroy(public_id)
            
            print(f"[CLOUDINARY] Delete result: {result}")
            return {
                "success": result.get('result') == 'ok',
                "result": result.get('result')
            }
        except Exception as e:
            print(f"[CLOUDINARY ERROR] Delete failed: {e}")
            return {
                "success": False,
                "error": str(e)
            }


# Global instance
_cloudinary_storage = None

def get_cloudinary_storage():
    """Get or create the global CloudinaryStorage instance"""
    global _cloudinary_storage
    if _cloudinary_storage is None:
        _cloudinary_storage = CloudinaryStorage()
    return _cloudinary_storage
