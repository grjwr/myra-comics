"""
Myra Comics
Speak a story (Hindi / English / Hinglish) -> story in English + Hindi -> colourful comic book.
Start a new story, or open a saved story file and change it.
Uses Google Gemini for speech understanding, story writing and panel drawing.
"""
import base64
import hashlib
import io
import json
import re
import time
import uuid
from pathlib import Path
from urllib.parse import quote

import requests
import streamlit as st
from fpdf import FPDF
from PIL import Image
from google import genai
from google.genai import types

st.set_page_config(page_title="Myra Comics", page_icon="📖", layout="wide")

# ---------------- Settings (from Streamlit secrets) ----------------
API_KEY = st.secrets.get("GEMINI_API_KEY", "")
APP_PASSWORD = st.secrets.get("APP_PASSWORD", "")
TEXT_MODEL = st.secrets.get("TEXT_MODEL", "gemini-3.6-flash")
# Other free models to try if the main one is busy (503) or its free daily limit is used up (429)
FALLBACK_MODELS = ["gemini-3.5-flash", "gemini-3.1-flash-lite", "gemini-3.5-flash-lite"]
IMAGE_MODEL = st.secrets.get("IMAGE_MODEL", "gemini-3.1-flash-image")  # only used if IMAGE_PROVIDER = "gemini" (paid)
# Free pictures: Cloudflare Workers AI (10,000 free neurons/day, no card needed)
CF_ACCOUNT_ID = st.secrets.get("CF_ACCOUNT_ID", "")
CF_API_TOKEN = st.secrets.get("CF_API_TOKEN", "")
CF_IMAGE_MODEL = st.secrets.get("CF_IMAGE_MODEL", "@cf/black-forest-labs/flux-1-schnell")
IMAGE_PROVIDER = st.secrets.get("IMAGE_PROVIDER", "cloudflare")
# Optional: share links (Supabase Storage). Leave these out and the app still works, just without links.
SUPABASE_URL = st.secrets.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = st.secrets.get("SUPABASE_KEY", "")
SUPABASE_BUCKET = st.secrets.get("SUPABASE_BUCKET", "comics")
FONT_DIR = Path(__file__).parent / "fonts"

SYSTEM = (
    "You help a young child turn her own spoken story into a comic book. "
    "She may speak Hindi, English, or a mix of both. "
    "Keep everything gentle and suitable for young children: no gore, nothing frightening "
    "beyond mild adventure, no romance, no unkind language. "
    "Keep HER characters, ideas and plot. Tidy them up and fill small gaps, but never replace her story. "
    "Use simple words a young reader can follow."
)

STYLE = (
    "A bright, colourful children's comic book panel. Bold clean outlines, flat vibrant colours, "
    "friendly expressive faces, simple uncluttered backgrounds, cheerful mood. "
    "IMPORTANT: no text, no letters, no speech bubbles and no captions anywhere in the image."
)

STORY_FORMAT = """{
  "title_en": "English title",
  "title_hi": "Hindi title in Devanagari",
  "story_en": "The full story in simple English, a few short paragraphs",
  "story_hi": "The same story in simple Hindi (Devanagari)",
  "characters": [
    {"name": "character name", "look": "fixed visual description in English: species/age, hair, clothes and colours"}
  ],
  "panels": [
    {
      "scene": "English description of what to DRAW in this panel: who, where, action, expressions",
      "caption_en": "One or two short English sentences for this panel",
      "caption_hi": "The same caption in Hindi (Devanagari)"
    }
  ]
}"""

# ---------------- Password gate ----------------
if APP_PASSWORD and not st.session_state.get("unlocked"):
    st.title("📖 Myra Comics")
    pw = st.text_input("Family password", type="password")
    if st.button("Enter"):
        if pw == APP_PASSWORD:
            st.session_state.unlocked = True
            st.rerun()
        else:
            st.error("That password is not right.")
    st.stop()

if not API_KEY:
    st.error("GEMINI_API_KEY is missing. Add it in Streamlit secrets.")
    st.stop()


@st.cache_resource
def get_client():
    return genai.Client(api_key=API_KEY)


client = get_client()
ss = st.session_state
ss.setdefault("mode", None)      # None = start screen, "new", "edit"
ss.setdefault("story", None)
ss.setdefault("images", [])      # list of (bytes, mime) or None, one per panel
ss.setdefault("rev", 0)          # bumps whenever the story is replaced (refreshes edit boxes)


