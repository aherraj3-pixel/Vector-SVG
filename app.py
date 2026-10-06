import os, re, time, uuid, requests
from collections import defaultdict, deque
from flask import Flask, request, jsonify, send_from_directory
try:
    from dotenv import load_dotenv; load_dotenv()
except ImportError:
    pass

app = Flask(__name__, static_folder="static")
KEY = os.environ.get("GEMINI_API_KEY", "")
MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

PER_MIN = int(os.environ.get("LIMIT_PER_MIN", 4))
PER_DAY = int(os.environ.get("LIMIT_PER_DAY_IP", 30))
GLOBAL_DAY = int(os.environ.get("LIMIT_GLOBAL_DAY", 800))
hits, day = defaultdict(deque), {"d": "", "n": 0}

def limited(ip):
    now = time.time(); q = hits[ip]
    while q and now - q[0] > 86400: q.popleft()
    if sum(1 for t in q if now - t < 60) >= PER_MIN: return "Too many requests. Wait a minute."
    if len(q) >= PER_DAY: return "Daily limit reached. Come back tomorrow."
    today = time.strftime("%Y-%m-%d")
    if day["d"] != today: day["d"], day["n"] = today, 0
    if day["n"] >= GLOBAL_DAY: return "Today's free capacity is used up. Try again tomorrow."
    q.append(now); day["n"] += 1

TYPES = {
    "Icon": "a single flat icon, simple bold shapes, centered, 512x512",
    "Logo": "a clean modern logo mark, minimal shapes, 2-3 colors, centered",
    "Illustration": "a flat vector illustration scene with layered shapes and a harmonious palette",
    "Pattern": "a seamless-looking repeating geometric or organic pattern filling the full canvas",
    "Diagram": "a clear labeled diagram or flowchart with boxes, arrows and readable text",
    "Sticker": "a cute sticker-style graphic with thick outline and flat colors",
    "Background": "a soft abstract wallpaper background with gradients and shapes",
}

def clean(text):
    m = re.search(r"<svg[\s\S]*</svg>", text, re.I)
    if not m: return None
    s = m.group(0)
    s = re.sub(r"<script[\s\S]*?</script>", "", s, flags=re.I)
    s = re.sub(r"<foreignObject[\s\S]*?</foreignObject>", "", s, flags=re.I)
    s = re.sub(r"\son\w+\s*=\s*(\"[^\"]*\"|'[^']*')", "", s, flags=re.I)
    head = re.match(r"<svg[^>]*>", s, re.I).group(0)
    new = head
    if "xmlns=" not in new: new = new.replace("<svg", '<svg xmlns="http://www.w3.org/2000/svg"', 1)
    if "viewBox" not in new: new = new.replace("<svg", '<svg viewBox="0 0 512 512"', 1)
    return s.replace(head, new, 1)

@app.get("/")
def index(): return send_from_directory("static", "index.html")

@app.post("/api/generate")
def generate():
    d = request.get_json(force=True)
    prompt = (d.get("prompt") or "").strip()[:500]
    kind = d.get("type") if d.get("type") in TYPES else "Icon"
    if not prompt: return jsonify(error="Enter a prompt first."), 400
    ip = (request.headers.get("X-Forwarded-For") or request.remote_addr or "?").split(",")[0].strip()
    why = limited(ip)
    if why: return jsonify(error=why), 429
    if not KEY: return jsonify(error="GEMINI_API_KEY is missing. Add it to your .env file."), 500
    instruction = (
        "You are an expert SVG artist. Return ONLY valid, self-contained SVG code. "
        "No markdown, no explanation. Use a viewBox, no external images or fonts, no scripts. "
        f"Create {TYPES[kind]}. Keep the background transparent unless the design needs one."
    )
    body = {"system_instruction": {"parts": [{"text": instruction}]},
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.8, "maxOutputTokens": 8192}}
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"
    for attempt in range(3):
        try:
            r = requests.post(url, json=body, headers={"x-goog-api-key": KEY}, timeout=60)
        except requests.RequestException:
            return jsonify(error="Could not reach Gemini. Check your internet."), 502
        if r.status_code in (429, 503) and attempt < 2:
            time.sleep(2 * (attempt + 1)); continue
        break
    if r.status_code == 429: return jsonify(error="Free limit reached. Wait a minute and try again."), 429
    if not r.ok: return jsonify(error=f"Gemini error {r.status_code}. Check your key and model name."), 502
    try:
        text = "".join(p.get("text", "") for p in r.json()["candidates"][0]["content"]["parts"])
    except Exception:
        return jsonify(error="Gemini returned no result. Try a different prompt."), 502
    svg = clean(text)
    if not svg: return jsonify(error="No valid SVG came back. Try again."), 502
    item = {"id": uuid.uuid4().hex[:8], "prompt": prompt, "type": kind, "svg": svg, "ts": int(time.time())}
    return jsonify(item)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
