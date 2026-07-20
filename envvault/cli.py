"""CLI for envvault: encrypt, decrypt, inspect."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from urllib.parse import urlparse, parse_qsl

from .core import (
    KEY_HEX_LEN,
    generate_key,
    encrypt_bytes,
    decrypt_bytes,
    build_vault_file,
    parse_vault_file,
    parse_key_uri,
    key_uri,
    load_env,
)


def _cmd_encrypt(args: argparse.Namespace) -> int:
    env_file = Path(args.envfile).resolve()
    if not env_file.is_file():
        print(f"ERROR: not found: {env_file}", file=sys.stderr)
        return 2

    plaintext = env_file.read_bytes()
    if not plaintext.strip():
        print(f"ERROR: {env_file} is empty", file=sys.stderr)
        return 2

    vault_path = Path(args.vault).resolve() if args.vault else env_file.parent / ".env.vault"

    # Preserve any existing environments already in the vault file.
    entries: dict[str, str] = {}
    if vault_path.exists():
        existing = parse_vault_file(vault_path.read_text(encoding="utf-8"))
        entries.update(existing)

    # New key for the target environment.
    key_hex = args.key or generate_key()
    if len(key_hex) < KEY_HEX_LEN:
        print(f"ERROR: --key must be >= {KEY_HEX_LEN} hex chars (got {len(key_hex)})", file=sys.stderr)
        return 2

    ciphertext = encrypt_bytes(plaintext, key_hex)
    entries[args.environment.lower()] = ciphertext

    vault_path.write_text(build_vault_file(entries), encoding="utf-8")

    uri = key_uri(key_hex, args.environment)
    print("=" * 64)
    print("envvault: encrypted OK")
    print("=" * 64)
    print(f"Source (.env)   : {env_file}")
    print(f"Vault (.env.vault): {vault_path}")
    print(f"Environment     : {args.environment}")
    print(f"Bytes encrypted : {len(plaintext)}")
    print()
    print(">>> Your DOTENV_KEY (set this on Render): <<<")
    print(uri)
    print()
    print(f"Commit {vault_path} to git. Set DOTENV_KEY in Render Dashboard (or env).")
    print("Locally, the app also falls back to plaintext .env if DOTENV_KEY is unset.")
    return 0


def _cmd_decrypt(args: argparse.Namespace) -> int:
    vault_path = Path(args.vault).resolve()
    if not vault_path.is_file():
        print(f"ERROR: not found: {vault_path}", file=sys.stderr)
        return 2

    entries = parse_vault_file(vault_path.read_text(encoding="utf-8"))
    if not entries:
        print("ERROR: no environments in vault", file=sys.stderr)
        return 2

    key_hex, env_name = parse_key_uri(args.key)
    ct = entries.get(env_name.lower())
    if not ct:
        print(f"ERROR: environment '{env_name}' not in vault. Available: {', '.join(entries)}", file=sys.stderr)
        return 2

    try:
        plaintext = decrypt_bytes(ct, key_hex)
    except Exception as e:
        print(f"ERROR: decryption failed: {e}", file=sys.stderr)
        return 1

    if args.outfile:
        Path(args.outfile).write_bytes(plaintext)
        print(f"Wrote {args.outfile} ({len(plaintext)} bytes)")
    else:
        sys.stdout.write(plaintext.decode("utf-8"))
        if not plaintext.endswith(b"\n"):
            sys.stdout.write("\n")
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    vault_path = Path(args.vault).resolve()
    if not vault_path.is_file():
        print(f"ERROR: not found: {vault_path}", file=sys.stderr)
        return 2
    entries = parse_vault_file(vault_path.read_text(encoding="utf-8"))
    print(f"Vault: {vault_path}")
    print(f"Environments: {', '.join(sorted(entries)) or '(none)'}")
    for env, ct in sorted(entries.items()):
        print(f"  {env:<12} -> {len(ct)} base64 chars")
    return 0


def _cmd_load(args: argparse.Namespace) -> int:
    ok = load_env()
    print(f"load_env() -> {ok}")
    import os
    for k in ("SUPABASE_URL", "JWT_SECRET", "CLOUDINARY_CLOUD_NAME"):
        print(f"  {k}={'SET' if os.getenv(k) else 'MISSING'}")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="envvault",
        description="Self-hosted .env vault (AES-256-GCM). No dotenv.org, no payment.",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_enc = sub.add_parser("encrypt", help="Encrypt a plaintext .env into .env.vault, print a DOTENV_KEY.")
    p_enc.add_argument("envfile", help="Path to plaintext .env to encrypt")
    p_enc.add_argument("--vault", help="Output .env.vault path (default: <envfile dir>/.env.vault)")
    p_enc.add_argument("--environment", default="production", help="Environment name (default: production)")
    p_enc.add_argument("--key", help="Reuse an existing 64-hex-char key (default: generate new)")
    p_enc.set_defaults(func=_cmd_encrypt)

    p_dec = sub.add_parser("decrypt", help="Decrypt one environment of .env.vault using a DOTENV_KEY.")
    p_dec.add_argument("vault", nargs="?", default=".env.vault", help="Path to .env.vault (default: ./.env.vault)")
    p_dec.add_argument("--key", required=True, help="dotenv:// URI (or set DOTENV_KEY env var and pass --key from env)")
    p_dec.add_argument("--outfile", help="Write plaintext to file (default: stdout)")
    p_dec.set_defaults(func=_cmd_decrypt)

    p_list = sub.add_parser("list", help="List environments present in .env.vault")
    p_list.add_argument("vault", nargs="?", default=".env.vault", help="Path to .env.vault")
    p_list.set_defaults(func=_cmd_list)

    p_load = sub.add_parser("load", help="Load env into os.environ (uses DOTENV_KEY if set, else falls back to .env)")
    p_load.set_defaults(func=_cmd_load)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
