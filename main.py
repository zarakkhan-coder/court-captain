# CourtCaptain — Minimal vote form (Name + Day + Time), real-time availability fetch,
# weather with icon, auto-suggest court from majority vote, "Votes Casted" KPI,
# and a cleaner UI with pickleball side graphics.
#
# Works on Render or any host. If Club Automation requires login, set CLUB_COOKIE
# (see "Simple steps" below).

from flask import Flask, request, redirect, url_for, render_template_string, flash, jsonify
from datetime import datetime, timezone, date, time as dtime
import os, sqlite3, requests, re
from bs4 import BeautifulSoup

APP_NAME = "CourtCaptain"
PREFERRED_COURTS = ["Court 1", "Court 2", "Court 3", "Court 4"]
ALL_COURTS = PREFERRED_COURTS + ["Court 5", "Court 6", "Court 7", "Outdoor A", "Outdoor B", "Other"]
DAYS = ["Saturday", "Sunday"]

# ------ Environment (Render → Environment) ------
RESET_PIN   = os.environ.get("RESET_PIN", "1234")
SECRET_KEY  = os.environ.get("SECRET_KEY", "replace-me")
BOOKING_URL = os.environ.get("BOOKING_URL", "https://walmart.clubautomation.com/event/reserve-court-new")
LAT = float(os.environ.get("WALTON_LAT", "36.372"))
LON = float(os.environ.get("WALTON_LON", "-94.208"))
# If the site needs login, paste your browser cookie value here (Render → Environment)
CLUB_COOKIE = os.environ.get("CLUB_COOKIE", "")

DB_PATH = "data.db"

app = Flask(__name__)
app.secret_key = SECRET_KEY

# ---------------- DB ----------------
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = db()
    c = conn.cursor()
    # minimal vote: name + day + time_text (free text like "9:00-10:00" or "10am")
    c.execute("""CREATE TABLE IF NOT EXISTS votes(
        name      TEXT PRIMARY KEY,
        day       TEXT NOT NULL,
        time_text TEXT NOT NULL,
        ts        TEXT NOT NULL
    )""")
    conn.commit(); conn.close()
init_db()

# --------------- Helpers ---------------
TIME_PATTERN = re.compile(r"\b(\d{1,2})(?::?(\d{2}))?\s*(am|pm|AM|PM)?\s*[-–—]\s*(\d{1,2})(?::?(\d{2}))?\s*(am|pm|AM|PM)?\b")
ONE_TIME_PATTERN = re.compile(r"\b(\d{1,2})(?::?(\d{2}))?\s*(am|pm|AM|PM)?\b")

def to_minutes(h, m, ap=None):
    h = int(h); m = int(m) if m else 0
    if ap:
        ap = ap.lower()
        if ap == "pm" and h != 12: h += 12
        if ap == "am" and h == 12: h = 0
    return h*60 + m

def parse_time_window(text):
    """
    Parse "9-10am", "9:00-10:00", "9am-10am", "9:30–10:30", etc → (start_min, end_min)
    If only one time is provided, return (t, t).
    """
    if not text: return None
    text = text.strip()
    m = TIME_PATTERN.search(text)
    if m:
        sh, sm, sap, eh, em, eap = m.groups()
        start = to_minutes(sh, sm, sap)
        end   = to_minutes(eh, em, eap) if (eh) else start
        return (start, end)
    # fallback single time like "9am"
    m2 = ONE_TIME_PATTERN.search(text)
    if m2:
        h, mm, ap = m2.groups()
        t = to_minutes(h, mm, ap)
        return (t, t)
    return None

def minute_diff(a, b):
    return abs(a - b)

def majority_choice(votes):
    """
    From DB rows -> majority (day, time_window). If tie, prefer Saturday.
    Returns {'day': 'Saturday', 'time_text': '9:00-10:00', 'window':(start,end), 'votes':N}
    """
    # counts by (day, normalized_time_window)
    buckets = {}
    pretty_time = {}
    for v in votes:
        day = v["day"]
        time_text = (v["time_text"] or "").strip()
        win = parse_time_window(time_text)
        if not win:  # skip invalid
            continue
        key = (day, win)
        buckets[key] = buckets.get(key, 0) + 1
        # remember a nice display string
        if key not in pretty_time: pretty_time[key] = time_text

    if not buckets:
        return None

    ranked = sorted(
        buckets.items(),
        key=lambda kv: (-kv[1], 0 if kv[0][0]=="Saturday" else 1)
    )
    (day, win), count = ranked[0]
    return {"day": day, "window": win, "time_text": pretty_time[(day, win)], "votes": count}