# ---------------- AI helpers ----------------
def parse_json(text: str) -> dict:
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text[text.find("{"):]
    return json.loads(text)


def _is_temporary(e: Exception) -> bool:
    code = getattr(e, "code", None) or getattr(e, "status_code", None)
    text = str(e)
    return code in (429, 500, 503, 504) or any(
        k in text for k in ("503", "UNAVAILABLE", "429", "RESOURCE_EXHAUSTED", "overloaded", "high demand")
    )


def _is_model_missing(e: Exception) -> bool:
    return getattr(e, "code", None) == 404 or "NOT_FOUND" in str(e)


def ask_json(prompt, extra_parts=None) -> dict:
    """Ask Gemini for JSON. If a model is busy or out of free quota, try the other free models."""
    contents = (extra_parts or []) + [prompt]
    models = [TEXT_MODEL] + [m for m in FALLBACK_MODELS if m != TEXT_MODEL]
    last_error = None
    for model in models:
        for attempt in range(3):          # busy model: try again after 3s, then 6s
            if attempt:
                time.sleep(3 * attempt)
            try:
                resp = client.models.generate_content(
                    model=model,
                    contents=contents,
                    config=types.GenerateContentConfig(
                        system_instruction=SYSTEM, response_mime_type="application/json"
                    ),
                )
                return parse_json(resp.text)
            except json.JSONDecodeError as e:
                last_error = e
            except Exception as e:
                last_error = e
                if _is_model_missing(e):
                    break
                if not _is_temporary(e):
                    raise
                if "RESOURCE_EXHAUSTED" in str(e) or getattr(e, "code", None) == 429:
                    break   # this model's free limit is used up: go straight to the next model
    raise RuntimeError(
        "Google's free AI is busy or today's free limit is used up on all models. "
        f"Please try again later. (Last error: {last_error})"
    )


def transcribe(audio_bytes: bytes, mime: str) -> str:
    prompt = (
        "Listen to this child speaking. Write down exactly what she says. "
        "Hindi words go in Devanagari script, English words in English. "
        "Only fix obvious mishearings. Return JSON: {\"transcript\": \"...\"}"
    )
    return ask_json(prompt, [types.Part.from_bytes(data=audio_bytes, mime_type=mime)])["transcript"]


def write_story(idea: str, n_panels: int) -> dict:
    prompt = f"""Here is the child's story idea (may be Hindi, English or mixed):

\"\"\"{idea}\"\"\"

Write it up as a short illustrated story and return ONLY this JSON:
{STORY_FORMAT}
Make exactly {n_panels} panels that tell the whole story from beginning to end."""
    return ask_json(prompt)


def revise_story(story: dict, change: str) -> dict:
    prompt = f"""Here is the child's current story as JSON:
{json.dumps(story, ensure_ascii=False)}

She wants these changes (may be Hindi, English or mixed):
\"\"\"{change}\"\"\"

Apply her changes and return the complete updated story in exactly this JSON format:
{STORY_FORMAT}
Keep everything she did not ask to change exactly as it is, including the "scene" text of unchanged panels
and the "look" of unchanged characters. Add or remove panels only if she asks (keep between 4 and 8)."""
    return ask_json(prompt)


def extract_image(resp):
    for cand in resp.candidates or []:
        if not cand.content or not cand.content.parts:
            continue
        for part in cand.content.parts:
            if getattr(part, "inline_data", None) and part.inline_data.data:
                return part.inline_data.data, (part.inline_data.mime_type or "image/png")
    return None


def _cf_errors(r) -> str:
    try:
        errs = r.json().get("errors") or []
        return "; ".join(f"{e.get('code')}: {e.get('message')}" for e in errs) or r.text[:300]
    except Exception:
        return r.text[:300]


