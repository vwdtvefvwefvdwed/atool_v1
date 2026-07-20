"""
envvault: self-hosted .env vault (no dotenv.org, no payment).

Encrypt a plaintext .env locally -> get ONE dotenv:// key -> commit the
encrypted .env.vault -> set DOTENV_KEY on Render -> app decrypts at startup.

Format (matches python-dotenv-vault's .env.vault so the same .env.vault works
on both, if you ever want to switch back):

    #/-------------------.env.vault---------------------/
    #/                envvault (self-hosted)             /
    #/------------------------------------------------- /

    DOTENV_VAULT_<ENV>="<base64(nonce||ciphertext||tag)>"
    DOTENV_VAULT_<ENV>_VERSION=1

Crypto: AES-256-GCM, 12-byte random nonce, no AAD. Each environment gets its
own fresh random key (so dev/prod keys differ as before).
"""
from .core import (
    KEY_HEX_LEN,
    generate_key,
    encrypt_bytes,
    decrypt_bytes,
    build_vault_file,
    parse_vault_file,
    key_uri,
    parse_key_uri,
    load_env,
)
from .cli import main

__all__ = [
    "KEY_HEX_LEN",
    "generate_key",
    "encrypt_bytes",
    "decrypt_bytes",
    "build_vault_file",
    "parse_vault_file",
    "key_uri",
    "parse_key_uri",
    "load_env",
    "main",
]
