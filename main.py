#!/usr/bin/env python3
"""
Main entry point for Atool backend deployment
Starts both Flask API and Job Worker Realtime in parallel

Direct Monetag Postback Flow:
    1. Monetag sends postback to: http://217.154.114.227:10148/api/monetag/postback
    2. Backend validates and stores in Supabase
    3. Job Worker processes ad completions
    4. No Telegram bot involved - direct server-to-server communication
"""

import os
import sys
import threading
import signal
import time
from envvault import load_env

# Load environment variables
load_env()
# Import Flask app
from app import app

# Import worker function
from job_worker_realtime import start_realtime

# Import ngrok for public URL tunneling
try:
    from pyngrok import ngrok
    NGROK_AVAILABLE = True
except ImportError:
    NGROK_AVAILABLE = False

# Global flags for graceful shutdown
shutdown_event = threading.Event()


def run_flask_app():
    """Run Flask API server"""
    print("\n" + "="*60)
    print("🚀 STARTING FLASK API SERVER")
    print("="*60)
    port = int(os.getenv("FLASK_PORT", "5000"))
    host = os.getenv("FLASK_HOST", "0.0.0.0")
    debug = os.getenv("FLASK_DEBUG", "False").lower() == "true"
    
    # Start ngrok tunnel if available
    ngrok_url = None
    if NGROK_AVAILABLE:
        ngrok_token = os.getenv("NGROK_AUTH_TOKEN")
        if ngrok_token:
            try:
                print("\n🔗 Starting ngrok tunnel...")
                # Set ngrok auth token
                ngrok.set_auth_token(ngrok_token)
                # Start tunnel pointing to local Flask server
                public_url = ngrok.connect(port, "http")
                ngrok_url = public_url.public_url
            except Exception as e:
                print(f"⚠️  Ngrok tunnel failed to start: {e}")
                print("   Continuing with localhost only...")
    
    # Display server URLs prominently
    print("\n" + "="*60)
    print("🌐 SERVER URLs")
    print("="*60)
    print(f"📍 Local Server: http://localhost:{port}")
    if ngrok_url:
        print(f"🌐 Public URL: {ngrok_url}")
        print("="*60)
    else:
        print("⚠️  Ngrok tunnel not available")
        print("="*60)
    
    try:
        app.run(host=host, port=port, debug=debug, use_reloader=False)
    except Exception as e:
        print(f"❌ Flask app error: {e}")
        shutdown_event.set()


def run_job_worker():
    """Run Job Worker Realtime in separate thread"""
    print("\n" + "="*60)
    print("🤖 STARTING JOB WORKER REALTIME")
    print("="*60)
    
    try:
        # Worker runs in blocking loop, so it will continue until interrupted
        start_realtime()
    except KeyboardInterrupt:
        print("\n⏹️  Job worker interrupted")
        shutdown_event.set()
    except Exception as e:
        print(f"❌ Job worker error: {e}")
        shutdown_event.set()


def signal_handler(signum, frame):
    """Handle shutdown signals"""
    print("\n\n" + "="*60)
    print("⏹️  SHUTDOWN SIGNAL RECEIVED")
    print("="*60)
    shutdown_event.set()
    sys.exit(0)


def main():
    """Start both services"""
    print("\n" + "="*60)
    print("🎯 ATOOL BACKEND STARTUP")
    print("="*60)
    print(f"Environment: {os.getenv('ENVIRONMENT', 'development')}")
    print(f"Backend URL: {os.getenv('BACKEND_URL', 'http://localhost:5000')}")
    print("="*60 + "\n")
    
    # Register signal handlers for graceful shutdown
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Start Flask app in main thread
    flask_thread = threading.Thread(target=run_flask_app, daemon=False)
    flask_thread.start()
    
    # Give Flask a moment to start
    time.sleep(2)
    
    # Start Job Worker in separate thread
    worker_thread = threading.Thread(target=run_job_worker, daemon=False)
    worker_thread.start()
    
    print("\n" + "="*60)
    print("✅ BOTH SERVICES STARTED SUCCESSFULLY")
    print("="*60)
    print("📊 Flask API running")
    print("🤖 Job Worker running")
    print("="*60 + "\n")
    
    # Wait for both threads
    try:
        flask_thread.join()
        worker_thread.join()
    except KeyboardInterrupt:
        signal_handler(None, None)


if __name__ == "__main__":
    main()