def draw_panel_cloudflare(prompt: str, simple_prompt: str):
    if not (CF_ACCOUNT_ID and CF_API_TOKEN):
        raise RuntimeError(
            "Cloudflare keys are missing. In Streamlit → Settings → Secrets add "
            "CF_ACCOUNT_ID and CF_API_TOKEN (spelled exactly like that)."
        )
    url = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID.strip()}/ai/run/{CF_IMAGE_MODEL}"
    headers = {"Authorization": f"Bearer {CF_API_TOKEN.strip()}"}
    last = ""
    for attempt in range(4):
        text = prompt if attempt < 2 else simple_prompt   # later tries use a shorter, simpler prompt
        if attempt:
            time.sleep(2 * attempt)
        try:
            r = requests.post(url, headers=headers, json={"prompt": text[:2000], "steps": 8}, timeout=120)
        except requests.RequestException as e:
            last = f"network problem: {e}"
            continue
        if r.ok:
            try:
                img_b64 = (r.json().get("result") or {}).get("image")
            except ValueError:
                img_b64 = None
            if img_b64:
                return base64.b64decode(img_b64), "image/jpeg"
            last = f"no picture in reply: {_cf_errors(r)}"
            continue
        err = _cf_errors(r)
        low = err.lower()
        if r.status_code in (401, 403) or "authentication" in low or "10000" in low:
            raise RuntimeError(
                "Cloudflare did not accept the API token. Create a new token with the "
                f"'Workers AI' template and put it in CF_API_TOKEN. (Cloudflare said: {err})"
            )
        if r.status_code == 404 or "could not route" in low or "7003" in low:
            raise RuntimeError(
                "Cloudflare could not find your account or the picture model. Check CF_ACCOUNT_ID "
                f"(32 letters/numbers from the dashboard). (Cloudflare said: {err})"
            )
        if r.status_code == 429 or "neuron" in low or "daily" in low:
            raise RuntimeError(
                "Today's free pictures are used up. Try again tomorrow (resets 5:30 AM India time). "
                f"(Cloudflare said: {err})"
            )
        last = f"HTTP {r.status_code}: {err}"   # busy / safety filter / other: try again
    raise RuntimeError(f"Cloudflare could not draw this picture after 4 tries. ({last})")


def draw_panel(story: dict, panel: dict, reference=None):
    chars = "\n".join(f"- {c['name']}: {c['look']}" for c in story.get("characters", []))
    prompt = (
        f"{STYLE}\n\nCharacters (keep them looking exactly like this):\n{chars}\n\n"
        f"Scene to draw: {panel['scene']}"
    )
    if IMAGE_PROVIDER == "cloudflare":
        simple = f"{STYLE}\n\nScene: {panel['scene']}"
        return draw_panel_cloudflare(prompt, simple)
    contents = [prompt]
    if reference:
        contents.append(types.Part.from_bytes(data=reference[0], mime_type=reference[1]))
        contents.append("Keep the characters' design, colours and art style the same as in this reference panel.")
    resp = client.models.generate_content(
        model=IMAGE_MODEL,
        contents=contents,
        config=types.GenerateContentConfig(response_modalities=["TEXT", "IMAGE"]),
    )
    return extract_image(resp)


# ---------------- State helpers ----------------
def set_story(new_story: dict, images: list):
    ss.story = new_story
    ss.images = images
    ss.rev += 1


def fit_images():
    n = len(ss.story["panels"])
    ss.images = (ss.images + [None] * n)[:n]


def reference_for(i):
    return next((im for j, im in enumerate(ss.images) if im and j != i), None)


def draw_pictures(only_missing: bool):
    story = ss.story
    fit_images()
    if not only_missing:
        ss.images = [None] * len(story["panels"])
    todo = [i for i, im in enumerate(ss.images) if not im]
    if not todo:
        st.info("All pictures are already drawn! Use 🔁 Redraw under a picture to change it.")
        return
    ss.draw_errors = []
    bar = st.progress(0.0, text="Drawing...")
    for n, i in enumerate(todo):
        bar.progress(n / len(todo), text=f"Drawing picture {i + 1} ({n + 1} of {len(todo)})...")
        try:
            ss.images[i] = draw_panel(story, story["panels"][i], reference_for(i))
        except Exception as e:
            ss.draw_errors.append(f"Picture {i + 1}: {e}")
            if "missing" in str(e) or "token" in str(e) or "used up" in str(e) or "account" in str(e):
                break   # same problem for every picture: stop early
    bar.progress(1.0, text="Done! 🎉")


def go_home():
    for k in ["mode", "story", "images", "idea", "change", "share"]:
        ss.pop(k, None)


def story_file() -> str:
    return json.dumps(
        {
            "app": "myra-comics",
            "version": 1,
            "idea": ss.get("idea", ""),
            "story": ss.story,
            "images": [
                {"mime": im[1], "data": base64.b64encode(im[0]).decode()} if im else None
                for im in ss.images
            ],
        },
        ensure_ascii=False,
    )