# ---- Weather (with icon) via Open-Meteo ----
def weather_icon(code, pop):
    # Simple mapping for icons (emoji) by weathercode; fallback by precipitation
    # Open-Meteo weathercode reference:
    # 0 Clear, 1-3 Partly cloudy, 45/48 Fog, 51-57 Drizzle, 61-67 Rain, 71-77 Snow, 80-82 Rain showers, 95-99 Thunder
    if code == 0: return "☀️"
    if code in (1,2,3): return "⛅"
    if code in (45,48): return "🌫️"
    if code in (51,53,55,56,57): return "🌦️"
    if code in (61,63,65,66,67,80,81,82): return "🌧️"
    if code in (71,73,75,77): return "❄️"
    if code in (95,96,99): return "⛈️"
    if pop and int(pop) >= 50: return "🌧️"
    return "🌤️"

def fetch_weather_days():
    """
    Return {'Saturday': {'tmax':..,'tmin':..,'pop':..,'code':..,'icon':'...'},
            'Sunday': {...}}
    """
    out = {}
    try:
        url = ("https://api.open-meteo.com/v1/forecast"
               f"?latitude={LAT}&longitude={LON}"
               "&daily=weathercode,temperature_2m_max,temperature_2m_min,precipitation_probability_max"
               "&forecast_days=7&timezone=auto")
        r = requests.get(url, timeout=8); r.raise_for_status()
        d = r.json().get("daily", {})
        times = d.get("time", [])
        tmax  = d.get("temperature_2m_max", [])
        tmin  = d.get("temperature_2m_min", [])
        pop   = d.get("precipitation_probability_max", [])
        code  = d.get("weathercode", [])
        for i, ds in enumerate(times):
            y,m,dd = map(int, ds.split("-"))
            wname = date(y,m,dd).strftime("%A")
            if wname in ("Saturday","Sunday"):
                icon = weather_icon(code[i] if i < len(code) else None,
                                    pop[i] if i < len(pop) else None)
                out[wname] = {"tmax": tmax[i], "tmin": tmin[i],
                              "pop": pop[i], "code": code[i], "icon": icon}
        return out
    except Exception:
        return out

# ---- Real-time availability from Club Automation ----
BASE_CA_URL = "https://walmart.clubautomation.com/event/reserve-court-new"

def fetch_availability_for_day(day):
    """
    For the chosen day, fetch each court page and extract time slots.
    Returns {'Court 1': ['9:00-10:00', ...], ...}
    NOTE: If the site needs login, set CLUB_COOKIE env var with your session cookie.
    """
    headers = {}
    if CLUB_COOKIE:
        headers["Cookie"] = CLUB_COOKIE

    slots_by_court = {c: [] for c in ALL_COURTS}

    for court in ALL_COURTS:
        try:
            params = {"day": day, "court": court}
            r = requests.get(BASE_CA_URL, params=params, headers=headers, timeout=10)
            r.raise_for_status()
            html = r.text
            soup = BeautifulSoup(html, "html.parser")

            # 1) Try obvious slot elements
            slot_nodes = soup.select(".slot, .time-slot, .slot-label, .reservation-time, time, [data-time]")
            found = set()
            for sn in slot_nodes:
                t = sn.get("data-time") or sn.get_text(" ", strip=True)
                t = re.sub(r"\s+", " ", t or "").strip()
                # Convert '9:00 AM - 10:00 AM' → '9:00-10:00' style (simple normalize)
                t = t.replace("AM", "am").replace("PM", "pm")
                t = re.sub(r"\s*-\s*", "-", t)
                if t and re.search(r"\d", t):
                    found.add(t)

            # 2) Fallback: scan all text for time windows
            if not found:
                for m in TIME_PATTERN.finditer(soup.get_text(" ", strip=True)):
                    sh, sm, sap, eh, em, eap = m.groups()
                    left = f"{sh}:{sm or '00'}{(''+sap).lower() if sap else ''}"
                    right = f"{eh}:{em or '00'}{(''+eap).lower() if eap else ''}"
                    found.add(f"{left}-{right}")

            slots_by_court[court] = sorted(list(found))
        except Exception:
            # leave it empty if error
            pass

    return slots_by_court

