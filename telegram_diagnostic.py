"""
Telegram Bot Configuration Diagnostic Tool
Helps identify configuration issues with Telegram bot
"""

import os
import requests
from envvault import load_env
load_env()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
TELEGRAM_SECRET = os.getenv("TELEGRAM_SECRET")

def diagnose():
    """Run comprehensive Telegram diagnostic"""
    
    print("\n" + "="*70)
    print("🔍 TELEGRAM BOT DIAGNOSTIC")
    print("="*70)
    
    # 1. Check environment variables
    print("\n1️⃣ CHECKING ENVIRONMENT VARIABLES:")
    print(f"   Bot Token set: {'✅ YES' if TELEGRAM_BOT_TOKEN else '❌ NO'}")
    if TELEGRAM_BOT_TOKEN:
        print(f"      Token: {TELEGRAM_BOT_TOKEN[:30]}...{TELEGRAM_BOT_TOKEN[-10:]}")
    
    print(f"   Chat ID set: {'✅ YES' if TELEGRAM_CHAT_ID else '❌ NO'}")
    if TELEGRAM_CHAT_ID:
        print(f"      Chat ID: {TELEGRAM_CHAT_ID}")
    
    print(f"   Secret set: {'✅ YES' if TELEGRAM_SECRET else '❌ NO'}")
    if TELEGRAM_SECRET:
        print(f"      Secret: {TELEGRAM_SECRET[:10]}...")
    
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("\n❌ MISSING REQUIRED ENVIRONMENT VARIABLES")
        return False
    
    # 2. Test bot token validity
    print("\n2️⃣ TESTING BOT TOKEN:")
    try:
        me_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getMe"
        me_response = requests.get(me_url, timeout=10)
        me_data = me_response.json()
        
        if me_data.get("ok"):
            bot_info = me_data.get("result", {})
            print(f"   ✅ Bot Token is VALID")
            print(f"      Bot Username: @{bot_info.get('username')}")
            print(f"      Bot ID: {bot_info.get('id')}")
            print(f"      Bot Name: {bot_info.get('first_name')}")
        else:
            error = me_data.get('description', 'Unknown error')
            print(f"   ❌ Bot Token is INVALID")
            print(f"      Error: {error}")
            return False
    except Exception as e:
        print(f"   ❌ Cannot connect to Telegram API: {e}")
        return False
    
    # 3. Test getting updates (check if messages exist)
    print("\n3️⃣ TESTING MESSAGE RETRIEVAL:")
    try:
        updates_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
        params = {"offset": 0, "limit": 5, "timeout": 5}
        
        response = requests.get(updates_url, params=params, timeout=10)
        data = response.json()
        
        print(f"   API Response OK: {data.get('ok')}")
        print(f"   HTTP Status: {response.status_code}")
        
        if not data.get("ok"):
            error = data.get('description', 'Unknown error')
            print(f"   ❌ API Error: {error}")
            print(f"      This is the problem! The getUpdates call is failing.")
            print(f"      Check your bot token and internet connection.")
            return False
        
        updates = data.get("result", [])
        print(f"   ✅ Messages in queue: {len(updates)}")
        
        if updates:
            print(f"\n   📨 Recent messages:")
            for i, update in enumerate(updates[:3]):
                msg = update.get('message', {})
                text = msg.get('text', 'NO TEXT')[:80]
                chat_id = msg.get('chat', {}).get('id')
                print(f"      [{i+1}] Chat ID: {chat_id}, Text: {text}")
        else:
            print(f"   ⚠️ No messages in queue")
            print(f"      Either:")
            print(f"      - Monetag is not sending postbacks, OR")
            print(f"      - Messages are being sent to wrong chat, OR")
            print(f"      - Messages were already fetched")
    
    except Exception as e:
        print(f"   ❌ Cannot fetch updates: {e}")
        return False
    
    # 4. Test sending a test message to verify chat works
    print("\n4️⃣ TESTING MESSAGE SENDING (to verify chat):")
    try:
        send_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        params = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": "✅ Telegram diagnostic test - if you see this, the chat ID is correct!"
        }
        
        send_response = requests.get(send_url, params=params, timeout=10)
        send_data = send_response.json()
        
        if send_data.get("ok"):
            print(f"   ✅ Test message SENT successfully")
            print(f"      Chat ID {TELEGRAM_CHAT_ID} is VALID")
            print(f"      Message ID: {send_data.get('result', {}).get('message_id')}")
            print(f"\n      👉 Check your Telegram: did you receive a test message?")
        else:
            error = send_data.get('description', 'Unknown error')
            print(f"   ❌ Failed to send test message")
            print(f"      Error: {error}")
            print(f"      Chat ID {TELEGRAM_CHAT_ID} might be WRONG")
            return False
    
    except Exception as e:
        print(f"   ❌ Cannot send test message: {e}")
        return False
    
    # 5. Check postback URL format
    print("\n5️⃣ POSTBACK URL FOR MONETAG:")
    base_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    params = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": "SOURCE:{telegram_id}|ZONE:{zone_id}|REWARD:{reward_event_type}|PRICE:{estimated_price}|YMID:{ymid}|SEC:" + TELEGRAM_SECRET
    }
    from urllib.parse import urlencode
    postback_url = base_url + "?" + urlencode(params)
    
    print(f"   URL: {postback_url[:100]}...")
    print(f"\n   ⚠️ IMPORTANT:")
    print(f"   1. Copy this URL")
    print(f"   2. Go to Monetag Dashboard")
    print(f"   3. Settings → Postback URL")
    print(f"   4. Paste the COMPLETE URL (all of it!)")
    print(f"   5. Save and TEST in Monetag dashboard")
    
    print("\n" + "="*70)
    print("✅ DIAGNOSTIC COMPLETE")
    print("="*70)
    print("\nSUMMARY:")
    print("- If you see this, your Telegram configuration is likely CORRECT")
    print("- Check Monetag dashboard to ensure postback URL is configured")
    print("- Make sure postback is ENABLED in Monetag")
    print("- Test the postback from Monetag dashboard")
    print("="*70 + "\n")
    
    return True

if __name__ == "__main__":
    diagnose()