def load_story_file(raw: bytes):
    data = json.loads(raw.decode("utf-8"))
    story = data["story"]
    if not story.get("panels"):
        raise ValueError("no panels")
    images = [(base64.b64decode(im["data"]), im["mime"]) if im else None for im in data.get("images", [])]
    ss["idea"] = data.get("idea", "")
    set_story(story, images)
    fit_images()


def file_name() -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", ss.story.get("title_en", "my_story").lower()).strip("_")
    return slug or "my_story"


# ---------------- Display helpers ----------------
def captions_for(panel: dict, lang: str):
    if lang == "English":
        return [panel["caption_en"]]
    if lang == "हिंदी":
        return [panel["caption_hi"]]
    return [panel["caption_en"], panel["caption_hi"]]


def title_for(story: dict, lang: str) -> str:
    if lang == "English":
        return story["title_en"]
    if lang == "हिंदी":
        return story["title_hi"]
    return f"{story['title_en']} / {story['title_hi']}"


def _jpeg(img_bytes: bytes):
    im = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    im.thumbnail((1200, 1200))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=85)
    buf.seek(0)
    return buf, im.width, im.height


@st.cache_data(show_spinner=False, max_entries=5)
def comic_pdf(story_json: str, images: tuple, lang: str) -> bytes:
    story = json.loads(story_json)
    pdf = FPDF(format="A4")
    pdf.set_auto_page_break(True, margin=15)
    pdf.add_font("NotoSans", "", str(FONT_DIR / "NotoSans-Regular.ttf"))
    pdf.add_font("NotoSans", "B", str(FONT_DIR / "NotoSans-Bold.ttf"))
    pdf.add_font("Deva", "", str(FONT_DIR / "NotoSansDevanagari-Regular.ttf"))
    pdf.add_font("Deva", "B", str(FONT_DIR / "NotoSansDevanagari-Bold.ttf"))
    pdf.set_fallback_fonts(["Deva"])   # Hindi letters use the Devanagari font
    pdf.set_text_shaping(True)         # joins Hindi letters and matras correctly

    # Comic pages
    pdf.add_page()
    pdf.set_font("NotoSans", "B", 24)
    pdf.set_text_color(228, 87, 46)
    pdf.multi_cell(0, 12, title_for(story, lang), align="C")
    pdf.ln(4)
    pdf.set_text_color(34, 34, 34)
    img_w = 120
    x = (pdf.w - img_w) / 2
    for i, panel in enumerate(story["panels"]):
        img = images[i] if i < len(images) else None
        if pdf.get_y() + img_w + 30 > pdf.h - 15:
            pdf.add_page()
        if img:
            buf, w, h = _jpeg(img[0])
            img_h = img_w * h / w
            y = pdf.get_y()
            pdf.image(buf, x=x, y=y, w=img_w, h=img_h)
            pdf.set_draw_color(34, 34, 34)
            pdf.set_line_width(1)
            pdf.rect(x, y, img_w, img_h)
            pdf.set_y(y + img_h + 3)
        pdf.set_font("NotoSans", "B", 13)
        for cap in captions_for(panel, lang):
            pdf.set_x(x)
            pdf.multi_cell(img_w, 7, cap, align="C")
        pdf.ln(6)

    # The full story at the end
    texts = []
    if lang in ("English", "Both"):
        texts.append((story["title_en"], story["story_en"]))
    if lang in ("हिंदी", "Both"):
        texts.append((story["title_hi"], story["story_hi"]))
    for title, body in texts:
        pdf.add_page()
        pdf.set_font("NotoSans", "B", 20)
        pdf.set_text_color(228, 87, 46)
        pdf.multi_cell(0, 11, title, align="C")
        pdf.ln(4)
        pdf.set_text_color(34, 34, 34)
        pdf.set_font("NotoSans", "", 13)
        pdf.multi_cell(0, 8, body)
    return bytes(pdf.output())


def upload_for_sharing(pdf_bytes: bytes, name: str) -> str:
    path = f"{uuid.uuid4().hex}/{name}.pdf"
    headers = {"apikey": SUPABASE_KEY, "Content-Type": "application/pdf", "x-upsert": "true"}
    if SUPABASE_KEY.startswith("eyJ"):  # legacy service_role key (JWT) also goes in Authorization
        headers["Authorization"] = f"Bearer {SUPABASE_KEY}"
    r = requests.post(
        f"{SUPABASE_URL}/storage/v1/object/{SUPABASE_BUCKET}/{path}",
        headers=headers,
        data=pdf_bytes,
        timeout=60,
    )
    r.raise_for_status()
    return f"{SUPABASE_URL}/storage/v1/object/public/{SUPABASE_BUCKET}/{path}"


