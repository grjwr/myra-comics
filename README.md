# 📖 Myra Comics

A web app that lets a child **tell a story by voice** — in Hindi, English, or a mix of both — and turns it into a **colourful comic book** with captions in English, Hindi, or both.

Built with Streamlit and runs **entirely on free services**.

---

## ✨ Features

- 🎤 **Voice input** — speak the story in Hindi, English or Hinglish; the app writes it down (Hindi in Devanagari).
- ✏️ **Fix before writing** — the transcribed text can be corrected or typed directly.
- 📝 **Bilingual story** — the idea is tidied into a simple, child-friendly story in **English and Hindi**, keeping the child's own characters and plot.
- 🎨 **Comic panels** — 4 to 8 colourful comic-style pictures, one per scene.
- 🔁 **Redraw** any single picture, or draw only the missing ones.
- 🌐 **Caption language** — English, हिंदी, or both.
- 📂 **Start new or continue** — the start screen offers a new story or opening a saved one.
- 🪄 **Change a saved story by voice** — e.g. *"make the cat orange"*, *"add a happy ending"*. Unchanged pictures are kept.
- ⬇️ **PDF download** — comic pages plus the full story at the end, with correct Hindi text rendering.
- 💾 **Save story file** (`.story.json`) — includes text and pictures, to reopen and edit later.
- 🔗 **Share link** — one-tap sharing on WhatsApp or email (optional).
- 🔒 **Family password** — keeps strangers from using the app.
- 🛡️ **Child-safe prompts** — content is kept gentle and age-appropriate.

---

## 🧱 Tech stack (all free)

| Part | Service |
|---|---|
| Web app & hosting | [Streamlit](https://streamlit.io) on Streamlit Community Cloud |
| Voice → text, story writing | Google Gemini API (free tier) |
| Comic pictures | Cloudflare Workers AI — FLUX.1 Schnell (free daily allowance) |
| Share links | Supabase Storage (free tier, optional) |
| PDF | fpdf2 + HarfBuzz text shaping, Noto Sans fonts |

---

## 📁 Project structure

```
kahani-comic/
├── app.py              # The whole Streamlit app
├── requirements.txt    # Python libraries
├── README.md
└── fonts/              # Needed for Hindi in the PDF — do not delete
    ├── NotoSans-Regular.ttf
    ├── NotoSans-Bold.ttf
    ├── NotoSansDevanagari-Regular.ttf
    └── NotoSansDevanagari-Bold.ttf
```

---

## 🔑 Secrets

The app reads its keys from Streamlit secrets. **Never commit keys to GitHub.**

```toml
# Required
GEMINI_API_KEY = "AIza..."              # Google AI Studio
CF_ACCOUNT_ID  = "..."                  # Cloudflare account ID
CF_API_TOKEN   = "..."                  # Cloudflare "Workers AI" API token

# Recommended
APP_PASSWORD   = "family-password"      # leave out to disable the password screen

# Optional — share links
SUPABASE_URL    = "https://xxxx.supabase.co"
SUPABASE_KEY    = "..."                 # service_role / secret key
SUPABASE_BUCKET = "comics"              # default: comics

# Optional — model overrides (if Google renames/retires a model)
TEXT_MODEL      = "gemini-3.6-flash"
CF_IMAGE_MODEL  = "@cf/black-forest-labs/flux-1-schnell"
```

If `CF_ACCOUNT_ID` / `CF_API_TOKEN` are not set, the app falls back to Gemini image generation, which **requires a paid Gemini account**.

---

## 🚀 Setup

### 1. Gemini API key
1. Go to [aistudio.google.com/apikey](https://aistudio.google.com/apikey) and sign in.
2. **Create API key** (create a new project if asked).
3. Do **not** enable billing — the free tier is enough.

### 2. Cloudflare (pictures)
1. Sign up free at [dash.cloudflare.com/sign-up](https://dash.cloudflare.com/sign-up) — no card needed.
2. **Workers & Pages** → copy the **Account ID** (right side).
3. Profile icon → **My Profile → API Tokens → Create Token** → **Workers AI** template → **Create Token**. Copy it (shown once).

### 3. Supabase (optional, for share links)
1. In your Supabase project → **Storage → New bucket** → name `comics`, **Public ON**.
2. **Project Settings → API** → copy the **Project URL** and the **secret / service_role key**.

### 4. Deploy on Streamlit Community Cloud
1. Go to [share.streamlit.io](https://share.streamlit.io) and sign in with GitHub.
2. **Create app → Deploy a public app from GitHub**.
3. Repository `grjwr/kahani-comic`, branch `main`, main file `app.py`.
4. **Advanced settings** → Python 3.12 → paste the secrets above → **Deploy**.

### Run locally (optional)
```bash
pip install -r requirements.txt
mkdir -p .streamlit
# put your secrets in .streamlit/secrets.toml (this file must NOT be committed)
streamlit run app.py
```
Add `.streamlit/secrets.toml` to `.gitignore`.

---

## 🧒 How to use

1. **Start a new story** (or open a saved `.story.json` file to change one).
2. Tap the 🎤 mic, tell the story, then **Turn my voice into words**. Fix any words.
3. Choose the number of pictures → **Write my story**.
4. **Draw missing pictures** → redraw any you don't like.
5. **Download** the PDF, **Save my story** for later, or **Make a share link**.

---

## 💰 Free-tier limits

- **Pictures:** about 100 per day on Cloudflare's free allowance. Resets daily at 00:00 UTC (5:30 AM IST). The free plan stops at the limit — it never charges.
- **Gemini:** free-tier requests-per-minute/day limits apply. If a model is busy (503), the app retries and falls back to other free Gemini models automatically.
- **Supabase:** 1 GB free storage; each comic PDF is roughly 1–2 MB.
- **Streamlit:** free apps sleep after a few days without use — click *"Yes, get this app back up"* to wake it.

---

## 🔐 Privacy notes

- On Gemini's free tier, Google may use submitted content (including voice recordings) to improve its products. Avoid personal details such as full names, school or address in stories.
- Share links are unguessable but **public to anyone who has the link** — share only with family and friends.
- Saved story files stay on your own device; nothing is stored on the server unless you create a share link.

---

## 🛠️ Troubleshooting

| Problem | Fix |
|---|---|
| `503 UNAVAILABLE` / "very busy" | Google's servers are overloaded — wait a minute and try again. |
| `404` / model not found | A model was renamed or retired — set `TEXT_MODEL` or `CF_IMAGE_MODEL` in secrets to a current name. |
| "Today's free pictures are used up" | Daily Cloudflare limit reached — try again after 5:30 AM IST. |
| Mic doesn't record | Allow microphone permission for the site in the browser settings. |
| Hindi looks broken in the PDF | Make sure the `fonts/` folder with all four `.ttf` files is in the repo. |
| "Could not make the link" | Bucket must be named `comics` and set to **Public**; `SUPABASE_URL` without a trailing `/`. |

---

## 📄 Credits

- Fonts: [Noto Sans](https://fonts.google.com/noto) and Noto Sans Devanagari by Google, licensed under the SIL Open Font License 1.1.
- Built by Rajiv Kumar ([@grjwr](https://github.com/grjwr)) for his daughter. ❤️
