"""
NSFW Text Moderation Service
Uses RapidAPI NSFW Text Moderation API to check prompts for inappropriate content
"""

import os
import requests
from dotenv_vault import load_dotenv

load_dotenv()


class NSFWModerator:
    """Check text prompts for NSFW content using RapidAPI with key rotation"""
    
    def __init__(self):
        self.api_host = os.getenv("RAPIDAPI_NSFW_HOST")
        self.base_url = f"https://{self.api_host}"
        
        # Load all available API keys
        self.api_keys = []
        for i in range(1, 10):
            key_name = f"RAPIDAPI_NSFW_KEY_{i}" if i > 1 else "RAPIDAPI_NSFW_KEY"
            api_key = os.getenv(key_name)
            if api_key and api_key != "YOUR_BACKUP_KEY_HERE_1" and api_key != "YOUR_BACKUP_KEY_HERE_2" and api_key != "YOUR_BACKUP_KEY_HERE_3":
                self.api_keys.append({
                    "key": api_key,
                    "name": key_name,
                    "active": True
                })
        
        if not self.api_keys or not self.api_host:
            raise ValueError("At least RAPIDAPI_NSFW_KEY and RAPIDAPI_NSFW_HOST must be set in .env file")
        
        self.current_key_index = 0
        print(f"[NSFW MODERATOR] Initialized with {len(self.api_keys)} API key(s)")
        for i, key_info in enumerate(self.api_keys):
            masked_key = self._mask_key(key_info['key'])
            print(f"  {i+1}. {key_info['name']}: {masked_key}")
    
    @staticmethod
    def _mask_key(key, visible=4):
        """Mask API key for logging"""
        if not key or len(key) <= visible * 2:
            return "***"
        return f"{key[:visible]}...{key[-visible:]}"
    
    def _is_quota_error(self, status_code, response_text):
        """Check if error is quota/rate limit related"""
        if status_code == 429:
            return True
        if status_code == 401:
            return True
        
        error_keywords = [
            "quota", "rate limit", "exceeded", "too many requests",
            "subscription", "disabled for your subscription"
        ]
        response_lower = response_text.lower()
        return any(keyword in response_lower for keyword in error_keywords)
    
    def _try_api_call(self, text, api_key_info):
        """Try API call with specific key"""
        headers = {
            "x-rapidapi-key": api_key_info['key'],
            "x-rapidapi-host": self.api_host,
            "Content-Type": "application/json"
        }
        
        payload = {"text": text}
        api_url = f"{self.base_url}/moderation_check.php"
        
        response = requests.post(
            api_url,
            json=payload,
            headers=headers,
            timeout=10
        )
        
        return response
    
    def check_text(self, text):
        """
        Check if text contains NSFW content with automatic key rotation
        
        Args:
            text: The text/prompt to check
        
        Returns:
            dict: {
                "is_safe": bool,
                "is_nsfw": bool,
                "confidence": float,
                "categories": dict,
                "error": str (if failed)
            }
        """
        try:
            if not text or not text.strip():
                return {
                    "is_safe": True,
                    "is_nsfw": False,
                    "confidence": 1.0,
                    "categories": {}
                }
            
            print(f"[NSFW MODERATOR] Checking text: {text[:50]}...")
            
            # Try each API key until one succeeds
            last_error = None
            for attempt in range(len(self.api_keys)):
                current_key_info = self.api_keys[self.current_key_index]
                
                if not current_key_info['active']:
                    print(f"[NSFW MODERATOR] Key {current_key_info['name']} is inactive, skipping...")
                    self.current_key_index = (self.current_key_index + 1) % len(self.api_keys)
                    continue
                
                masked_key = self._mask_key(current_key_info['key'])
                print(f"[NSFW MODERATOR] Using key: {current_key_info['name']} ({masked_key})")
                
                try:
                    response = self._try_api_call(text, current_key_info)
                    print(f"[NSFW MODERATOR] API Response Status: {response.status_code}")
                    
                    if response.status_code == 200:
                        # Success! Parse and return result
                        result = response.json()
                        print(f"[NSFW MODERATOR] SUCCESS with {current_key_info['name']}")
                        return self._parse_response(result)
                    
                    # Check if quota/rate limit error
                    response_text = response.text
                    if self._is_quota_error(response.status_code, response_text):
                        print(f"[NSFW MODERATOR] QUOTA ERROR with {current_key_info['name']}")
                        print(f"  Status: {response.status_code}")
                        print(f"  Response: {response_text[:200]}")
                        
                        # Rotate to next key
                        self.current_key_index = (self.current_key_index + 1) % len(self.api_keys)
                        print(f"[NSFW MODERATOR] Rotating to next key...")
                        last_error = f"Quota exceeded: {response.status_code}"
                        continue
                    else:
                        # Other error, still try next key
                        print(f"[NSFW MODERATOR] ERROR with {current_key_info['name']}: {response.status_code}")
                        print(f"  Response: {response_text[:200]}")
                        last_error = f"API error: {response.status_code}"
                        self.current_key_index = (self.current_key_index + 1) % len(self.api_keys)
                        continue
                        
                except requests.RequestException as e:
                    print(f"[NSFW MODERATOR] Request error with {current_key_info['name']}: {e}")
                    last_error = f"Request error: {str(e)}"
                    self.current_key_index = (self.current_key_index + 1) % len(self.api_keys)
                    continue
            
            # All keys failed
            print(f"[NSFW MODERATOR ERROR] All API keys failed. Last error: {last_error}")
            return {
                "is_safe": True,  # Default to safe when all keys fail
                "is_nsfw": False,
                "confidence": 0.0,
                "categories": {},
                "error": f"All API keys exhausted. {last_error}"
            }
            
        except Exception as e:
            error_msg = f"Unexpected error: {str(e)}"
            print(f"[NSFW MODERATOR ERROR] {error_msg}")
            return {
                "is_safe": True,  # Default to safe on unexpected errors
                "is_nsfw": False,
                "confidence": 0.0,
                "categories": {},
                "error": error_msg
            }
    
    def _parse_response(self, result):
        """Parse API response and determine if content is NSFW"""
        # Parse response - check moderation_classes scores
        moderation_classes = result.get("moderation_classes", {})
        profanity_matches = result.get("profanity", {}).get("matches", [])
        
        # Extract scores (0-1 range)
        sexual_score = moderation_classes.get("sexual", 0)
        discriminatory_score = moderation_classes.get("discriminatory", 0)
        insulting_score = moderation_classes.get("insulting", 0)
        violent_score = moderation_classes.get("violent", 0)
        toxic_score = moderation_classes.get("toxic", 0)
        self_harm_score = moderation_classes.get("self-harm", 0)
        
        # Determine if NSFW (threshold: 0.3 or any profanity matches)
        max_score = max(sexual_score, discriminatory_score, insulting_score, 
                       violent_score, toxic_score, self_harm_score)
        has_profanity = len(profanity_matches) > 0
        
        is_nsfw = max_score >= 0.3 or has_profanity
        
        categories = {
            "sexual": sexual_score,
            "discriminatory": discriminatory_score,
            "insulting": insulting_score,
            "violent": violent_score,
            "toxic": toxic_score,
            "self_harm": self_harm_score,
            "profanity_count": len(profanity_matches)
        }
        
        return {
            "is_safe": not is_nsfw,
            "is_nsfw": is_nsfw,
            "confidence": max_score,
            "categories": categories,
            "profanity_matches": profanity_matches,
            "raw_response": result
        }


# Singleton instance
_moderator_instance = None

def get_moderator():
    """Get or create the NSFW moderator singleton"""
    global _moderator_instance
    if _moderator_instance is None:
        _moderator_instance = NSFWModerator()
    return _moderator_instance
