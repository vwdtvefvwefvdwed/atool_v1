#!/bin/bash

# Test Monetag Postback Integration
# This script tests if the postback endpoint receives data correctly

echo "=========================================="
echo "MONETAG POSTBACK TEST"
echo "=========================================="
echo ""

POSTBACK_URL="https://friendly-potato-g4jv5rrr69953p9xv-5000.app.github.dev/api/monetag/postback"
TEST_CLICK_ID="test_$(date +%s)_$(shuf -i 1000-9999 -n 1)"

echo "Timestamp: $(date)"
echo "Test Click ID: $TEST_CLICK_ID"
echo "Postback URL: $POSTBACK_URL"
echo ""

# Test 1: With valued (completed) ad
echo "Test 1: Sending COMPLETED ad postback..."
echo "---"
curl -s -X POST "$POSTBACK_URL?ymid=$TEST_CLICK_ID&estimated_price=2.50&reward_event_type=valued" \
  -H "Content-Type: application/json" | jq .

echo ""
echo "---"
echo ""

# Test 2: With not_valued (failed) ad
TEST_CLICK_ID_2="test_failed_$(date +%s)_$(shuf -i 1000-9999 -n 1)"
echo "Test 2: Sending FAILED ad postback..."
echo "---"
curl -s -X POST "$POSTBACK_URL?ymid=$TEST_CLICK_ID_2&estimated_price=0&reward_event_type=not_valued" \
  -H "Content-Type: application/json" | jq .

echo ""
echo "---"
echo ""
echo "✅ Postback tests completed"
echo "Check backend logs to see if postbacks were received"
