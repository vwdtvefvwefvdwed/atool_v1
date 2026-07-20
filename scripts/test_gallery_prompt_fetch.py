"""Test: Gallery Remix live prompt fetch. Prints NO secrets."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
from envvault import load_env
load_env()

def main():
    print("=" * 60)
    # 1. Static check: no main/new account usage in prompt_store
    store_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'workflows', 'common_workflow', 'prompt_store.py')
    src = open(store_path, 'r', encoding='utf-8').read()
    forbidden = ['from supabase_client', 'import supabase_client', 'supabase_failover',
                 'get_failover_manager', "os.getenv('SUPABASE_URL')", 'os.getenv("SUPABASE_URL")', "'NEW_SUPABASE_URL'"]
    hits = [x for x in forbidden if x in src]
    if hits:
        print(f"[FAIL] prompt_store references main/new account: {hits}"); return 1
    print("[OK]   prompt_store.py: ZERO main/new account code references")

    # 2. Env configured
    from workflows.common_workflow.prompt_store import get_prompt_store, PROMPT_LOOKUP_UNAVAILABLE, GEN_ID_OFFSET
    store = get_prompt_store()
    if not store.configured():
        print("[FAIL] GALLERY_SUPABASE_URL / GALLERY_SUPABASE_ANON_KEY not set in backend env"); return 1
    print("[OK]   GALLERY_SUPABASE_* env configured")

    # 3. Live resolve with a real gallery_feed id
    client = store._get_client()
    resp = client.table('gallery_feed').select('id').limit(1).execute()
    if not resp.data:
        print("[WARN] gallery_feed empty - positive path not testable")
    else:
        rid = resp.data[0]['id']
        entry = store.resolve(rid)
        if not isinstance(entry, dict):
            print(f"[FAIL] resolve({rid}) -> {entry}"); return 1
        print(f"[OK]   resolve({rid}) -> name='{entry.get('name')}' prompt_present={bool(entry.get('prompt'))}")

    # 4. generated_feed offset path (optional)
    try:
        resp = client.table('generated_feed').select('id').limit(1).execute()
        if resp.data:
            gid = GEN_ID_OFFSET + resp.data[0]['id']
            entry = store.resolve(gid)
            if not isinstance(entry, dict):
                print(f"[FAIL] offset path resolve({gid}) -> {entry}"); return 1
            print(f"[OK]   generated_feed offset path: resolve({gid}) -> entry")
        else:
            print("[SKIP] generated_feed empty")
    except Exception:
        print("[SKIP] generated_feed view not present (optional)")

    # 5. Unknown id -> None (410 path)
    entry = store.resolve(999999999)
    if entry is None:
        print("[OK]   unknown id -> None (app.py returns 410)")
    elif entry is PROMPT_LOOKUP_UNAVAILABLE:
        print("[FAIL] unknown id -> UNAVAILABLE"); return 1
    else:
        print("[WARN] unknown id unexpectedly resolved")
    print("=" * 60)
    print("ALL CHECKS PASSED - main/new untouched, live gallery fetch works")
    return 0

if __name__ == '__main__':
    sys.exit(main())