def pick_best_court(day, want_window, avail_map):
    """
    Choose the best court given preferred window and availability map.
    Priority: exact time match on preferred courts 1–4 → exact on others →
              nearest-time on preferred → nearest-time on others.
    Returns {'court': 'Court 1', 'slot': '9:00-10:00', 'match':'exact'|'near', 'diff_min':X} or None
    """
    if not want_window: return None
    start_want = (want_window[0] + want_window[1]) // 2  # mid-point

    def slot_mid_minutes(slot_text):
        win = parse_time_window(slot_text)
        if not win: return None
        return (win[0] + win[1]) // 2

    # Helper to scan with a set of courts and exact/near flag
    def scan(courts, exact=True):
        best = None
        for c in courts:
            for s in avail_map.get(c, []):
                sw = parse_time_window(s)
                if not sw: continue
                if exact:
                    # exact-ish: overlapping windows
                    if not (sw[1] >= want_window[0] and sw[0] <= want_window[1]):
                        continue
                mid = (sw[0] + sw[1]) // 2
                diff = minute_diff(mid, start_want)
                if (best is None) or (diff < best["diff_min"]):
                    best = {"court": c, "slot": s, "match": "exact" if exact else "near", "diff_min": diff}
        return best

    # Try exact on preferred → exact on others → near on preferred → near on others
    others = [c for c in ALL_COURTS if c not in PREFERRED_COURTS]
    for courts, exact in [(PREFERRED_COURTS, True), (others, True), (PREFERRED_COURTS, False), (others, False)]:
        found = scan(courts, exact=exact)
        if found: return found
    return None

# --------------- Templates ---------------
BASE = """
<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{{ app_name }}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;600;800&display=swap" rel="stylesheet">
<style>
:root{--bg:#0b1220;--card:#121a2b;--glass:rgba(255,255,255,.06);--text:#e8eefc;--muted:#9fb0d6;--primary:#5ea1ff;--accent:#22d3ee;--success:#22c55e;--warn:#f59e0b;--danger:#ef4444;--border:#1f2a44;--shadow:0 12px 28px rgba(0,0,0,.35)}
*{box-sizing:border-box} html,body{margin:0;padding:0;background:linear-gradient(180deg,#0a0f1a,#0b1220);color:var(--text);font-family:Inter,system-ui,Segoe UI,Roboto,Arial,sans-serif}
.page{max-width:1100px;margin:24px auto;padding:0 18px;position:relative}
.side-art{position:fixed;top:10%;bottom:10%;left:0;right:0;pointer-events:none;display:flex;justify-content:space-between;opacity:.15}
.paddle{width:160px;height:240px;border-radius:40px;background:radial-gradient(circle at 30% 30%,#3fd0ff,transparent 60%),#1b2a44;border:6px solid #2b4b7a;box-shadow:0 20px 40px rgba(0,0,0,.4);transform:rotate(-12deg)}
.paddle.right{transform:scaleX(-1) rotate(-12deg)}
.ball{width:40px;height:40px;border-radius:999px;background:#ffd54a;box-shadow:0 8px 18px rgba(0,0,0,.45);align-self:center}
.header{position:sticky;top:0;background:var(--glass);backdrop-filter:blur(10px);border:1px solid var(--border);border-radius:14px;padding:14px 18px;margin-bottom:18px;display:flex;align-items:center;justify-content:space-between;box-shadow:var(--shadow);animation:fadeIn .5s ease}
.brand{display:flex;gap:10px;align-items:center}.logo{font-size:24px}.title{font-weight:800}
.nav{display:flex;gap:12px}.nav a{padding:8px 12px;border-radius:10px;color:var(--muted);text-decoration:none;transition:.2s}
.nav a:hover{background:rgba(255,255,255,.06);color:var(--text);transform: translateY(-1px)}
.flash{margin:14px 0;padding:12px 14px;border-radius:12px;border:1px solid var(--border)}.flash.success{background:rgba(34,197,94,.12)}.flash.error{background:rgba(239,68,68,.12)}
.card{background:var(--card);border:1px solid var(--border);border-radius:16px;padding:20px;box-shadow:var(--shadow);margin-bottom:18px;transition:transform .15s ease,box-shadow .15s ease;animation:fadeIn .4s ease}
.card:hover{transform: translateY(-2px);box-shadow:0 14px 30px rgba(0,0,0,.4)}
.glass{background:var(--glass);border:1px solid var(--border);border-radius:14px;padding:18px;margin-bottom:18px;animation:fadeIn .4s ease}
.heading{margin:0 0 6px 0;font-size:28px}.sub{margin:0;color:var(--muted)}
.form{display:flex;flex-direction:column;gap:14px}.grid{display:grid;grid-template-columns:1fr 1fr 1fr;gap:14px}@media(max-width:900px){.grid{grid-template-columns:1fr}}
label span{display:block;margin-bottom:8px;color:var(--muted)}
input,select{width:100%;padding:12px;border-radius:10px;border:1px solid var(--border);background:#0f1627;color:var(--text)} input:focus,select:focus{outline:none;border-color:var(--primary)}
.btn{display:inline-flex;align-items:center;gap:8px;padding:12px 14px;border-radius:12px;border:1px solid var(--border);cursor:pointer;background:#0f1627;color:var(--text);transition:.2s}
.btn.primary{background:linear-gradient(135deg,var(--primary),#7cd2ff);color:#06101f;border:none}
.btn.primary:hover{filter:brightness(1.05);transform: translateY(-1px)}
.table{display:flex;flex-direction:column;gap:10px}
.row{display:grid;grid-template-columns:1.2fr 1.2fr 1fr;gap:12px;background:#0f1627;border:1px solid var(--border);border-radius:12px;padding:12px}
.head{background:transparent;border-style:dashed;font-weight:600}
.badge{background:#14304a;border:1px solid #285b8a;padding:4px 8px;border-radius:999px;font-size:12px;margin-left:6px}
.kpi{display:flex;gap:10px;flex-wrap:wrap}
.kpi .pill{border:1px solid var(--border);background:#0f1627;border-radius:999px;padding:8px 12px;display:inline-flex;align-items:center;gap:8px}
.voters{color:var(--muted);font-size:13px;margin-top:4px}
.footer{margin:18px 0;color:var(--muted);text-align:center}
@keyframes fadeIn {from{opacity:0;transform:translateY(4px)} to{opacity:1;transform:none}}
</style></head><body><div class="page">

<div class="side-art">
  <div class="paddle"></div>
  <div class="ball"></div>
  <div class="paddle right"></div>
</div>

<div class="header"><div class="brand"><div class="logo">🏓</div><div class="title">{{ app_name }}</div></div>
<div class="nav"><a href="{{ url_for('home') }}">Vote</a><a href="{{ url_for('results') }}">Results</a></div></div>
{% with messages = get_flashed_messages(with_categories=true) %}{% for cat,msg in messages %}<div class="flash {{ cat }}">{{ msg }}</div>{% endfor %}{% endwith %}
{{ content|safe }}
<div class="footer">© {{ app_name }}</div>
</div></body></html>
"""

