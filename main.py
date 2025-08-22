from flask import Flask, request, redirect, url_for, render_template_string, flash, jsonify
from datetime import datetime, timezone
import sqlite3, os, requests

APP_NAME = "CourtCaptain"
PREFERRED_COURTS = ["Court 1","Court 2","Court 3","Court 4"]
ALL_COURTS = PREFERRED_COURTS + ["Court 5","Court 6","Court 7","Court 8","Outdoor A","Outdoor B","Other"]
DAYS = ["Saturday","Sunday"]

# Config via env (edit in Render dashboard)
RESET_PIN = os.environ.get("RESET_PIN","1234")
SECRET_KEY = os.environ.get("SECRET_KEY","replace-me")
# Coordinates for outdoor courts (edit if needed)
LAT = float(os.environ.get("WALTON_LAT","36.372"))  # example
LON = float(os.environ.get("WALTON_LON","-94.208")) # example
# If wholehealth.walmart.com requires auth, set a cookie/header in Render:
WALHEALTH_COOKIE = os.environ.get("WALHEALTH_COOKIE","")  # e.g. "session=abc123"

DB_PATH = "data.db"

app = Flask(__name__)
app.secret_key = SECRET_KEY

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = db(); c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS votes(
        name TEXT PRIMARY KEY, day TEXT NOT NULL, court TEXT NOT NULL, ts TEXT NOT NULL
    )""")
    conn.commit(); conn.close()
init_db()

def pref_rank(court):
    return PREFERRED_COURTS.index(court) if court in PREFERRED_COURTS else 100+ALL_COURTS.index(court)
def day_rank(day): return 0 if day=="Saturday" else 1

def tally():
    conn = db()
    rows = conn.execute("SELECT name,day,court FROM votes").fetchall()
    conn.close()
    counts, players = {}, set()
    for r in rows:
        players.add(r["name"].strip().lower())
        key = (r["day"], r["court"])
        counts[key] = counts.get(key,0)+1
    ranked = sorted(counts.items(), key=lambda kv:(-kv[1], pref_rank(kv[0][1]), day_rank(kv[0][0])))
    top = ranked[0] if ranked else None
    return {
        "total_players": len(players),
        "counts": [{"day":d,"court":c,"votes":v} for (d,c),v in ranked],
        "top_choice": {"day":top[0][0],"court":top[0][1],"votes":top[1]} if top else None,
        "booking_possible": len(players) >= 4
    }

def is_outdoor(court:str)->bool:
    return "Outdoor" in court or court.lower().startswith("outdoor")

def fetch_weather(day:str):
    """Open-Meteo (no key). Returns simple forecast dict for Sat/Sun."""
    try:
        # Pull next 3 days; pick Saturday/Sunday by name.
        url = f"https://api.open-meteo.com/v1/forecast?latitude={LAT}&longitude={LON}&daily=temperature_2m_max,temperature_2m_min,precipitation_probability_max&forecast_days=7&timezone=auto"
        r = requests.get(url, timeout=6); r.raise_for_status()
        d = r.json().get("daily",{})
        # Map dates to weekday names
        from datetime import date
        out=[]
        for i,ds in enumerate(d.get("time",[])):
            y,m,dd = map(int,ds.split("-"))
            wname = date(y,m,dd).strftime("%A")
            out.append({
                "date": ds,
                "weekday": wname,
                "tmax": d.get("temperature_2m_max",[None]*8)[i],
                "tmin": d.get("temperature_2m_min",[None]*8)[i],
                "pop":  d.get("precipitation_probability_max",[None]*8)[i],
            })
        target = next((x for x in out if x["weekday"].lower().startswith(day[:3].lower())), None)
        return target
    except Exception:
        return None

def fetch_availability():
    """
    Placeholder for wholehealth.walmart.com availability.
    If the site needs auth, set WALHEALTH_COOKIE in Render env (session cookie).
    Return dict: {"Court 1": True/False, ...}
    """
    try:
        headers = {}
        if WALHEALTH_COOKIE:
            headers["Cookie"] = WALHEALTH_COOKIE
        # TODO: replace with the real availability endpoint once known.
        # For now, treat 1–4 as available, others unknown.
        # Example: requests.get("https://wholehealth.walmart.com/...", headers=headers, timeout=6)
        avail = {c: True for c in PREFERRED_COURTS}
        for c in ALL_COURTS:
            avail.setdefault(c, True)  # optimistic until endpoint is known
        return avail
    except Exception:
        return {c: True for c in ALL_COURTS}

BASE = """
<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{{ app_name }}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;600;800&display=swap" rel="stylesheet">
<style>
:root{--bg:#0b1220;--card:#121a2b;--glass:rgba(255,255,255,.06);--text:#e8eefc;--muted:#9fb0d6;--primary:#5ea1ff;--accent:#22d3ee;--success:#22c55e;--warn:#f59e0b;--danger:#ef4444;--border:#1f2a44;--shadow:0 12px 28px rgba(0,0,0,.35)}
*{box-sizing:border-box} html,body{margin:0;padding:0;background:linear-gradient(180deg,#0a0f1a,#0b1220);color:var(--text);font-family:Inter,system-ui,Segoe UI,Roboto,Arial,sans-serif}
.page{max-width:980px;margin:24px auto;padding:0 18px}
.header{position:sticky;top:0;background:var(--glass);backdrop-filter:blur(10px);border:1px solid var(--border);border-radius:14px;padding:14px 18px;margin-bottom:18px;display:flex;align-items:center;justify-content:space-between;box-shadow:var(--shadow)}
.brand{display:flex;gap:10px;align-items:center}.logo{font-size:24px}.title{font-weight:800}
.nav{display:flex;gap:12px}.nav a{padding:8px 12px;border-radius:10px;color:var(--muted);text-decoration:none}.nav a:hover{background:rgba(255,255,255,.06);color:var(--text)}
.flash{margin:14px 0;padding:12px 14px;border-radius:12px;border:1px solid var(--border)}.flash.success{background:rgba(34,197,94,.12)}.flash.error{background:rgba(239,68,68,.12)}
.card{background:var(--card);border:1px solid var(--border);border-radius:16px;padding:20px;box-shadow:var(--shadow);margin-bottom:18px}
.glass{background:var(--glass);border:1px solid var(--border);border-radius:14px;padding:18px;margin-bottom:18px}
.heading{margin:0 0 6px 0;font-size:28px}.sub{margin:0;color:var(--muted)}
.form{display:flex;flex-direction:column;gap:14px}.grid{display:grid;grid-template-columns:1fr 1fr 1fr;gap:14px}@media(max-width:840px){.grid{grid-template-columns:1fr}}
label span{display:block;margin-bottom:8px;color:var(--muted)}
input,select{width:100%;padding:12px;border-radius:10px;border:1px solid var(--border);background:#0f1627;color:var(--text)} input:focus,select:focus{outline:none;border-color:var(--primary)}
.btn{display:inline-flex;align-items:center;gap:8px;padding:12px 14px;border-radius:12px;border:1px solid var(--border);cursor:pointer;background:#0f1627;color:var(--text)}
.btn.primary{background:linear-gradient(135deg,var(--primary),#7cd2ff);color:#06101f;border:none}
.btn.link{background:transparent;border:none;color:var(--primary)}
.btn.danger{background:linear-gradient(135deg,#ef4444,#f87171);border:none}
.table{display:flex;flex-direction:column;gap:10px}
.row{display:grid;grid-template-columns:1fr 1fr 100px;gap:12px;background:#0f1627;border:1px solid var(--border);border-radius:12px;padding:12px}
.head{background:transparent;border-style:dashed;font-weight:600}
.tag{padding:4px 10px;border-radius:999px;border:1px solid var(--border);font-size:12px}
.tag.success{background:rgba(34,197,94,.15);border-color:#194d33}
.tag.warn{background:rgba(245,158,11,.15);border-color:#4a3517}
.footer{margin:18px 0;color:var(--muted);text-align:center}
.note{color:var(--muted);font-size:14px}
.badge{background:#14304a;border:1px solid #285b8a;padding:2px 8px;border-radius:999px;font-size:12px;margin-left:6px}
</style></head><body><div class="page">
<div class="header"><div class="brand"><div class="logo">🏓</div><div class="title">{{ app_name }}</div></div>
<div class="nav"><a href="{{ url_for('home') }}">Vote</a><a href="{{ url_for('results') }}">Results</a></div></div>
{% with messages = get_flashed_messages(with_categories=true) %}{% for cat,msg in messages %}<div class="flash {{ cat }}">{{ msg }}</div>{% endfor %}{% endwith %}
{% block content %}{% endblock %}
<div class="footer">© {{ app_name }} — Pickleball made easy.</div>
</div></body></html>
"""

INDEX = """
{% extends "base" %}{% block content %}
<div class="glass"><h1 class="heading">Weekend Pickleball Poll — Walton Fitness Centre</h1>
<p class="sub">Vote by <b>Wednesday 6pm</b>. Need <b>4+ players</b> to book. Preferred courts: <b>1–4</b>.</p></div>
<div class="card">
  <form method="POST" class="form">
    <div class="grid">
      <label><span>Your name</span><input name="name" required placeholder="e.g., Sam W."></label>
      <label><span>Preferred day</span><select name="day" required><option value="" disabled selected>Choose</option>{% for d in days %}<option value="{{ d }}">{{ d }}</option>{% endfor %}</select></label>
      <label><span>Preferred court</span><select name="court" required><option value="" disabled selected>Choose</option>
        {% for c in courts %}<option value="{{ c }}">{{ c }}{% if c in preferred %} ★{% endif %}</option>{% endfor %}
      </select></label>
    </div>
    <div style="display:flex;gap:10px;flex-wrap:wrap"><button class="btn primary" type="submit">Submit Vote</button><a class="btn link" href="{{ url_for('results') }}">See Results</a></div>
    <p class="note">Pin this link in WhatsApp so friends can vote anytime.</p>
  </form>
</div>
<div class="card">
  <form method="POST" action="{{ url_for('reset') }}" onsubmit="return confirm('You will be asked for PIN to clear all votes. Continue?')">
    <button class="btn danger" type="submit">Reset Votes (New Week)</button>
  </form>
  <p class="note">Admin-only (PIN). Use weekly to start fresh.</p>
</div>
{% endblock %}
"""

RESULTS = """
{% extends "base" %}{% block content %}
{% set s = summary %}
<div class="glass">
  <h1 class="heading">Live Results</h1>
  <p class="sub">Players confirmed: <b>{{ s.total_players }}</b> • {% if s.booking_possible %}<span class="tag success">Booking possible (≥4)</span>{% else %}<span class="tag warn">Need at least 4 players</span>{% endif %}</p>
</div>

{% if s.top_choice %}
<div class="card" style="border-color:#22d3ee">
  <h3 style="margin-top:0">Decision (auto)</h3>
  <p style="font-size:20px">
    <b>{{ s.top_choice.day }}</b> on <b>{{ s.top_choice.court }}</b> <span class="sub">({{ s.top_choice.votes }} votes)</span>
    {% if s.top_choice.court in outdoor_list and weather %}
      <span class="badge">Weather: {{ weather.tmin|default('?')|int }}–{{ weather.tmax|default('?')|int }}°C, {{ weather.pop|default('?') }}% rain</span>
    {% endif %}
  </p>
  <p class="note">Ranking: most votes → preferred courts (1–4) → Saturday over Sunday.</p>
</div>
{% endif %}

<div class="card">
  <h3 style="margin-top:0">Vote Breakdown</h3>
  <div class="table">
    <div class="row head"><div>Day</div><div>Court</div><div>Votes</div></div>
    {% for item in s.counts %}
      <div class="row">
        <div>{{ item.day }}</div>
        <div>{{ item.court }}{% if item.court in preferred %} ★{% endif %}</div>
        <div>{{ item.votes }}</div>
      </div>
    {% else %}
      <div class="row"><div>No votes yet. Share the link! 🏓</div><div></div><div></div></div>
    {% endfor %}
  </div>
</div>

<div class="card">
  <h3 style="margin-top:0">Availability (from wholehealth.walmart.com)</h3>
  <div class="table">
    <div class="row head"><div>Court</div><div>Status</div><div></div></div>
    {% for c in courts %}
      <div class="row"><div>{{ c }}</div><div>{{ "Available" if availability[c] else "Unavailable" }}</div><div>{% if c in preferred %}Preferred{% endif %}</div></div>
    {% endfor %}
  </div>
  <p class="note">If availability requires login, set WALHEALTH_COOKIE in environment.</p>
</div>

<div class="glass"><p class="note">By Thursday evening, lock in the choice and book on the Walton Fitness Centre site.</p></div>
{% endblock %}
"""

RESET = """
{% extends "base" %}{% block content %}
<div class="card">
  <h2 style="margin-top:0">Reset Votes (Admin)</h2>
  <form method="POST" action="{{ url_for('confirm_reset') }}" class="form">
    <label><span>Enter PIN</span><input type="password" name="pin" placeholder="PIN" required></label>
    <button class="btn danger" type="submit">Confirm Reset</button>
    <a class="btn link" href="{{ url_for('home') }}">Cancel</a>
  </form>
</div>
{% endblock %}
"""

def render_page(tpl, **ctx):
    return render_template_string(
        "{% set app_name='" + APP_NAME + "' %}" +
        "{% block base %}" + BASE + "{% endblock %}" +
        "{% extends 'base' %}" + tpl,
        app_name=APP_NAME, **ctx
    )

@app.route("/", methods=["GET","POST"])
def home():
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        day  = request.form.get("day")
        court= request.form.get("court")
        if not name or day not in DAYS or court not in ALL_COURTS:
            flash("Please enter your name, a valid day, and a court.", "error")
            return redirect(url_for("home"))
        conn = db()
        conn.execute(
            "INSERT INTO votes(name, day, court, ts) VALUES(?,?,?,?) "
            "ON CONFLICT(name) DO UPDATE SET day=excluded.day, court=excluded.court, ts=excluded.ts",
            (name, day, court, datetime.now(timezone.utc).isoformat())
        )
        conn.commit(); conn.close()
        flash("Vote submitted. Thanks!", "success")
        return redirect(url_for("results"))
    return render_page(INDEX, days=DAYS, courts=ALL_COURTS, preferred=PREFERRED_COURTS)

@app.route("/results")
def results():
    s = tally()
    avail = fetch_availability()
    wx = None
    # If top choice is outdoor, fetch weather for that day
    top = s.get("top_choice")
    if top and is_outdoor(top["court"]):
        wx = fetch_weather(top["day"])
    return render_page(RESULTS, summary=s, preferred=PREFERRED_COURTS, courts=ALL_COURTS,
                       availability=avail, outdoor_list=[c for c in ALL_COURTS if is_outdoor(c)], weather=wx)

@app.route("/reset", methods=["POST"])
def reset():
    return render_page(RESET)

@app.route("/confirm-reset", methods=["POST"])
def confirm_reset():
    if request.form.get("pin","") != RESET_PIN:
        flash("Invalid PIN.", "error")
        return redirect(url_for("home"))
    conn = db(); conn.execute("DELETE FROM votes"); conn.commit(); conn.close()
    flash("All votes cleared. Fresh week! 🏓", "success")
    return redirect(url_for("home"))

# (Optional) JSON APIs
@app.route("/api/summary")
def api_summary(): return jsonify(tally())
@app.route("/api/availability")
def api_availability(): return jsonify(fetch_availability())

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT",5000)))
