# Odysseus Setup Log

Running history of what was set up on this machine (office/work PC, personal
device — not IT-managed), why, and the decisions behind it. Newest entries
at the bottom.

---

## 2026-09-01 — Initial install

Installed via Docker Compose from the **official** repo, not the `odysseusai.dev`
site (that's an unofficial, ad-monetized SEO guide, not affiliated with the
project — flagging in case it resurfaces in search results later).

- Source: https://github.com/odysseus-dev/odysseus (formerly under
  `pewdiepie-archdaemon`, moved orgs; 86k+ stars, AGPL-3.0, actively maintained)
- Cloned into `c:\Projects\Odysseusai`
- `cp .env.example .env`
- `docker compose up -d --build`
- Containers: `odysseus` (app), `chromadb` (vector memory), `searxng` (search),
  `ntfy` (notifications)
- App reachable at `http://localhost:7000`
- Admin user `admin` created with a temporary password from container logs —
  changed after first login.

## 2026-09-02 — GPU passthrough (NVIDIA RTX 5070)

**Why:** plan to run local models (Qwen2.5-7B-Instruct) via Cookbook, in
addition to the Anthropic API for chat.

- Ran `scripts/check-docker-gpu.sh` (read-only diagnostic) — passthrough
  confirmed working.
- Enabled the NVIDIA compose overlay via
  `scripts/check-docker-gpu.sh --enable-nvidia-overlay --yes`, which set:
  ```
  COMPOSE_FILE=docker-compose.yml:docker/gpu.nvidia.yml
  ```
- **Bug hit:** Docker Compose failed to parse that value on Windows —
  `:` collides with drive-letter syntax in Windows paths. Fixed by switching
  to `;` as the separator (the `.env.example` template already documents this
  Windows case):
  ```
  COMPOSE_FILE=docker-compose.yml;docker/gpu.nvidia.yml
  ```
- Rebuilt (`docker compose up -d --build`) and verified with
  `docker exec odysseusai-odysseus-1 nvidia-smi` — GPU visible inside the
  container.
- **Note for later:** GPU passthrough only makes the GPU visible to the
  container. Actually serving a model on it still requires installing a
  CUDA-enabled engine via Cookbook → Dependencies (e.g. llama.cpp with CUDA).
  Passthrough working ≠ inference using the GPU yet.

## 2026-09-02 — Anthropic API billing error

**Symptom:** `HTTP 400: Your credit balance is too low`, despite the claude.ai
account showing a positive balance.

**Root cause:** claude.ai subscription "usage credits" (Pro/Max plan overage)
and the **Anthropic API** billing balance are two separate systems. The API
key used in Odysseus draws from the API console balance
(console.anthropic.com → Plans & Billing), not the claude.ai subscription.

**Fix:** added a payment method and purchased API credits directly in the
API console. No changes needed on the Odysseus side once that balance was
positive.

## 2026-09-02 — Multi-machine access (office + home) via Tailscale

**Goal:** use the same Odysseus instance (same chats, memory, tasks, notes)
from both the office PC and a home PC.

**Decision: one shared instance, not two separate installs.**
All state (SQLite DB, ChromaDB, uploaded files under `./data`) lives on a
single machine with no built-in sync between installs. Running Odysseus a
second time on the home PC would just create a second, disconnected dataset.
Splitting the backend across two instances (shared Postgres/ChromaDB) was
considered and rejected — unsupported by the project, and risks state
corruption from two processes writing concurrently.

**Chosen host:** this machine (office PC), since it already has Odysseus
installed and GPU-configured. Confirmed with the user this PC is a *personal*
device at the office, not IT-managed — otherwise this would not be
recommended (IT-managed machines can be restarted/reimaged/network-restricted
without notice, and mixing personal AI memory/email/tasks into a managed
corporate asset is a bad idea regardless).

**Networking: Tailscale**, a private mesh VPN, instead of exposing anything
to the public internet or doing router port-forwarding.
- Installed on this machine (tailnet: `samisan2007.github`)
- Tailscale IPv4 of this machine: `100.124.175.65`
- Home PC needs Tailscale installed and signed into the same account to join
  the tailnet.

