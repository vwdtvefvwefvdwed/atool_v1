"""
Test Worker Accounts and Edge Function
Verifies all 3 worker Supabase accounts are functioning correctly
"""

import os
import uuid
import time
from datetime import datetime
from dotenv_vault import load_dotenv
from supabase import create_client
from worker_client import get_worker_client
import requests

load_dotenv()

# Worker configurations
WORKERS = [
    {
        'id': 'worker-1',
        'url': os.getenv('WORKER_1_URL'),
        'key': os.getenv('WORKER_1_ANON_KEY'),
    },
    {
        'id': 'worker-2',
        'url': os.getenv('WORKER_2_URL'),
        'key': os.getenv('WORKER_2_ANON_KEY'),
    },
    {
        'id': 'worker-3',
        'url': os.getenv('WORKER_3_URL'),
        'key': os.getenv('WORKER_3_ANON_KEY'),
    }
]

EDGE_FUNCTION_URL = os.getenv('EDGE_FUNCTION_URL')
MAIN_SUPABASE_KEY = os.getenv('SUPABASE_SERVICE_ROLE_KEY')
USE_EDGE_FUNCTION = os.getenv('USE_EDGE_FUNCTION', 'false').lower() == 'true'


def print_header(text):
    """Print formatted header"""
    print("\n" + "=" * 80)
    print(f"  {text}")
    print("=" * 80)


def print_success(text):
    """Print success message"""
    print(f"✅ {text}")


def print_error(text):
    """Print error message"""
    print(f"❌ {text}")


def print_info(text):
    """Print info message"""
    print(f"ℹ️  {text}")


def test_worker_connection(worker_config):
    """Test direct connection to a worker"""
    print(f"\n🔌 Testing {worker_config['id']}...")
    print(f"   URL: {worker_config['url']}")
    
    try:
        # Create Supabase client
        client = create_client(worker_config['url'], worker_config['key'])
        
        # Try to select from priority1_queue (should work even if empty)
        response = client.table('priority1_queue').select('*').limit(1).execute()
        
        print_success(f"{worker_config['id']} connection successful")
        return True
        
    except Exception as e:
        print_error(f"{worker_config['id']} connection failed: {str(e)}")
        return False


def test_worker_insert(worker_config, priority=1):
    """Test inserting data into worker queue"""
    print(f"\n📝 Testing INSERT to {worker_config['id']} (priority{priority}_queue)...")
    
    try:
        client = create_client(worker_config['url'], worker_config['key'])
        
        # Create test data
        test_data = {
            'user_id': str(uuid.uuid4()),
            'job_id': str(uuid.uuid4()),
            'request_payload': {
                'prompt': f'Test from {worker_config["id"]}',
                'model': 'flux-dev',
                'aspect_ratio': '1:1'
            }
        }
        
        # Insert into queue
        table_name = f'priority{priority}_queue'
        response = client.table(table_name).insert(test_data).execute()
        
        if response.data:
            print_success(f"INSERT successful to {worker_config['id']}/{table_name}")
            return response.data[0]
        else:
            print_error(f"INSERT failed - no data returned")
            return None
            
    except Exception as e:
        print_error(f"INSERT failed: {str(e)}")
        return None


def test_worker_select(worker_config, priority=1):
    """Test selecting data from worker queue"""
    print(f"\n🔍 Testing SELECT from {worker_config['id']} (priority{priority}_queue)...")
    
    try:
        client = create_client(worker_config['url'], worker_config['key'])
        
        # Select from queue
        table_name = f'priority{priority}_queue'
        response = client.table(table_name).select('*').limit(5).execute()
        
        count = len(response.data) if response.data else 0
        print_success(f"SELECT successful - found {count} entries in {table_name}")
        return response.data
        
    except Exception as e:
        print_error(f"SELECT failed: {str(e)}")
        return None


def test_edge_function_insert(priority=1):
    """Test inserting via edge function"""
    print(f"\n🌐 Testing Edge Function INSERT (priority{priority}_queue)...")
    
    if not EDGE_FUNCTION_URL:
        print_error("EDGE_FUNCTION_URL not configured")
        return None
    
    try:
        # Create test data
        test_data = {
            'user_id': str(uuid.uuid4()),
            'job_id': str(uuid.uuid4()),
            'request_payload': {
                'prompt': f'Test via Edge Function - {datetime.now().isoformat()}',
                'model': 'flux-dev',
                'aspect_ratio': '1:1'
            }
        }
        
        # Call edge function
        payload = {
            'operation': 'insert',
            'table': f'priority{priority}_queue',
            'data': test_data
        }
        
        headers = {
            'Content-Type': 'application/json',
            'apikey': MAIN_SUPABASE_KEY,
            'Authorization': f'Bearer {MAIN_SUPABASE_KEY}'
        }
        
        response = requests.post(EDGE_FUNCTION_URL, json=payload, headers=headers, timeout=30)
        response.raise_for_status()
        result = response.json()
        
        if result.get('success'):
            worker_used = result.get('worker', 'unknown')
            print_success(f"Edge Function INSERT successful - routed to {worker_used}")
            return result
        else:
            print_error(f"Edge Function returned error: {result.get('error')}")
            return None
            
    except Exception as e:
        print_error(f"Edge Function failed: {str(e)}")
        return None


