# Flask Backend - Discord Bot Integration

This Flask backend fetches the latest ngrok URL from Discord and proxies requests to ComfyUI.

## Setup

1. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

2. **Configuration:**
   - Discord credentials are stored in `.env` file
   - The `.env` file is already created with bot token and channel ID
   - No additional configuration needed unless using a different Discord bot

3. **Run the server:**
   ```bash
   python app.py
   ```
   
   Server will start on `http://localhost:5000`

## Endpoints

- `GET /get-url` - Fetch latest ngrok URL from Discord
- `POST /generate` - Generate AI content (auto-fetches URL)
- `GET /health` - Check backend status
- `POST /clear-cache` - Clear cached URL

## How It Works

1. ComfyUI posts ngrok URL to Discord webhook
2. Flask backend fetches latest URL from Discord API
3. Frontend calls `/generate` endpoint
4. Backend forwards request to ComfyUI via ngrok URL

## Environment Secrets (envvault)

Secrets are managed by the self-hosted `envvault` package (replaces the paid
`python-dotenv-vault` / dotenv.org sync). Same one-key deployment model, no
dotenv.org dependency.

- Local plaintext secrets live in `backend/.env` (gitignored).
- Encrypted vault `backend/.env.vault` is committed; safe to push.
- Render stores ONE secret (`DOTENV_KEY`) which decrypts the vault at boot.

### Quick workflow

```powershell
cd backend
# edit plaintext secrets
notepad .env
# re-encrypt (prints the new DOTENV_KEY) - then update it on Render
python -m envvault encrypt .env --environment production
# commit the new vault
git add .env.vault && git commit -m "rotate secrets" && git push
```

Full migration write-up + operator runbook:
[`docs/architecture/ENVVAULT_MIGRATION.md`](../docs/architecture/ENVVAULT_MIGRATION.md)

Workflow cheatsheet: [`envvault/README.md`](envvault/README.md)


