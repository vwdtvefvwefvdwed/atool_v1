"""
Failover Event Broadcaster
Broadcasts failover events to all connected frontend clients via Server-Sent Events (SSE)
"""

import time
import queue
import threading
from typing import Set
from datetime import datetime

# Global set of client queues (each client gets a queue)
_connected_clients: Set[queue.Queue] = set()
_clients_lock = threading.Lock()


def add_client(client_queue: queue.Queue):
    """Register a new SSE client"""
    with _clients_lock:
        _connected_clients.add(client_queue)
        print(f"[Failover Broadcast] Client connected. Total clients: {len(_connected_clients)}")


def remove_client(client_queue: queue.Queue):
    """Unregister an SSE client"""
    with _clients_lock:
        _connected_clients.discard(client_queue)
        print(f"[Failover Broadcast] Client disconnected. Total clients: {len(_connected_clients)}")


def broadcast_failover_event(event_data: dict):
    """
    Broadcast failover event to ALL connected clients
    
    Args:
        event_data: {
            "event": "failover",
            "using_backup": bool,
            "main_url": str,
            "backup_url": str,
            "failover_time": str,
            "failover_reason": str
        }
    """
    with _clients_lock:
        client_count = len(_connected_clients)
        print(f"[Failover Broadcast] Broadcasting failover event to {client_count} clients")
        print(f"[Failover Broadcast] Event: {event_data}")
        
        # Send to all connected clients
        dead_clients = []
        for client_queue in _connected_clients:
            try:
                # Non-blocking put (drop if queue full)
                client_queue.put_nowait(event_data)
            except queue.Full:
                print(f"[Failover Broadcast] Client queue full, marking for removal")
                dead_clients.append(client_queue)
            except Exception as e:
                print(f"[Failover Broadcast] Error sending to client: {e}")
                dead_clients.append(client_queue)
        
        # Clean up dead clients
        for dead_client in dead_clients:
            _connected_clients.discard(dead_client)
        
        if dead_clients:
            print(f"[Failover Broadcast] Removed {len(dead_clients)} dead clients")


def get_connected_client_count() -> int:
    """Get number of currently connected SSE clients"""
    with _clients_lock:
        return len(_connected_clients)


def format_sse_message(data: dict) -> str:
    """
    Format message for Server-Sent Events protocol
    
    Format:
        event: failover
        data: {"using_backup": true, ...}
        
    """
    import json
    event_type = data.get("event", "message")
    message = f"event: {event_type}\n"
    message += f"data: {json.dumps(data)}\n\n"
    return message
