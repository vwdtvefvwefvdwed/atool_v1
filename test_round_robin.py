"""
Test script for Round-Robin API Key Rotation
Simulates multiple requests to verify proper rotation behavior
"""

import api_key_round_robin

def test_round_robin():
    """Test round-robin rotation logic with mock data"""
    
    print("\n" + "="*60)
    print("ROUND-ROBIN API KEY ROTATION TEST")
    print("="*60 + "\n")
    
    class MockSupabaseClient:
        """Mock Supabase client for testing"""
        def __init__(self, key_count):
            self.key_count = key_count
        
        def table(self, table_name):
            return self
        
        def select(self, fields, count=None):
            return self
        
        def eq(self, field, value):
            return self
        
        def execute(self):
            class Result:
                def __init__(self, count):
                    self.count = count
            return Result(self.key_count)
    
    api_key_round_robin.rotation_state = {}
    
    print("Test 1: vision-atlas with 4 API keys")
    print("-" * 60)
    
    mock_client = MockSupabaseClient(4)
    provider_key = "vision-atlas"
    provider_id = "test-provider-1"
    
    for i in range(8):
        row = api_key_round_robin.get_next_row_for_provider(provider_key, provider_id, mock_client)
        print(f"  Request {i+1}: Using row {row}")
        api_key_round_robin.mark_row_used(provider_key, row, save_to_disk=False)
    
    print("\nExpected: 0, 1, 2, 3, 0, 1, 2, 3 (wraps around)")
    
    print("\n" + "="*60)
    print("Test 2: vision-nova with 3 API keys")
    print("-" * 60)
    
    mock_client2 = MockSupabaseClient(3)
    provider_key2 = "vision-nova"
    provider_id2 = "test-provider-2"
    
    for i in range(7):
        row = api_key_round_robin.get_next_row_for_provider(provider_key2, provider_id2, mock_client2)
        print(f"  Request {i+1}: Using row {row}")
        api_key_round_robin.mark_row_used(provider_key2, row, save_to_disk=False)
    
    print("\nExpected: 0, 1, 2, 0, 1, 2, 0 (wraps around)")
    
    print("\n" + "="*60)
    print("Test 3: Current rotation state")
    print("-" * 60)
    
    state = api_key_round_robin.get_current_state()
    for provider, data in state.items():
        print(f"  {provider}: current_row = {data['current_row']}")
    
    print("\n" + "="*60)
    print("Test 4: Reset provider")
    print("-" * 60)
    
    print(f"  Before reset: vision-atlas at row {api_key_round_robin.rotation_state['vision-atlas']['current_row']}")
    api_key_round_robin.reset_provider("vision-atlas")
    print(f"  After reset: vision-atlas at row {api_key_round_robin.rotation_state['vision-atlas']['current_row']}")
    
    print("\n" + "="*60)
    print("✅ All tests completed!")
    print("="*60 + "\n")

if __name__ == "__main__":
    test_round_robin()
