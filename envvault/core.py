"""Core encrypt/decrypt + vault file (de)serialization for envvault."""
from __future__ import annotations

import os
import re
import secrets
from base64 import b64encode, b64decode
from pathlib import Path
from typing import Iterable, Optional
from urllib.parse import urlparse, parse_qsl, quote

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.exceptions import InvalidTag

KEY_HEX_LEN = 64  # 64 hex chars = 32 bytes = AES-256
NONCE_LEN = 12

_VAULT_HEADER = (
    "#/-------------------.env.vault---------------------/\n"
    "#/                envvault (self-hosted)             /\n"
    "#/   AES-256-GCM. Decrypt with DOTENV_KEY env var.  /\n"
    "#/------------------------------------------------- /\n"
)

_ENTRY_RE = re.compile(
    r'^\s*DOTENV_VAULT_(\w+)\s*=\s*"([^"]+)"\s*$',
    re.MULTILINE,
)


def generate_key() -> str:
    """Return a fresh 64-hex-char (32-byte) AES key."""
    return secrets.token_hex(KEY_HEX_LEN // 2)


def encrypt_bytes(plaintext: bytes, key_hex: str) -> str:
    """Encrypt plaintext bytes. Return base64(nonce||ciphertext||tag)."""
    aesgcm = AESGCM(bytes.fromhex(key_hex[-KEY_HEX_LEN:]))
    nonce = os.urandom(NONCE_LEN)
    ct = aesgcm.encrypt(nonce, plaintext, b"")
    return b64encode(nonce + ct).decode("ascii")


def decrypt_bytes(ciphertext_b64: str, key_hex: str) -> bytes:
    """Decrypt base64(nonce||ciphertext||tag) back to plaintext bytes."""
    aesgcm = AESGCM(bytes.fromhex(key_hex[-KEY_HEX_LEN:]))
    raw = b64decode(ciphertext_b64)
    nonce, ct = raw[:NONCE_LEN], raw[NONCE_LEN:]
    return aesgcm.decrypt(nonce, ct, b"")


def key_uri(key_hex: str, environment: str) -> str:
    """Build a dotenv:// URI embedding the key (password) and environment (query)."""
    return f"dotenv://:key_{key_hex}@dotenv.org/vault/.env.vault?environment={environment}"


def parse_key_uri(uri: str) -> tuple[str, str]:
    """Parse a dotenv:// URI -> (key_hex, environment_name)."""
    parsed = urlparse(uri.strip())
    key = parsed.password
    if not key or len(key) < KEY_HEX_LEN:
        raise ValueError(
            f"INVALID_DOTENV_KEY: key part needs >= {KEY_HEX_LEN} hex chars (got {len(key) if key else 0})"
        )
    params = dict(parse_qsl(parsed.query))
    env = params.get("environment")
    if not env:
        raise ValueError("INVALID_DOTENV_KEY: missing ?environment=<name>")
    return key[-KEY_HEX_LEN:], env


def build_vault_file(entries: dict[str, str]) -> str:
    """Serialize {env_name: ciphertext_b64} -> .env.vault file content."""
    lines: list[str] = [_VAULT_HEADER, ""]
    for env in sorted(entries):
        lines.append(f"# {env}")
        lines.append(f'DOTENV_VAULT_{env.upper()}="{entries[env]}"')
        lines.append(f"DOTENV_VAULT_{env.upper()}_VERSION=1")
        lines.append("")
    return "\n".join(lines)


def parse_vault_file(content: str) -> dict[str, str]:
    """Return {env_name: ciphertext_b64} from .env.vault content."""
    out: dict[str, str] = {}
    for env, ct in _ENTRY_RE.findall(content):
        if env.endswith("_VERSION"):
            continue
        out[env.lower()] = ct
    return out


def load_env(
    vault_path: Optional[Path | str] = None,
    dotenv_key: Optional[str] = None,
    fallback_dotenv: Optional[Path | str] = None,
) -> bool:
    """Decrypt .env.vault using DOTENV_KEY and apply vars to os.environ.

    Resolution order for the key:
      1. dotenv_key arg
      2. os.environ['DOTENV_KEY']
    Vault path resolution:
      1. vault_path arg
      2. os.environ['VAULT_PATH']
      3. ./.env.vault in CWD, then walk up to repo root
    Fallback: if no DOTENV_KEY present, load plaintext fallback_dotenv
    (default: backend/.env) via python-dotenv so local dev still works.

    Returns True if anything was loaded.
    """
    from dotenv import load_dotenv as _load_dotenv

    key = dotenv_key or os.environ.get("DOTENV_KEY")
    if not key:
        if fallback_dotenv is None:
            fallback_dotenv = _find_dotenv_upward(".env")
        if fallback_dotenv and Path(fallback_dotenv).exists():
            return _load_dotenv(fallback_dotenv, override=True)
        return False

    if vault_path is None:
        vault_path = os.environ.get("VAULT_PATH") or _find_dotenv_upward(".env.vault")
    if not vault_path or not Path(vault_path).exists():
        raise FileNotFoundError(
            f"DOTENV_KEY set but no .env.vault found near {vault_path or 'CWD'}. "
            "Run: python -m envvault encrypt backend/.env"
        )

    vault_text = Path(vault_path).read_text(encoding="utf-8")
    entries = parse_vault_file(vault_text)

    key_hex, env_name = parse_key_uri(key)
    ct = entries.get(env_name.lower())
    if not ct:
        raise KeyError(
            f"NOT_FOUND_DOTENV_ENVIRONMENT: environment '{env_name}' not in .env.vault. "
            f"Available: {', '.join(entries)}"
        )

    try:
        plaintext = decrypt_bytes(ct, key_hex)
    except InvalidTag:
        raise ValueError(
            f"INVALID_DOTENV_KEY: key does not decrypt environment '{env_name}'. "
            "Regenerate with: python -m envvault encrypt backend/.env"
        )

    # Parse & apply without clobbering real OS env vars (Render's already-set
    # vars win). python-dotenv honors override=False by default.
    import io
    return _load_dotenv(stream=io.StringIO(plaintext.decode("utf-8")), override=True)


def _find_dotenv_upward(filename: str, start: Optional[Path | str] = None) -> Optional[Path]:
    """Walk upward from start (default: CWD) looking for filename."""
    p = Path(start or Path.cwd()).resolve()
    for cand in [p, *p.parents]:
        f = cand / filename
        if f.is_file():
            return f
    return None