INDEX = """
<div class="glass"><h1 class="heading">Weekend Pickleball Poll — Walton Fitness Centre</h1>
<p class="sub">Enter your name, pick a day, and tell us your preferred time (e.g., "9-10am").</p></div>

<div class="card">
  <form method="POST" class="form">
    <div class="grid">
      <label><span>Your name</span><input name="name" required placeholder="e.g., Sam W."></label>
      <label><span>Preferred day</span>
        <select name="day" required>
          <option value="" disabled selected>Choose</option>
          {% for d in days %}<option value="{{ d }}">{{ d }}</option>{% endfor %}
        </select>
      </label>
      <label><span>Preferred time (e.g., 9-10am or 9:30-10:30)</span>
        <input name="time_text" required placeholder="e.g., 9-10am">
      </label>
    </div>
    <div style="display:flex;gap:10px;flex-wrap:wrap">
      <button class="btn primary" type="submit">Submit Vote</button>
      <a class="btn" href="{{ url_for('results') }}">Go to Results</a>
    </div>
  </form>
</div>
"""

RESULTS = """
{% set s = summary %}
<div class="glass">
  <h1 class="heading">Dashboard</h1>
  <div class="kpi">
    <div class="pill">Votes Casted: <b>{{ s.total_players }}</b></div>
    <div class="pill">Saturday: <span class="badge">{{ wx.Saturday.icon if wx.Saturday else '—' }}</span>
      {% if wx.Saturday %}<span class="badge">{{ wx.Saturday.tmin|int }}–{{ wx.Saturday.tmax|int }}°C • {{ wx.Saturday.pop|int }}% rain</span>{% endif %}
    </div>
    <div class="pill">Sunday: <span class="badge">{{ wx.Sunday.icon if wx.Sunday else '—' }}</span>
      {% if wx.Sunday %}<span class="badge">{{ wx.Sunday.tmin|int }}–{{ wx.Sunday.tmax|int }}°C • {{ wx.Sunday.pop|int }}% rain</span>{% endif %}
    </div>
  </div>
</div>

{% if s.majority %}
<div class="card" style="border-color:#22d3ee">
  <h3 style="margin-top:0">Majority Pick</h3>
  <p style="font-size:18px">
    <b>{{ s.majority.day }}</b> • <b>{{ s.majority.time_text }}</b>
    <span class="sub">({{ s.majority.votes }} vote{{ '' if s.majority.votes==1 else 's' }})</span>
  </p>
  {% if s.suggestion %}
    <p style="margin-top:8px">Suggested Court: <b>{{ s.suggestion.court }}</b> — <b>{{ s.suggestion.slot }}</b>
    <span class="badge">{{ 'Exact match' if s.suggestion.match=='exact' else 'Nearest time' }}</span></p>
    <form method="POST" action="{{ url_for('book_court') }}">
      <input type="hidden" name="day" value="{{ s.majority.day }}">
      <input type="hidden" name="court" value="{{ s.suggestion.court }}">
      <button class="btn primary" type="submit">Book Court</button>
    </form>
  {% else %}
    <p class="sub">No suitable court/slot found yet for the majority time.</p>
  {% endif %}
  <p class="note">Ranking logic: votes → preferred courts (1–4) → exact time → nearest time.</p>
</div>
{% endif %}

<div class="card">
  <h3 style="margin-top:0">Real-Time Availability for {{ s.majority.day if s.majority else 'Saturday/Sunday' }}</h3>
  <div class="table">
    <div class="row head"><div>Court</div><div>Open Slots</div><div>Preferred</div></div>
    {% for c in courts %}
      <div class="row">
        <div>{{ c }}</div>
        <div>{% if avail.get(c) and avail.get(c)|length > 0 %}{{ avail.get(c) | join(', ') }}{% else %}—{% endif %}</div>
        <div>{% if c in preferred %}Yes{% else %}—{% endif %}</div>
      </div>
    {% endfor %}
  </div>
</div>

<div class="card">
  <h3 style="margin-top:0">Votes (who chose what)</h3>
  <div class="table">
    <div class="row head"><div>Name</div><div>Day</div><div>Time</div></div>
    {% for v in raw_votes %}
      <div class="row"><div>{{ v.name }}</div><div>{{ v.day }}</div><div>{{ v.time_text }}</div></div>
    {% else %}
      <div class="row"><div>No votes yet.</div><div></div><div></div></div>
    {% endfor %}
  </div>
</div>
"""