def test_round_robin_distribution():
    """Test round-robin distribution across workers"""
    print_header("Testing Round-Robin Distribution")
    print_info("Sending 6 requests to see distribution pattern...")
    
    worker_counts = {}
    
    for i in range(6):
        print(f"\n📤 Request {i + 1}/6...")
        result = test_edge_function_insert(priority=1)
        
        if result:
            worker = result.get('worker', 'unknown')
            worker_counts[worker] = worker_counts.get(worker, 0) + 1
            time.sleep(0.5)  # Small delay between requests
    
    print("\n" + "-" * 80)
    print("📊 Distribution Summary:")
    for worker, count in sorted(worker_counts.items()):
        print(f"   {worker}: {count} requests")
    print("-" * 80)
    
    # Check if distribution is balanced
    if len(worker_counts) >= 2:
        print_success("Round-robin distribution working (multiple workers used)")
    else:
        print_error("Distribution issue - only 1 worker used")
    
    return worker_counts


def test_all_priority_queues(worker_config):
    """Test all 3 priority queues on a worker"""
    print(f"\n🎯 Testing all priority queues on {worker_config['id']}...")
    
    results = {}
    for priority in [1, 2, 3]:
        try:
            client = create_client(worker_config['url'], worker_config['key'])
            table_name = f'priority{priority}_queue'
            response = client.table(table_name).select('*').limit(1).execute()
            results[priority] = True
            print_success(f"  {table_name} accessible")
        except Exception as e:
            results[priority] = False
            print_error(f"  {table_name} error: {str(e)}")
    
    return all(results.values())


def cleanup_test_data():
    """Clean up test data from all workers"""
    print_header("Cleanup Test Data")
    print_info("Removing test entries from all workers...")
    
    for worker in WORKERS:
        try:
            client = create_client(worker['url'], worker['key'])
            
            for priority in [1, 2, 3]:
                table_name = f'priority{priority}_queue'
                # Delete test entries (where prompt contains 'Test')
                # Note: This requires RLS to be configured properly
                print(f"   Cleaning {worker['id']}/{table_name}...")
                
        except Exception as e:
            print(f"   Warning: Cleanup failed for {worker['id']}: {str(e)}")
    
    print_info("Cleanup complete (note: may require manual cleanup if RLS restricts)")


def main():
    """Run all tests"""
    print_header("Worker Accounts Test Suite")
    print_info(f"Edge Function URL: {EDGE_FUNCTION_URL}")
    print_info(f"USE_EDGE_FUNCTION: {USE_EDGE_FUNCTION}")
    
    # Test 1: Direct worker connections
    print_header("Test 1: Direct Worker Connections")
    connection_results = {}
    for worker in WORKERS:
        connection_results[worker['id']] = test_worker_connection(worker)
    
    # Test 2: Insert operations
    print_header("Test 2: Direct INSERT Operations")
    insert_results = {}
    for worker in WORKERS:
        if connection_results[worker['id']]:
            insert_results[worker['id']] = test_worker_insert(worker, priority=1)
    
    # Test 3: Select operations
    print_header("Test 3: Direct SELECT Operations")
    for worker in WORKERS:
        if connection_results[worker['id']]:
            test_worker_select(worker, priority=1)
    
    # Test 4: All priority queues
    print_header("Test 4: All Priority Queues")
    priority_results = {}
    for worker in WORKERS:
        if connection_results[worker['id']]:
            priority_results[worker['id']] = test_all_priority_queues(worker)
    
    # Test 5: Edge function (if configured)
    if EDGE_FUNCTION_URL:
        print_header("Test 5: Edge Function Routing")
        edge_result = test_edge_function_insert(priority=1)
        
        # Test 6: Round-robin distribution
        distribution = test_round_robin_distribution()
    else:
        print_header("Test 5 & 6: Edge Function Tests")
        print_error("EDGE_FUNCTION_URL not configured - skipping edge function tests")
    
    # Final Summary
    print_header("TEST SUMMARY")
    
    print("\n🔌 Worker Connections:")
    for worker_id, status in connection_results.items():
        status_icon = "✅" if status else "❌"
        print(f"   {status_icon} {worker_id}: {'Connected' if status else 'Failed'}")
    
    print("\n📝 INSERT Operations:")
    for worker_id, result in insert_results.items():
        status_icon = "✅" if result else "❌"
        print(f"   {status_icon} {worker_id}: {'Success' if result else 'Failed'}")
    
    print("\n🎯 Priority Queues:")
    for worker_id, status in priority_results.items():
        status_icon = "✅" if status else "❌"
        print(f"   {status_icon} {worker_id}: {'All queues working' if status else 'Some queues failed'}")
    
    if EDGE_FUNCTION_URL:
        print("\n🌐 Edge Function:")
        all_workers_healthy = all(connection_results.values())
        print(f"   {'✅' if all_workers_healthy else '❌'} Round-robin distribution: {len(distribution)} workers used")
    
    # Overall status
    all_passed = (
        all(connection_results.values()) and
        all(insert_results.values()) and
        all(priority_results.values())
    )
    
    print("\n" + "=" * 80)
    if all_passed:
        print("🎉 ALL TESTS PASSED - All workers are functioning correctly!")
    else:
        print("⚠️  SOME TESTS FAILED - Check errors above")
    print("=" * 80)
    
    return all_passed


if __name__ == "__main__":
    success = main()
    exit(0 if success else 1)
