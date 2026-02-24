"""
NSFW Text Moderation Service
Uses RapidAPI NSFW Text Moderation API to check prompts for inappropriate content.
API keys are loaded from the 'vision-rapidapi' provider in Worker1 Supabase.
Falls back to .env keys if the database is unavailable.
"""

import os
import requests
from dotenv_vault import load_dotenv

load_dotenv()

PROVIDER_NAME = "vision-rapidapi"


class NSFWModerator:
    """Check text prompts for NSFW content using RapidAPI with key rotation"""

    def __init__(self):
        self.api_host = os.getenv("RAPIDAPI_NSFW_HOST")
        self.base_url = f"https://{self.api_host}"
        self.api_keys = []

        self._load_keys_from_db()

        if not self.api_keys:
            print(f"[NSFW MODERATOR] DB keys unavailable, falling back to .env keys")
            self._load_keys_from_env()

        if not self.api_host:
            raise ValueError(
                "RAPIDAPI_NSFW_HOST is not set in .env — this is required regardless of key source"
            )

        if not self.api_keys:
            raise ValueError(
                "No NSFW API keys found. Add keys to the 'vision-rapidapi' provider in Worker1 "
                "or set RAPIDAPI_NSFW_KEY in .env"
            )

        self.current_key_index = 0
        print(f"[NSFW MODERATOR] Initialized with {len(self.api_keys)} API key(s)")
        for i, key_info in enumerate(self.api_keys):
            masked_key = self._mask_key(key_info["key"])
            source = key_info.get("source", "env")
            print(f"  {i+1}. {key_info['name']} [{source}]: {masked_key}")

    def _load_keys_from_db(self):
        """Load all keys for vision-rapidapi provider from Worker1 Supabase"""
        try:
            from provider_api_keys import get_all_api_keys_for_provider
            db_keys = get_all_api_keys_for_provider(PROVIDER_NAME)
            if db_keys:
                for record in db_keys:
                    api_key = record.get("api_key", "").strip()
                    if api_key:
                        self.api_keys.append({
                            "key": api_key,
                            "name": f"key_#{record.get('key_number', record.get('id', '?'))}",
                            "db_id": record.get("id"),
                            "active": True,
                            "source": "db"
                        })
                print(f"[NSFW MODERATOR] Loaded {len(self.api_keys)} key(s) from DB provider '{PROVIDER_NAME}'")
        except Exception as e:
            print(f"[NSFW MODERATOR] Could not load keys from DB: {e}")

    def _load_keys_from_env(self):
        """Load keys from .env as fallback"""
        for i in range(1, 10):
            key_name = f"RAPIDAPI_NSFW_KEY_{i}" if i > 1 else "RAPIDAPI_NSFW_KEY"
            api_key = os.getenv(key_name, "").strip()
            if api_key and api_key not in ("YOUR_BACKUP_KEY_HERE_1", "YOUR_BACKUP_KEY_HERE_2", "YOUR_BACKUP_KEY_HERE_3"):
                self.api_keys.append({
                    "key": api_key,
                    "name": key_name,
                    "db_id": None,
                    "active": True,
                    "source": "env"
                })

    def _remove_key(self, key_info, error_message):
        """
        Remove a failed key from the active list.
        If the key came from DB, delete it from Supabase (archived to deleted_api_keys first).
        """
        db_id = key_info.get("db_id")
        if db_id:
            try:
                from provider_api_keys import delete_api_key
                deleted = delete_api_key(db_id, error_message)
                if deleted:
                    print(f"[NSFW MODERATOR] Key '{key_info['name']}' deleted from DB (id={db_id})")
                else:
                    print(f"[NSFW MODERATOR] Failed to delete key '{key_info['name']}' from DB")
            except Exception as e:
                print(f"[NSFW MODERATOR] Error deleting key from DB: {e}")

        key_info["active"] = False

    @staticmethod
    def _mask_key(key, visible=4):
        if not key or len(key) <= visible * 2:
            return "***"
        return f"{key[:visible]}...{key[-visible:]}"

    def _is_quota_error(self, status_code, response_text):
        if status_code in (429, 401):
            return True
        error_keywords = [
            "quota", "rate limit", "exceeded", "too many requests",
            "subscription", "disabled for your subscription", "monthly limit",
            "limit reached", "you are not subscribed", "blocked"
        ]
        return any(keyword in response_text.lower() for keyword in error_keywords)

    def _try_api_call(self, text, api_key_info):
        headers = {
            "x-rapidapi-key": api_key_info["key"],
            "x-rapidapi-host": self.api_host,
            "Content-Type": "application/json"
        }
        response = requests.post(
            f"{self.base_url}/moderation_check.php",
            json={"text": text},
            headers=headers,
            timeout=10
        )
        return response

    def check_text(self, text):
        """
        Check if text contains NSFW content with automatic key rotation.
        On quota/limit errors, the exhausted key is deleted from Supabase and
        the next available key is tried automatically.

        Returns:
            dict: {
                "is_safe": bool,
                "is_nsfw": bool,
                "confidence": float,
                "categories": dict,
                "error": str  (only present on failure)
            }
        """
        try:
            if not text or not text.strip():
                return {"is_safe": True, "is_nsfw": False, "confidence": 1.0, "categories": {}}

            print(f"[NSFW MODERATOR] Checking text: {text[:50]}...")

            active_keys = [k for k in self.api_keys if k["active"]]
            if not active_keys:
                print("[NSFW MODERATOR ERROR] No active API keys available")
                return {
                    "is_safe": True,
                    "is_nsfw": False,
                    "confidence": 0.0,
                    "categories": {},
                    "error": "No active API keys available"
                }

            last_error = None

            for attempt in range(len(active_keys)):
                active_keys = [k for k in self.api_keys if k["active"]]
                if not active_keys:
                    break

                self.current_key_index = self.current_key_index % len(active_keys)
                current_key_info = active_keys[self.current_key_index]

                masked_key = self._mask_key(current_key_info["key"])
                print(f"[NSFW MODERATOR] Using key: {current_key_info['name']} ({masked_key}) [{current_key_info.get('source', 'env')}]")

                try:
                    response = self._try_api_call(text, current_key_info)
                    print(f"[NSFW MODERATOR] API Response Status: {response.status_code}")

                    if response.status_code == 200:
                        result = response.json()
                        print(f"[NSFW MODERATOR] SUCCESS with {current_key_info['name']}")
                        return self._parse_response(result)

                    response_text = response.text
                    if self._is_quota_error(response.status_code, response_text):
                        error_msg = f"Quota/limit error: HTTP {response.status_code} - {response_text[:200]}"
                        print(f"[NSFW MODERATOR] QUOTA ERROR with {current_key_info['name']}")
                        print(f"  Status: {response.status_code}")
                        print(f"  Response: {response_text[:200]}")

                        self._remove_key(current_key_info, error_msg)
                        last_error = error_msg
                        self.current_key_index = 0
                        continue
                    else:
                        error_msg = f"API error: HTTP {response.status_code} - {response_text[:200]}"
                        print(f"[NSFW MODERATOR] ERROR with {current_key_info['name']}: {response.status_code}")
                        print(f"  Response: {response_text[:200]}")
                        last_error = error_msg
                        self.current_key_index = (self.current_key_index + 1) % max(len(active_keys), 1)
                        continue

                except requests.RequestException as e:
                    last_error = f"Request error: {str(e)}"
                    print(f"[NSFW MODERATOR] Request error with {current_key_info['name']}: {e}")
                    self.current_key_index = (self.current_key_index + 1) % max(len(active_keys), 1)
                    continue

            print(f"[NSFW MODERATOR ERROR] All API keys exhausted. Last error: {last_error}")
            return {
                "is_safe": True,
                "is_nsfw": False,
                "confidence": 0.0,
                "categories": {},
                "error": f"All API keys exhausted. {last_error}"
            }

        except Exception as e:
            error_msg = f"Unexpected error: {str(e)}"
            print(f"[NSFW MODERATOR ERROR] {error_msg}")
            return {
                "is_safe": True,
                "is_nsfw": False,
                "confidence": 0.0,
                "categories": {},
                "error": error_msg
            }

    def _parse_response(self, result):
        moderation_classes = result.get("moderation_classes", {})
        profanity_matches = result.get("profanity", {}).get("matches", [])

        sexual_score = moderation_classes.get("sexual", 0)
        discriminatory_score = moderation_classes.get("discriminatory", 0)
        insulting_score = moderation_classes.get("insulting", 0)
        violent_score = moderation_classes.get("violent", 0)
        toxic_score = moderation_classes.get("toxic", 0)
        self_harm_score = moderation_classes.get("self-harm", 0)

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


_moderator_instance = None


def get_moderator():
    """Get or create the NSFW moderator singleton"""
    global _moderator_instance
    if _moderator_instance is None:
        _moderator_instance = NSFWModerator()
    return _moderator_instance
