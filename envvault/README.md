# envvault - self-hosted .env vault (no dotenv.org, no payment)

AES-256-GCM. Same `.env.vault` format as `python-dotenv-vault`, so you keep one
key per environment. Everything runs locally; no cloud sync, no paid plan.

## Files

```
backend/
  .env          gitignored - your real plaintext secrets (local dev only)
  .env.vault    COMMITTED  - encrypted secrets, safe to push
  envvault/     package    - encrypt, decrypt, load
```

## Local workflow

### 1. Edit `backend/.env`

Just a normal plaintext `.env`. Keep it gitignored (already is via `backend/.gitignore`).
Run `python app.py` from `backend/` — `envvault.load_env()` will see no `DOTENV_KEY`
and fall back to loading `backend/.env` directly.

### 2. Encrypt and get the DOTENV_KEY

From the `backend/` directory:

```powershell
python -m envvault encrypt .env --environment production
```

Output:

```
================================================================
envvault: encrypted OK
================================================================
Source (.env)   : .../backend/.env
Vault (.env.vault): .../backend/.env.vault
Environment     : production
Bytes encrypted : 7294

>>> Your DOTENV_KEY (set this on Render): <<<
dotenv://:key_<64-hex-chars>@dotenv.org/vault/.env.vault?environment=production
```

- The key is random; saved only in this stdout. Save it somewhere safe.
- Re-running for the same environment generates a NEW key and NEW ciphertext
  for that environment (preserving any other environments already in the vault).
- To rotate the key: just re-run `encrypt`. Old key stops working.

### 3. Commit `.env.vault` and push

```powershell
git add backend/.env.vault
git commit -m "update .env.vault"
git push
```

`.env.vault` is tracked (the `.gitignore` has `!.env.vault`). `.env` stays ignored.

### 4. Set DOTENV_KEY on Render

Render Dashboard -> your service -> Environment -> add:

| Key        | Value                                                                  |
|------------|------------------------------------------------------------------------|
| DOTENV_KEY  | dotenv://:key_<64-hex>@dotenv.org/vault/.env.vault?environment=production |

Set it on both `atool-backend` and `atool-worker`. That is the ONLY secret Render
needs; all your app secrets come from the committed encrypted vault.

### 5. Render runtime

At startup `envvault.load_env()` checks `os.environ["DOTENV_KEY"]`:
- if present -> reads `backend/.env.vault`, finds `DOTENV_VAULT_PRODUCTION`,
  AES-GCM decrypts with the key, applies vars to `os.environ`.
- if absent -> falls back to `backend/.env` (local dev).

## Other commands

```powershell
# List environments present in the vault
python -m envvault list backend/.env.vault

# Decrypt & print plaintext (e.g. verify before deploying)
python -m envvault decrypt backend/.env.vault `
  --key "dotenv://:key_<64-hex>@dotenv.org/vault/.env.vault?environment=production"

# Decrypt to a file
python -m envvault decrypt backend/.env.vault `
  --key "dotenv://:key_<...>" --outfile /tmp/check.txt

# Encrypt for multiple environments (e.g. dev + prod, separate keys)
python -m envvault encrypt backend/.env --environment production
python -m envvault encrypt backend/.env.dev --environment development
```

## Security notes

- AES-256-GCM, fresh 12-byte random nonce per encryption, no AAD.
- Each `encrypt` run produces a key that only decrypts that one environment's block.
- A leaked `.env.vault` without its `DOTENV_KEY` is useless ciphertext.
- A leaked `DOTENV_KEY` without `.env.vault` is useless on its own.
- For real rotation, regenerate the key (`encrypt` always makes a new one) and
  update it on Render; the old ciphertext is replaced.
- `backend/.env` is gitignored; never commit plaintext secrets.

## Failure modes

| Symptom                                            | Cause / Fix                                            |
|----------------------------------------------------|--------------------------------------------------------|
| `NOT_FOUND_DOTENV_ENVIRONMENT: 'production'`       | wrong env name in URI, or vault not rebuilt            |
| `INVALID_DOTENV_KEY: Key ... must be 64 chars`      | key truncated / copied wrong                          |
| `INVALID_DOTENV_KEY: decryption failed`             | key doesn't match this vault's ciphertext              |
| `FileNotFoundError: DOTENV_KEY set but no vault`    | `.env.vault` missing from deploy - commit it          |

## Why not python-dotenv-vault anymore?

`python-dotenv-vault` was already free for decryption, but its *sync* / *build*
flow (`npx dotenv-vault`) calls the paid dotenv.org service. `envvault` does the
same AES-GCM crypto locally with no dotenv.org dependency at all.
