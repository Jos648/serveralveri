# IdlePool

IdlePool currently has a local desktop mode and an experimental Cloudflare
control plane. The Cloudflare Worker coordinates accounts, listings, bids and
leases; provider agents heartbeat over HTTPS, so providers do not need an
inbound port.

## Free Cloudflare prototype setup

Cloudflare hosts the control API and D1 database. It does **not** run Docker or
customer Python/Node programs. Application containers and public app links are
not implemented in this prototype yet.

1. Install Node.js LTS and create a free Cloudflare account.
2. From the project folder, install Wrangler:

   ```powershell
   npm install --save-dev wrangler
   npx wrangler login
   ```

3. Create the D1 database and copy the returned ID into
   `cloudflare\wrangler.toml`, replacing `REPLACE_WITH_D1_DATABASE_ID`:

   ```powershell
   npx wrangler d1 create idlepool-control
   ```

4. Apply the schema and deploy the Worker:

   ```powershell
   npx wrangler d1 migrations apply idlepool-control --remote --config cloudflare\wrangler.toml
   npx wrangler deploy --config cloudflare\wrangler.toml
   ```

5. Start the desktop app against its deployed `workers.dev` URL:

   ```powershell
   .\.venv\Scripts\python.exe .\idlepool.py server --cloud-api https://YOUR-WORKER.YOUR-SUBDOMAIN.workers.dev
   ```

6. On every computer that should use the same marketplace, run the app with
   that exact same `--cloud-api` URL. The listing is visible without logging
   in; creating an order or renting requires an account. Register once, then
   use the account key from **Hesab** to sign in on the other computer. In
   **Cihazımı sat**, copy the generated agent command and run it on the provider
   PC. The command includes the cloud API URL; the agent sends HTTPS heartbeats
   and receives lease notifications by polling.

   For the packaged Windows app, use:

   ```powershell
   .\dist\IdlePool.exe server --cloud-api https://YOUR-WORKER.YOUR-SUBDOMAIN.workers.dev
   ```

   Running the app without `--cloud-api` starts local mode, whose listings are
   not shared with other computers.

The `cloudflare\migrations` directory contains the D1 schema. The free service
has provider limits and is suitable only for testing; check Cloudflare's
current quotas before inviting users. Kudos are prototype credits, not money.
Do not use this prototype for valuable data or untrusted workloads.

## Local mode

Without `--cloud-api`, the desktop app uses its local API:

```powershell
python idlepool.py
```

To run an agent against the local API, start the desktop app first and then
use the command shown in **Cihazımı sat**.

For local testing on a single computer, start the server with
`--allow-self-rent` to let your account rent its own listed device:

```powershell
python idlepool.py server --allow-self-rent
```

This option is off by default and applies only to the local desktop server.
Busy devices remain unavailable. Use a separate test account and avoid
valuable data when testing remote commands.

## Build the Windows app

On Windows with Python 3.14 installed, run:

```powershell
.\build_exe.bat
```

The script creates or reuses `.venv`, installs `requirements.txt`, and writes
the standalone desktop executable to `dist\IdlePool.exe`.