**Reachability change:** set `APP_BIND=0.0.0.0` in `.env` (was `127.0.0.1`).
This makes Docker's port mapping listen on *all* host network interfaces —
including the office LAN/Wi-Fi adapter, not just Tailscale's virtual adapter.
Compose has no per-interface bind option, so this is an unavoidable
side-effect of making it reachable over Tailscale at all.

**Mitigation for the LAN side-effect:** two Windows Firewall inbound rules,
scoped by IP range, so only Tailscale traffic can actually reach port 7000
even though Docker is listening more broadly:
```powershell
New-NetFirewallRule -DisplayName "Odysseus (Tailscale only)" -Direction Inbound -Protocol TCP -LocalPort 7000 -RemoteAddress 100.64.0.0/10 -Action Allow
New-NetFirewallRule -DisplayName "Odysseus (block LAN)" -Direction Inbound -Protocol TCP -LocalPort 7000 -RemoteAddress Any -Action Block
```
(`100.64.0.0/10` is Tailscale's CGNAT-based private IP range.)

**Verified:**
- `docker compose up -d` applied the new bind; port mapping confirmed as
  `0.0.0.0:7000->7000/tcp`.
- `http://localhost:7000` → HTTP 302 to `/login` (still works locally).
- `http://100.124.175.65:7000` → HTTP 302 to `/login` (now reachable over
  Tailscale).

**Still to do:**
- Install Tailscale on the home PC, sign into the same account.
- From home, open `http://100.124.175.65:7000`.
- Disable sleep on this machine (Settings → Power) so it stays reachable when
  unattended; lock with Win+L instead of signing out — locking keeps the
  session (and Docker containers) running, signing out would stop Docker
  Desktop since it runs per-user, not as a system service.
- Docker Desktop → enable "Start Docker Desktop when you log in" so
  containers (which already have `restart: unless-stopped`) come back after
  a reboot.
- Optional: HTTPS via Caddy + `tailscale cert` for clipboard/HTTP2 support
  (see `website/setup.md`, "HTTPS + LAN/Tailscale exposure").

## 2026-09-02 — Phone access from campus network: unreliable

Tried reaching Odysseus from a phone on Tailscale while both devices were on
the university (workplace) network.

- `tailscale status` on the PC showed the phone as `active; direct` with a
  real public endpoint — the WireGuard tunnel itself was establishing fine,
  which argues against the campus network fully blocking Tailscale.
- Confirmed server-side health throughout: `0.0.0.0:7000` listening, firewall
  rules correctly scoped (verified via `Get-NetFirewallAddressFilter`).
- Symptoms from the phone: `ERR_NETWORK_CHANGED` (Chrome) on one attempt,
  generic "can't be reached" on another.
- Leading theory: Android battery optimization throttling the Tailscale app
  in the background, causing the VPN tunnel to restart mid-request. Advised
  setting Tailscale to "Unrestricted" battery usage on the phone.
- Secondary possibility not ruled out: campus network client isolation or
  DPI interfering with the tunnel after it's established, even though initial
  connection succeeds.
- **Decision:** test from home network (much less restrictive) to get a clean
  baseline before concluding it's a campus-network issue vs. a local config
  issue. Not yet resolved — revisit after the home-network test.

## 2026-09-02 — One-click start/stop (no terminal needed)

**Why:** running `docker compose up -d` from a terminal every time is
friction the user doesn't want day-to-day — wanted a normal
double-click-to-launch experience.

Created two scripts in the project root plus Desktop shortcuts pointing to
them:

- `start-odysseus.bat` — checks if Docker Desktop is running (launches it and
  waits if not), runs `docker compose up -d`, polls `http://localhost:7000`
  until it actually responds (not just "containers started" — waits for the
  app to be ready), then opens it in the default browser.
- `stop-odysseus.bat` — `docker compose stop`. Deliberately `stop`, not
  `down`: keeps containers/network/volumes intact for a fast restart rather
  than tearing them down and recreating on next start.
- Desktop shortcuts: **Start Odysseus** / **Stop Odysseus**
  (`C:\Users\samisan\Desktop\`), targeting the two `.bat` files above.

Tested end-to-end from a stopped state: Docker check → containers up →
`0.0.0.0:7000` binding preserved (still Tailscale-reachable) → HTTP 302
confirmed → browser opened automatically. Works.
