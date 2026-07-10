# Deploying the web app

Two supported targets: **Railway** (Procfile included) and **Streamlit
Community Cloud** (free). Both give you a public URL to send to founders.

---

## Option A — Railway

The repo already contains everything Railway needs: `Procfile` (runs the
FastAPI product — `web/` + `server/api.py` — via uvicorn, binding
`0.0.0.0:$PORT`), pinned `requirements.txt`, and `.python-version` (3.12).

1. Push the repo to GitHub (see the git commands at the bottom of this file).
2. In Railway: **New Project → Deploy from GitHub repo** → pick the repo.
   Nixpacks auto-detects Python and uses the `Procfile` start command.
3. In the service's **Variables** tab add:

   ```
   ANTHROPIC_API_KEY=<your-anthropic-api-key>
   CLERK_PUBLISHABLE_KEY=<your-clerk-publishable-key>
   CLERK_SECRET_KEY=<your-clerk-secret-key>
   ```

   The app reads the environment first, so no secrets file is needed. Without
   the Clerk keys, auth is disabled and the app runs unprotected — set them for
   any real deployment.
4. **Settings → Networking → Generate Domain** to get your public URL.

Notes for Railway:
- **Playwright browsers are NOT installed** by default; the app automatically
  falls back to fast HTTP fetching (expected, documented behavior). If you want
  JS rendering, add a custom build command
  `pip install -r requirements.txt && playwright install --with-deps chromium`
  and use at least a 1 GB memory service — otherwise skip it.
- Set a **spend limit** on your Anthropic key before sharing the URL: the app
  has a per-session cap but no global auth/rate limiting yet.

---

## Option B — Streamlit Community Cloud (free)

This turns `streamlit_app.py` into a public URL like
`https://your-app-name.streamlit.app`.

## What you need
- A **GitHub account** (the code must live in a GitHub repo).
- A **Streamlit Community Cloud account** — sign in at
  <https://share.streamlit.io> with that same GitHub account (free).
- Your **Anthropic API key**.

---

## Step 1 — Run it locally first (optional but recommended)

```bash
# from the project root, with the venv active and your key in .env
pip install -r requirements.txt
streamlit run streamlit_app.py
```
It opens at <http://localhost:8501>. The key is read from your existing `.env`.

---

## Step 2 — Push the repo to GitHub

The repo is already a git repo with a v1.0.0 commit. Create an **empty** GitHub
repo (no README), then:

```bash
git remote add origin https://github.com/<your-username>/<your-repo>.git
git push -u origin master
```

> ✅ Your secrets are safe: `.env` and `.streamlit/secrets.toml` are git-ignored,
> so the key is **not** in what you push. (Verify with `git ls-files | grep -E "\.env$|secrets\.toml$"` — it should print nothing.)

---

## Step 3 — Create the app on Streamlit Cloud

1. Go to <https://share.streamlit.io> → **Create app** → **Deploy a public app from GitHub**.
2. Select your repo, branch **`master`**, and main file **`streamlit_app.py`**.
3. (Optional) **Advanced settings** → Python version **3.12**.
4. Click **Deploy**. The first build takes a couple of minutes.

---

## Step 4 — Add your API key as a secret (this is the important part)

The app will start but show *"This app isn't configured yet"* until you add the
key. Do **not** put the key in the code.

1. In your app's page, open **⋮ → Settings → Secrets** (or **Advanced settings →
   Secrets** during deploy).
2. Paste exactly this (TOML format), with your real key:

   ```toml
   ANTHROPIC_API_KEY = "<your-anthropic-api-key>"
   ```

3. **Save.** The app restarts and reads the key from `st.secrets` — it is never
   shown to users and never logged.

Your shareable URL is now live. Send it to founders. 🎉

---

## Costs & abuse (read before sharing widely)

- **Every generation calls Claude**, which costs you money on your Anthropic key.
- Built-in guards: results are **cached per URL** (re-runs are free) and each
  browser session is capped at **15 generations**.
- Still, a public link can be hit by anyone. Recommended:
  - Set a **monthly spend limit** in the Anthropic console
    (<https://console.anthropic.com> → Billing → Limits).
  - Watch usage; if it's abused, unshare the link or add a password
    (Streamlit Cloud lets you make the app private, or add a simple gate).

## Note on JavaScript-heavy sites

Streamlit Cloud does **not** install the Playwright browser, so the app
automatically falls back to fast HTTP fetching. Most sites work great; a few
heavily JavaScript-rendered sites may yield thinner research. This is expected
and safe — installing a headless browser on the free tier is not recommended
(it can exceed the memory limit). Full rendering still works when you run
locally with `python -m playwright install chromium`.