def download_and_share(story: dict, lang: str):
    st.header("📤 Download & share")
    with st.spinner("Making your comic book PDF..."):
        pdf = comic_pdf(json.dumps(story, ensure_ascii=False), tuple(ss.images), lang)
    pdf_key = hashlib.md5(pdf).hexdigest()
    name = file_name()

    c1, c2 = st.columns(2)
    with c1:
        st.download_button("⬇️ Download comic book (PDF)", data=pdf, file_name=f"{name}_comic.pdf",
                           mime="application/pdf", type="primary", use_container_width=True)
    with c2:
        save_button("bottom")

    if not (SUPABASE_URL and SUPABASE_KEY):
        st.caption("Tip: download the PDF and send it on WhatsApp or email to share it.")
        return

    st.markdown("**Share with family**")
    share = ss.get("share", {})
    if share.get("key") != pdf_key:
        st.caption("Makes a link anyone can open. Only people you send it to will know it.")
        if st.button("🔗 Make a share link", use_container_width=True):
            with st.spinner("Making your link..."):
                try:
                    ss.share = {"key": pdf_key, "url": upload_for_sharing(pdf, name)}
                    st.rerun()
                except Exception as e:
                    st.error(f"Could not make the link. You can still download the PDF. ({e})")
        return

    url = share["url"]
    msg = f"Look at my comic book: {title_for(story, lang)} 📖\n{url}"
    b1, b2 = st.columns(2)
    with b1:
        st.link_button("🟢 Share on WhatsApp", f"https://wa.me/?text={quote(msg)}", use_container_width=True)
    with b2:
        st.link_button("✉️ Share by email",
                       f"mailto:?subject={quote('My comic book: ' + story['title_en'])}&body={quote(msg)}",
                       use_container_width=True)
    st.caption("Or copy the link:")
    st.code(url, language=None)


def save_button(where):
    st.download_button(
        "💾 Save my story (to open again later)",
        data=story_file(),
        file_name=f"{file_name()}.story.json",
        mime="application/json",
        key=f"save_{where}",
        use_container_width=True,
    )


def show_story(story: dict):
    c1, c2 = st.columns(2)
    with c1:
        st.subheader(story["title_en"])
        st.write(story["story_en"])
    with c2:
        st.subheader(story["title_hi"])
        st.write(story["story_hi"])


def show_comic_section(step_label: str):
    story = ss.story
    fit_images()
    st.header(step_label)
    lang = st.radio("Comic words in:", ["English", "हिंदी", "Both"], horizontal=True, index=2)

    missing = sum(1 for im in ss.images if not im)
    b1, b2 = st.columns(2)
    with b1:
        if st.button(f"🎨 Draw missing pictures ({missing})", type="primary",
                     disabled=missing == 0, use_container_width=True):
            draw_pictures(only_missing=True)
            st.rerun()
    with b2:
        if st.button("🖌️ Redraw ALL pictures", use_container_width=True,
                     help="Use this if you changed how a character looks."):
            draw_pictures(only_missing=False)
            st.rerun()

    if ss.get("draw_errors"):
        st.error("Some pictures could not be drawn:\n\n" + "\n\n".join(ss.draw_errors))

    cols = st.columns(2)
    for i, panel in enumerate(story["panels"]):
        with cols[i % 2]:
            img = ss.images[i]
            if img:
                st.image(img[0], use_container_width=True)
            else:
                st.info(f"Picture {i + 1} is not drawn yet")
            for cap in captions_for(panel, lang):
                st.markdown(f"**{cap}**")
            with st.expander("✏️ Fix the words"):
                panel["caption_en"] = st.text_input("English", panel["caption_en"], key=f"cap_en_{i}_{ss.rev}")
                panel["caption_hi"] = st.text_input("हिंदी", panel["caption_hi"], key=f"cap_hi_{i}_{ss.rev}")
            if st.button("🔁 Redraw", key=f"redraw_{i}"):
                with st.spinner(f"Redrawing picture {i + 1}..."):
                    try:
                        ss.images[i] = draw_panel(story, panel, reference_for(i))
                        ss.draw_errors = []
                        st.rerun()
                    except Exception as e:
                        st.error(f"Redraw failed: {e}")
            st.divider()

    if any(ss.images):
        download_and_share(story, lang)