# --------------- Routes ---------------
@app.get("/health")
def health(): return jsonify(ok=True), 200

@app.route("/", methods=["GET","POST"])
def home():
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        day = request.form.get("day")
        time_text = (request.form.get("time_text") or "").strip()
        if not name or day not in DAYS or not time_text:
            flash("Please enter your name, select a valid day, and provide a time like '9-10am'.", "error")
            return redirect(url_for("home"))
        conn = db()
        conn.execute(
            "INSERT INTO votes(name, day, time_text, ts) VALUES(?,?,?,?) "
            "ON CONFLICT(name) DO UPDATE SET day=excluded.day, time_text=excluded.time_text, ts=excluded.ts",
            (name, day, time_text, datetime.now(timezone.utc).isoformat())
        )
        conn.commit(); conn.close()
        flash("Vote submitted. Thanks!", "success")
        return redirect(url_for("results"))
    return render_template_string(BASE, content=render_template_string(INDEX, days=DAYS), app_name=APP_NAME)

@app.get("/results")
def results():
    # Pull raw votes
    conn = db()
    rows = conn.execute("SELECT name, day, time_text FROM votes").fetchall()
    conn.close()

    # Majority (day + time window)
    majority = majority_choice(rows)

    # Weather (Sat/Sun)
    wx = fetch_weather_days()

    # Real-time availability for chosen majority day (or Saturday if none yet)
    chosen_day = majority["day"] if majority else "Saturday"
    avail = fetch_availability_for_day(chosen_day)

    # Suggest court from availability
    suggestion = pick_best_court(chosen_day, majority["window"] if majority else None, avail) if majority else None

    # Compose summary object
    summary = {
        "total_players": len(rows),
        "majority": majority,
        "suggestion": suggestion
    }

    html = render_template_string(
        RESULTS,
        summary=summary,
        wx=wx,
        avail=avail,
        courts=ALL_COURTS,
        preferred=PREFERRED_COURTS,
        raw_votes=rows
    )
    return render_template_string(BASE, content=html, app_name=APP_NAME)

@app.post("/book")
def book_court():
    """Redirect to booking site with simple query params."""
    day = (request.form.get("day") or "").strip()
    court = (request.form.get("court") or "").strip()
    if day not in DAYS or court not in ALL_COURTS:
        flash("Invalid booking selection.", "error")
        return redirect(url_for("results"))
    sep = "&" if "?" in BOOKING_URL else "?"
    target = f"{BOOKING_URL}{sep}day={day}&court={court}"
    return redirect(target, code=302)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
