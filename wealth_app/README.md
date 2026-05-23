# 💼 Wealth Dashboard — Cloud Deployment

Deploys to Render (free). Reads live from your Google Sheet.
Works on iPhone, Android, desktop — any browser, anywhere.

---

## Step 1 — Create a Service Account (Google Cloud, ~3 min)

1. Go to https://console.cloud.google.com
2. Select your existing project (the one you used before)
3. **APIs & Services → Credentials → + Create Credentials → Service account**
   - Name: `wealth-dashboard` → Create
   - Skip optional steps → Done
4. Click the service account email you just created
5. **Keys tab → Add Key → Create new key → JSON → Create**
   - A `.json` file downloads — keep it safe, you'll need it soon
6. Back in **APIs & Services → Enabled APIs**, confirm these are on:
   - Google Sheets API ✅
   - Google Drive API ✅

---

## Step 2 — Share your sheet with the service account

1. Open the downloaded JSON file, find the `client_email` field — looks like:
   `wealth-dashboard@your-project.iam.gserviceaccount.com`
2. Open your Google Sheet
3. Click **Share** → paste that email → set to **Viewer** → Share

---

## Step 3 — Push to GitHub

1. Create a new **private** GitHub repo called `wealth-dashboard`
2. Push this folder to it:
   ```bash
   cd wealth_app
   git init
   git add .
   git commit -m "Initial deploy"
   git remote add origin https://github.com/YOUR_USERNAME/wealth-dashboard.git
   git push -u origin main
   ```

---

## Step 4 — Deploy on Render

1. Go to https://render.com → Sign up (free)
2. **New → Web Service → Connect your GitHub repo**
3. Settings:
   - **Name**: wealth-dashboard (or anything)
   - **Runtime**: Python
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `gunicorn app:server --bind 0.0.0.0:$PORT --workers 1 --timeout 120`
   - **Plan**: Free
4. **Environment Variables → Add:**
   - Key: `GOOGLE_SERVICE_ACCOUNT_JSON`
   - Value: paste the **entire contents** of the JSON file you downloaded
5. Click **Create Web Service**

Render builds and deploys (~2 min). You get a URL like:
`https://wealth-dashboard-xxxx.onrender.com`

---

## Step 5 — Add to iPhone Home Screen

1. Open Safari on your iPhone
2. Go to your Render URL
3. Tap the **Share** button (box with arrow)
4. Tap **"Add to Home Screen"**
5. Name it "Wealth" → Add

It now appears as an app icon on your home screen. Tap it anytime to see your live dashboard.

---

## Updating data

Just update your Google Sheet as normal. Tap **🔄** in the dashboard to refresh.

---

## Notes

- Free Render tier spins down after 15 min of inactivity — first load may take ~30 sec to wake up
- The service account JSON contains a private key — never commit it to git (it's in env vars, not code)
- To upgrade to always-on: Render paid plan ($7/mo) or use Railway.app (similar, free tier)
