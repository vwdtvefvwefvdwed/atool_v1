import os
import time
import secrets
import math
import jwt
from datetime import datetime, timedelta
from typing import Dict, Optional, Tuple
from collections import defaultdict

CAPTCHA_SECRET = os.getenv('CAPTCHA_SECRET', 'your-super-secret-captcha-key-change-in-production')

class SliderCaptchaManager:
    def __init__(self, ttl_seconds: int = 120, max_attempts: int = 3, max_failures: int = 5, cooldown_seconds: int = 180):
        self.ttl_seconds = ttl_seconds
        self.max_attempts = max_attempts
        self.max_failures = max_failures
        self.cooldown_seconds = cooldown_seconds
        self.challenges: Dict[str, dict] = {}
        self.ip_attempts: Dict[str, list] = defaultdict(list)
        self.ip_failures: Dict[str, dict] = defaultdict(lambda: {'count': 0, 'cooldown_until': 0})
        self.cleanup_interval = 300
        self.last_cleanup = time.time()
        
    def _cleanup_expired(self):
        current_time = time.time()
        if current_time - self.last_cleanup < self.cleanup_interval:
            return
            
        self.last_cleanup = current_time
        expired_keys = []
        
        for challenge_id, data in self.challenges.items():
            if current_time - data['created_at'] > self.ttl_seconds:
                expired_keys.append(challenge_id)
        
        for key in expired_keys:
            del self.challenges[key]
            
        for ip, attempts in list(self.ip_attempts.items()):
            self.ip_attempts[ip] = [t for t in attempts if current_time - t < 3600]
            if not self.ip_attempts[ip]:
                del self.ip_attempts[ip]
        
        # Cleanup expired cooldowns
        for ip in list(self.ip_failures.keys()):
            if current_time > self.ip_failures[ip]['cooldown_until']:
                self.ip_failures[ip]['count'] = 0
                self.ip_failures[ip]['cooldown_until'] = 0
    
    def _check_cooldown(self, client_ip: str) -> Optional[dict]:
        """Check if IP is in cooldown period. Returns error dict if in cooldown, None otherwise."""
        if not client_ip:
            return None
        
        current_time = time.time()
        failure_data = self.ip_failures[client_ip]
        
        if current_time < failure_data['cooldown_until']:
            wait_time = int(failure_data['cooldown_until'] - current_time)
            minutes = wait_time // 60
            seconds = wait_time % 60
            
            if minutes > 0:
                time_str = f"{minutes} minute{'s' if minutes > 1 else ''} {seconds} second{'s' if seconds != 1 else ''}"
            else:
                time_str = f"{seconds} second{'s' if seconds != 1 else ''}"
            
            return {
                'success': False,
                'error': f'Too many failed attempts. Please wait {time_str} before trying again.',
                'cooldown': True,
                'wait_seconds': wait_time
            }
        
        return None
    
    def generate_challenge(self, client_ip: str = None) -> Tuple[str, dict]:
        self._cleanup_expired()
        
        # Check if IP is in cooldown
        cooldown_error = self._check_cooldown(client_ip)
        if cooldown_error:
            raise Exception(cooldown_error['error'])
        
        if client_ip:
            recent_attempts = [t for t in self.ip_attempts[client_ip] if time.time() - t < 60]
            if len(recent_attempts) > 10:
                raise Exception("Rate limit exceeded")
        
        challenge_id = secrets.token_urlsafe(32)
        correct_x = secrets.randbelow(140) + 80
        correct_y = secrets.randbelow(80) + 20
        image_seed = secrets.token_hex(8)
        
        self.challenges[challenge_id] = {
            'correct_x': correct_x,
            'correct_y': correct_y,
            'image_seed': image_seed,
            'created_at': time.time(),
            'attempts': 0,
            'failed': False,
            'client_ip': client_ip
        }
        
        return challenge_id, {
            'challenge_id': challenge_id,
            'image_seed': image_seed,
            'correct_x': correct_x,
            'correct_y': correct_y
        }
    
    def _analyze_movement(self, movements: list) -> dict:
        if not movements or len(movements) < 3:
            return {'valid': False, 'reason': 'Insufficient movement data'}
        
        y_deviations = []
        speeds = []
        
        for i in range(1, len(movements)):
            y_deviations.append(abs(movements[i]['y'] - movements[i - 1]['y']))
            
            dx = movements[i]['x'] - movements[i - 1]['x']
            dt = max(movements[i]['t'] - movements[i - 1]['t'], 1)
            speeds.append(abs(dx / dt))
        
        total_y_deviation = sum(y_deviations)
        if total_y_deviation < 3:
            return {'valid': False, 'reason': 'Movement too straight (bot-like)'}
        
        if len(speeds) > 1:
            mean_speed = sum(speeds) / len(speeds)
            variance = sum((s - mean_speed) ** 2 for s in speeds) / len(speeds)
            
            if variance < 0.01:
                return {'valid': False, 'reason': 'Constant velocity (bot-like)'}
        
        return {'valid': True}
    
    def verify_challenge(
        self,
        challenge_id: str,
        final_x: int,
        movements: list,
        duration: int,
        client_ip: str = None
    ) -> dict:
        self._cleanup_expired()
        
        # Check cooldown first
        cooldown_error = self._check_cooldown(client_ip)
        if cooldown_error:
            return cooldown_error
        
        if client_ip:
            self.ip_attempts[client_ip].append(time.time())
        
        if challenge_id not in self.challenges:
            return {'success': False, 'error': 'Challenge expired or invalid'}
        
        challenge = self.challenges[challenge_id]
        
        if challenge['failed']:
            return {'success': False, 'error': 'Challenge already failed'}
        
        if challenge['attempts'] >= self.max_attempts:
            challenge['failed'] = True
            return {'success': False, 'error': 'Max attempts exceeded'}
        
        challenge['attempts'] += 1
        
        if time.time() - challenge['created_at'] > self.ttl_seconds:
            del self.challenges[challenge_id]
            return {'success': False, 'error': 'Challenge expired'}
        
        if duration < 600 or duration > 15000:
            challenge['failed'] = True
            self._record_failure(client_ip)
            return {'success': False, 'error': 'Invalid completion time'}
        
        correct_x = challenge['correct_x']
        position_tolerance = 5
        
        print(f"🎯 Position check: final_x={final_x}, correct_x={correct_x}, diff={abs(final_x - correct_x)}, tolerance={position_tolerance}")
        
        if abs(final_x - correct_x) > position_tolerance:
            if challenge['attempts'] >= self.max_attempts:
                challenge['failed'] = True
            self._record_failure(client_ip)
            return {'success': False, 'error': f'Incorrect position (off by {abs(final_x - correct_x)}px)'}
        
        movement_analysis = self._analyze_movement(movements)
        if not movement_analysis['valid']:
            challenge['failed'] = True
            self._record_failure(client_ip)
            return {
                'success': False,
                'error': f"Bot behavior detected: {movement_analysis['reason']}"
            }
        
        # Success - reset failure count for this IP
        if client_ip:
            self.ip_failures[client_ip]['count'] = 0
            self.ip_failures[client_ip]['cooldown_until'] = 0
        
        del self.challenges[challenge_id]
        
        token = jwt.encode(
            {
                'captcha': True,
                'ts': datetime.utcnow().isoformat(),
                'exp': datetime.utcnow() + timedelta(minutes=5)
            },
            CAPTCHA_SECRET,
            algorithm='HS256'
        )
        
        return {
            'success': True,
            'token': token,
            'verified_at': datetime.utcnow().isoformat()
        }
    
    def _record_failure(self, client_ip: str):
        """Record a failed attempt and impose cooldown if needed."""
        if not client_ip:
            return
        
        self.ip_failures[client_ip]['count'] += 1
        failure_count = self.ip_failures[client_ip]['count']
        
        print(f"⚠️ Failure #{failure_count} for IP {client_ip}")
        
        if failure_count >= self.max_failures:
            self.ip_failures[client_ip]['cooldown_until'] = time.time() + self.cooldown_seconds
            print(f"🚫 IP {client_ip} in cooldown for {self.cooldown_seconds} seconds")

captcha_manager = SliderCaptchaManager()

def get_captcha_manager() -> SliderCaptchaManager:
    return captcha_manager
