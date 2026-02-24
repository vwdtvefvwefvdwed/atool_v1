import os
import json
import importlib
from pathlib import Path
from typing import Dict, List, Optional

from error_notifier import notify_error, ErrorType

__all__ = ['notify_error', 'ErrorType', 'discover_workflows', 'get_all_workflows', 'get_workflow_class', 'reload_workflows']

_workflows_cache: Dict[str, any] = {}

def load_workflow_config(workflow_path: Path) -> Optional[Dict]:
    config_file = workflow_path / "config.json"
    if config_file.exists():
        with open(config_file, 'r', encoding='utf-8') as f:
            return json.load(f)
    return None

def discover_workflows() -> Dict[str, any]:
    global _workflows_cache
    
    if _workflows_cache:
        return _workflows_cache
    
    workflows_dir = Path(__file__).parent
    workflows = {}
    
    for item in workflows_dir.iterdir():
        if item.is_dir() and not item.name.startswith('_'):
            config = load_workflow_config(item)
            
            if config and config.get('enabled', True):
                try:
                    module = importlib.import_module(f'workflows.{item.name}.workflow')
                    workflow_class = getattr(module, f'{_to_pascal_case(item.name)}Workflow', None)
                    
                    if workflow_class:
                        workflows[config['id']] = {
                            'config': config,
                            'class': workflow_class,
                            'path': item
                        }
                except (ImportError, AttributeError) as e:
                    print(f"Failed to load workflow {item.name}: {e}")
    
    _workflows_cache = workflows
    return workflows

def _to_pascal_case(snake_str: str) -> str:
    return ''.join(word.capitalize() for word in snake_str.split('_'))

def get_all_workflows() -> List[Dict]:
    workflows = discover_workflows()
    return [w['config'] for w in workflows.values()]

def get_workflow_class(workflow_id: str):
    workflows = discover_workflows()
    workflow = workflows.get(workflow_id)
    return workflow['class'] if workflow else None

def reload_workflows():
    global _workflows_cache
    _workflows_cache = {}
    return discover_workflows()