def voice_box(audio_label: str, audio_key: str, text_key: str, text_label: str):
    audio = st.audio_input(audio_label, key=audio_key)
    if audio is not None and st.button("✍️ Turn my voice into words", key=f"tr_{audio_key}"):
        with st.spinner("Listening carefully..."):
            try:
                ss[text_key] = transcribe(audio.getvalue(), audio.type or "audio/wav")
            except Exception as e:
                st.error(f"Could not understand the recording. Please try again. ({e})")
    st.text_area(text_label, key=text_key, height=150)


# ---------------- UI ----------------
st.title("📖 Myra Comics")

# ----- Start screen -----
if ss.mode is None:
    st.subheader("What would you like to do today?")
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("### ✨ New story")
        st.write("Tell a brand-new story and make a comic from it.")
        if st.button("Start a new story", type="primary", use_container_width=True):
            ss.mode = "new"
            st.rerun()
    with c2:
        st.markdown("### 📂 Change a saved story")
        st.write("Open a story you saved before and change it.")
        up = st.file_uploader("Choose your saved story file", type=["json"])
        if up is not None and st.button("Open this story", use_container_width=True):
            try:
                load_story_file(up.getvalue())
                ss.mode = "edit"
                st.rerun()
            except Exception:
                st.error("That file doesn't look like a saved story. Pick a file ending in .story.json")
    st.stop()

# ----- Sidebar (all modes) -----
with st.sidebar:
    st.button("🏠 Back to start", on_click=go_home, use_container_width=True)
    if st.button("🔍 Test voice/story AI (Google)", use_container_width=True):
        with st.spinner("Asking Google Gemini..."):
            try:
                ask_json('Return JSON: {"ok": "yes"}')
                st.success("✅ Google Gemini is working!")
            except Exception as e:
                st.error(str(e))
    if st.button("🔍 Test picture drawing", use_container_width=True):
        with st.spinner("Asking Cloudflare for a test picture..."):
            try:
                img = draw_panel_cloudflare(f"{STYLE}\n\nScene: a happy yellow duck in a pond",
                                            "a happy yellow cartoon duck")
                st.success("✅ Picture drawing works!")
                st.image(img[0])
            except Exception as e:
                st.error(str(e))
    if ss.story:
        st.caption("Save your story first if you want to come back to it later!")
        save_button("sidebar")

# ----- New story -----
if ss.mode == "new":
    st.caption("Tell your story in Hindi or English. We'll write it down and turn it into a comic book!")
    st.header("1️⃣ Tell your story")
    voice_box("Press the mic 🎤 and tell your story. Press again when you finish.",
              "audio_new", "idea", "Your story (you can fix any words here, or just type it):")

    st.header("2️⃣ Make it a story")
    n_panels = st.slider("How many comic pictures?", 4, 8, 6)
    if st.button("📝 Write my story", disabled=not ss.get("idea", "").strip()):
        with st.spinner("Writing your story in English and Hindi..."):
            try:
                set_story(write_story(ss["idea"], n_panels), [])
            except Exception as e:
                st.error(f"Story writing failed, please try again. ({e})")

    if ss.story:
        show_story(ss.story)
        show_comic_section("3️⃣ Make my comic book")

# ----- Change a saved story -----
elif ss.mode == "edit":
    st.caption("Here is your saved story. Tell us what to change!")
    show_story(ss.story)

    st.header("✏️ Change my story")
    voice_box("Press the mic 🎤 and say what to change (e.g. \"make the cat orange\", \"add a happy ending\").",
              "audio_change", "change", "What should we change? (you can also type it):")
    if st.button("🪄 Change my story", disabled=not ss.get("change", "").strip()):
        with st.spinner("Changing your story..."):
            try:
                old = ss.story
                new = revise_story(old, ss["change"])
                if new.get("characters") != old.get("characters"):
                    images = []  # a character looks different -> redraw everything for consistency
                else:
                    by_scene = {p["scene"]: im for p, im in zip(old["panels"], ss.images)}
                    images = [by_scene.get(p["scene"]) for p in new["panels"]]
                set_story(new, images)
                ss.pop("change", None)
                st.rerun()
            except Exception as e:
                st.error(f"Changing the story failed, please try again. ({e})")

    show_comic_section("🎨 My comic book")
