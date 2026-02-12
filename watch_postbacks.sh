#!/bin/bash

# Real-time log watcher for Monetag postbacks
# Run this to see postbacks arrive in real-time

echo "=========================================="
echo "MONETAG POSTBACK LOG MONITOR"
echo "=========================================="
echo ""
echo "⏳ Watching for postbacks..."
echo "Complete an ad in your app to trigger postback"
echo ""
echo "Look for these messages:"
echo "  💰 MONETAG POSTBACK RECEIVED"
echo "  ✅ POSTBACK ACCEPTED & PROCESSED"
echo ""
echo "Press Ctrl+C to stop"
echo "=========================================="
echo ""

# Start backend and capture logs
cd /workspaces/251459456/Atool/backend
python app.py 2>&1 | grep -E "(MONETAG|postback|click_id|POSTBACK|Session|Verified)" --line-buffered
