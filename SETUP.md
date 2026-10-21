# 🛠️ Setup Guide

Complete step-by-step instructions to get YAAS (youtube-agentic-ai-studio) running from scratch.

> **Time required:** ~10 minutes

---

## Prerequisites

- Python **3.10 or newer** — [python.org/downloads](https://www.python.org/downloads/)
- `ffmpeg` installed and on your PATH (see [Step 1b](#1b-install-ffmpeg))
- A Google account (for Gemini + YouTube)
- A Pexels account (free image API)

---

## Step 1 — Clone and install

### 1a. Clone the repository

```bash
git clone https://github.com/yourusername/youtube-ai-agent.git
cd youtube-ai-agent
```

### 1b. Install ffmpeg

ffmpeg is required by MoviePy for video rendering.

| OS | Command |
|---|---|
| **Windows** | Download from [ffmpeg.org](https://ffmpeg.org/download.html) → extract → add `bin/` folder to PATH |
| **Mac** | `brew install ffmpeg` |
| **Ubuntu/Debian** | `sudo apt install ffmpeg` |

Verify: `ffmpeg -version` should print version info.

### 1c. Install Python dependencies

```bash
pip install -r requirements.txt
```

> ⚠️ **Do not upgrade** `moviepy` or `numpy` — they are pinned intentionally. See [FAQ in README](README.md#-faq).

---

## Step 2 — Get your API keys

### 2a. Google Gemini API Key (required — free)

1. Go to [aistudio.google.com/apikey](https://aistudio.google.com/apikey)
2. Sign in with your Google account
3. Click **Create API Key**
4. Copy the key

### 2b. Pexels API Key (required — free)

1. Go to [pexels.com/api](https://www.pexels.com/api/)
2. Create a free account and click **Your API Key**
3. Copy the key

---

## Step 3 — Add your keys

All keys are optional. YAAS runs without any, but a Gemini key makes scripts much better.

### Option A — In the app (easiest)

Run `python gui.py`, click **Settings** (top right), paste your keys, and press **Save**.
They're written to a local `.env` file that git ignores.

### Option B — Edit `.env` yourself

```bash
cp .env.example .env
# open .env and fill in the keys you have
```

Real environment variables (`export GEMINI_API_KEY=...`) also work and take priority over `.env`.

> Never put keys in `config.py`. It's tracked by git, so they'd end up in your commits.

---

## Step 4 — Set up YouTube upload (optional)

Skip this step if you just want to generate videos locally without uploading.

### 4a. Create a Google Cloud project

1. Go to [console.cloud.google.com](https://console.cloud.google.com)
2. Click **Select a project → New Project**
3. Name it (e.g. "YAAS") and click **Create**

### 4b. Enable the YouTube Data API

1. In your new project, go to **APIs & Services → Library**
2. Search for **YouTube Data API v3**
3. Click **Enable**

### 4c. Create OAuth credentials

1. Go to **APIs & Services → Credentials**
2. Click **Create Credentials → OAuth client ID**
3. If prompted, configure the consent screen first:
   - User Type: **External**
   - Fill in app name, support email, and developer email
   - Scopes: click **Add or remove scopes** → search "youtube" → select `youtube.upload`
   - Test users: add your own Gmail address
4. Back in Credentials → **Create Credentials → OAuth client ID**
   - Application type: **Desktop app**
   - Name: anything (e.g. "YT Agent Desktop")
5. Click **Download JSON**
6. Rename the downloaded file to `client_secret.json`
7. Place it in the root of the project (replacing the placeholder)

### 4d. First-time authentication

On your first run, a browser window will open asking you to sign in with Google and grant permission. This only happens once — the token is saved locally as `youtube_token.pickle`.

> If you see a "Google hasn't verified this app" warning, click **Advanced → Go to (app name) (unsafe)**. This is expected for personal OAuth apps in testing mode.

---

## Step 5 — Customise your channel

In the app, open **Settings → Your channel** and set:

- **Channel name**, which is shown in your videos and thumbnails (switch it off or use custom text with *Show channel name in videos*)
- **What your channel is about**: the more specific, the better the AI's topics and scripts.
  Example: *"Educational science channel for curious adults, style of Veritasium."*

These are saved to `config.py` (`CHANNEL_NAME`, `CHANNEL_DESCRIPTION`, `SHOW_CHANNEL_BRAND`, `BRAND_TEXT`) if you prefer editing it directly.

---

## Step 6 — Launch

### GUI mode (recommended)

```bash
python gui.py
```

Opens at **http://localhost:7070**. Type a topic on Home and press **Make video**.

### CLI mode

```bash
python pipeline.py
```

Runs the full pipeline in the terminal. The review dashboard opens automatically in your browser when the video is ready.

---

## Step 7 — Add background music (optional)

Drop any `.mp3` or `.wav` files into the `music library/` folder.  
The pipeline will automatically shuffle and loop them to match the video duration.

The repo includes a few dark cinematic tracks to get you started. Replace or add to them freely.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `No module named 'moviepy'` | Run `pip install -r requirements.txt` again |
| `ffmpeg not found` | Install ffmpeg and ensure it's on your PATH |
| `RESOURCE_EXHAUSTED` / 429 | Gemini quota hit — wait until midnight Pacific or switch starting model |
| `youtube_token.pickle` auth error | Delete the file and re-run to re-authenticate |
| Tiny/ugly video text | Install DejaVu fonts: `sudo apt install fonts-dejavu` (Linux) |
| Flask port already in use | Change `REVIEW_PORT` in `config.py` |
| `client_secret.json` error | Ensure you replaced the placeholder with your real Google credentials |
| Captions out of sync | Switch to a different `VOICE_ID` in `config.py` (see voice list in file) |

---

## Optional: Run as a background service

To keep the studio running continuously (e.g. on a home server):

```bash
# Using nohup (Linux/Mac)
nohup python gui.py > studio.log 2>&1 &

# Using screen
screen -S yt-studio
python gui.py
# Detach: Ctrl+A then D
```

---

If you get stuck, [open an issue](https://github.com/yourusername/youtube-ai-agent/issues) with your error message and OS.


## Connect Canva inside YAAS

The integration uses **Canva for your platform (REST APIs)**, not a Canva editor
app or a plugin connection in ChatGPT. Follow the [official quickstart](https://www.canva.dev/docs/apps/quickstart/)
for current Developer Portal labels and account eligibility.

1. Open the Canva Developer Portal and create an app. In development, use an
   account authorized to test it; public distribution requires Canva's review.
2. Under **Outside Canva**, click **Start integrating**. Enable **Canva REST APIs**.
   If Canva requires MFA, enable it in Account and security first. Accounts using
   only Google sign-in may need to add a Canva password through Canva’s password
   reset flow before MFA becomes available. Complete password/MFA setup yourself.
3. In Configuration, generate a client secret and copy the client ID. Request only
   `design:meta:read` and `design:content:read` for this integration.
4. Register this redirect URL exactly:
   `http://127.0.0.1:7070/api/canva/callback`
   If you change the studio port, update both Canva and YAAS to match.
5. Launch YAAS and open **Scene Board → Set up the Canva connection**. Enter the
   client ID, secret, and registered redirect URL. Save, then click **Connect Canva**.
6. Approve the requested access on Canva. After returning to YAAS, search your
   designs, select a page number, choose PNG or MP4, and import it into a scene. For MP4, select portrait or
   landscape 1080p to match the design.

The browser uses the registered `127.0.0.1` origin for sign-in even if you opened
YAAS at `localhost`. Credentials are stored in the ignored `.env` file. OAuth
access/refresh tokens stay in the ignored `.canva_tokens.json` with owner-only
file permissions. Don't commit either file. Disconnect revokes Canva access and
removes the saved tokens.

Export jobs are polled without starting another export. After a browser reload,
use **Check existing export** to resume. A changed draft requires a fresh scene
selection; **Clear export selection** unlocks the picker without deleting anything
in Canva. Restarting the studio during sign-in requires connecting again.

Imports are limited to a single design page in PNG/MP4 format, up to 250 MB.
Unsupported formats and designs with inaccessible premium elements can fail;
YAAS reports the error rather than silently substituting a different asset.
Only existing-design exports are used—no Canva AI-generation calls.


## Optional: Cloudflare Tunnel and Access

For the current Flask app, keep the UI and backend on one hostname:

`https://yaas.learnwithsk.dev → Cloudflare Access → Tunnel → http://127.0.0.1:7070`

Cloudflare Pages can host the static HTML, but splitting it from the backend
requires additional API/media routing and authentication handling. A single
Tunnel hostname keeps the existing relative URLs and Canva sign-in working.

1. Create a Cloudflare Access self-hosted application for the **entire hostname**.
   Allow only your intended identity. Configure the tunnel to **Protect with Access**
   so the connector validates Access tokens before forwarding requests. Don't
   exempt API, file, or OAuth callback routes from the Access policy.
2. Run the origin bound only to `127.0.0.1`. Route the hostname through a named
   tunnel to `http://127.0.0.1:7070`; no inbound router port forwarding is needed.
3. In the local `.env`, set `STUDIO_BEHIND_PROXY=1` and a persistent random
   `STUDIO_SESSION_SECRET` of at least 32 characters. This trusts one proxy's
   forwarded HTTPS scheme and uses secure session cookies. Enable this mode only
   behind the trusted local connector; it is **not** an Access token validator.
4. Set `CANVA_REDIRECT_URI=https://yaas.learnwithsk.dev/api/canva/callback` in YAAS
   and register that exact HTTPS URL in the Canva Developer Portal.
5. Bypass caching for this private studio hostname. Use one application process with multiple threads for remote operation. The
   current run state and progress events are held in memory, so multiple worker
   processes are not supported. Use a production WSGI server rather than Flask's
   development server for ongoing hosted use.
6. Verify Access blocks unauthorized requests to HTML, APIs, and video files;
   then test Canva sign-in, progress events, uploads, and video seeking through
   the hostname. Cloudflare's plan-specific request-size limits also apply.

The Mac must remain awake, online, and running the origin and tunnel. Media,
credentials, and rendering remain on the Mac. This is a single-user studio, not
an isolated multi-user service. Publishing DNS, creating the Access policy, and
starting a tunnel are separate deployment steps; this repository change does
not publish the app.

Official guides: [Cloudflare Tunnel](https://developers.cloudflare.com/tunnel/get-started/)
and [Access self-hosted applications](https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/self-hosted-public-app/).
