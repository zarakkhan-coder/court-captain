# CourtCaptain — Votes + Names, 4-player rule, Weather, Manual Availability, Bookmarklet Importer, Booking Button

from flask import Flask, request, redirect, url_for, render_template_string, flash, jsonify
from datetime import datetime, timezone, date
import os, sqlite3, requests, json

APP_NAME = "CourtCaptain"
PREFERRED_COURTS = ["Court 1", "Court 2", "Court 3", "Court 4"]
ALL_COURTS = PREFERRED_COURTS + ["Court 5", "Court 6", "Court 7", "Outdoor A", "Outdoor B", "Other"]
DAYS = ["Saturday", "Sunday"]

# --- Environment (Render → Environment) ---
RESET_PIN   = os.environ.get("RESET_PIN", "1234")
SECRET_KEY  = os.environ.get("SECRET_KEY", "replace-me")
BOOKING_URL = os.environ.get("BOOKING_URL", "https://walmart.clubautomation.com/event/reserve-court-new")
LAT = float(os.environ.get("WALTON_LAT", "36.372"))
LON = float(os.environ.get("WALTON_LON", "-94.208"))

DB_PATH = "data.db"

app = Flask(__name__)
app.secret_key = SECRET_KEY

# ---------- DB ----------
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = db()
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS votes(
        name  TEXT PRIMARY KEY,
        day   TEXT NOT NULL,
        court TEXT NOT NULL,
        ts    TEXT NOT NULL
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS availability(
        day   TEXT NOT NULL,
        court TEXT NOT NULL,
        slot  TEXT NOT NULL
    )""")
    conn.commit(); conn.close()
init_db()

# ---------- helpers ----------
def pref_rank(court):
    return PREFERRED_COURTS.index(court) if court in PREFERRED_COURTS else 100 + ALL_COURTS.index(court)

def day_rank(day): return 0 if day == "Saturday" else 1

def is_outdoor(court:str)->bool: return "outdoor" in court.lower()

def tally_with_names():
    conn = db()
    rows = conn.execute("SELECT name, day, court FROM votes").fetchall()
    conn.close()
    unique_players = set()
    buckets = {}
    for r in rows:
        nm = (r["name"] or "").strip()
        if not nm: continue
        unique_players.add(nm.lower())
        key = (r["day"], r["court"])
        if key not in buckets: buckets[key] = {"votes":0, "names":[]}
        buckets[key]["votes"] += 1
        buckets[key]["names"].append(nm)
    ranked = sorted(buckets.items(), key=lambda kv:(-kv[1]["votes"], pref_rank(kv[0][1]), day_rank(kv[0][0])))
    top = None
    if ranked:
        (d,c), info = ranked[0]
        top = {"day":d, "court":c, "votes":info["votes"], "names":sorted(info["names"])}
    counts = [{"day":d, "court":c, "votes":info["votes"], "names":sorted(info["names"])} for (d,c),info in ranked]
    return {"total_players":len(unique_players), "counts":counts, "top_choice":top, "booking_possible":len(unique_players)>=4}

def fetch_weather_all():
    out = {}
    try:
        url = ("https://api.open-meteo.com/v1/forecast"
               f"?latitude={LAT}&longitude={LON}"
               "&daily=temperature_2m_max,temperature_2m_min,precipitation_probability_max"
               "&forecast_days=7&timezone=auto")
        r = requests.get(url, timeout=8); r.raise_for_status()
        d = r.json().get("daily", {})
        times = d.get("time", [])
        tmax  = d.get("temperature_2m_max", [])
        tmin  = d.get("temperature_2m_min", [])
        pop   = d.get("precipitation_probability_max", [])
        for i, ds in enumerate(times):
            y,m,dd = map(int, ds.split("-"))
            wname = date(y,m,dd).strftime("%A")
            if wname in ("Saturday","Sunday"):
                out[wname] = {"date":ds, "tmax":tmax[i], "tmin":tmin[i], "pop":pop[i]}
        return out
    except Exception:
        return out

def get_availability_times():
    """Return {'Saturday': {'Court 1':[...], ...}, 'Sunday': {...}} from DB."""
    result = {d: {c: [] for c in ALL_COURTS} for d in DAYS}
    conn = db()
    rows = conn.execute("SELECT day, court, slot FROM availability").fetchall()
    conn.close()
    for r in rows:
        day, court, slot = r["day"], r["court"], r["slot"]
        if day in result and court in result[day]:
            if slot not in result[day][court]:
                result[day][court].append(slot)
    for d in DAYS:
        for c in ALL_COURTS:
            result[d][c].sort()
    return result

def clear_all_availability():
    conn = db(); conn.execute("DELETE FROM availability"); conn.commit(); conn.close()

def bulk_upsert_availability(day:str, mapping:dict):
    """
    mapping: {'Court 1': ['9:00–10:00', ...], 'Court 2': [...], ...}
    Clears existing rows for that day and inserts fresh.
    """
    if day not in DAYS: return
    conn = db()
    conn.execute("DELETE FROM availability WHERE day=?", (day,))
    for court, slots in mapping.items():
        if court not in ALL_COURTS: continue
        for s in slots:
            if not s: continue
            conn.execute("INSERT INTO availability(day, court, slot) VALUES(?,?,?)", (day, court, s))
    conn.commit(); conn.close()

# ---------- templates ----------
BASE = """
<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{{ app_name }}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;600;800&display=swap" rel="stylesheet">
<style>
:root{--bg:#0b1220;--card:#121a2b;--glass:rgba(255,255,255,.06);--text:#e8eefc;--muted:#9fb0d6;--primary:#5ea1ff;--accent:#22d3ee;--success:#22c55e;--warn:#f59e0b;--danger:#ef4444;--border:#1f2a44;--shadow:0 12px 28px rgba(0,0,0,.35)}
*{box-sizing:border-box} html,body{margin:0;padding:0;background:linear-gradient(180deg,#0a0f1a,#0b1220);color:var(--text);font-family:Inter,system-ui,Segoe UI,Roboto,Arial,sans-serif}
.page{max-width:1000px;margin:24px auto;padding:0 18px}
.header{position:sticky;top:0;background:var(--glass);backdrop-filter:blur(10px);border:1px solid var(--border);border-radius:14px;padding:14px 18px;margin-bottom:18px;display:flex;align-items:center;justify-content:space-between;box-shadow:var(--shadow);animation: fadeIn .5s ease}
.brand{display:flex;gap:10px;align-items:center}.logo{font-size:24px}.title{font-weight:800}
.nav{display:flex;gap:12px}.nav a{padding:8px 12px;border-radius:10px;color:var(--muted);text-decoration:none;transition:.2s}
.nav a:hover{background:rgba(255,255,255,.06);color:var(--text);transform: translateY(-1px)}
.flash{margin:14px 0;padding:12px 14px;border-radius:12px;border:1px solid var(--border)}.flash.success{background:rgba(34,197,94,.12)}.flash.error{background:rgba(239,68,68,.12)}
.card{background:var(--card);border:1px solid var(--border);border-radius:16px;padding:20px;box-shadow:var(--shadow);margin-bottom:18px;transition: transform .15s ease, box-shadow .15s ease;animation: fadeIn .4s ease}
.card:hover{transform: translateY(-2px);box-shadow:0 14px 30px rgba(0,0,0,.4)}
.glass{background:var(--glass);border:1px solid var(--border);border-radius:14px;padding:18px;margin-bottom:18px;animation: fadeIn .4s ease}
.heading{margin:0 0 6px 0;font-size:28px}.sub{margin:0;color:var(--muted)}
.form{display:flex;flex-direction:column;gap:14px}.grid{display:grid;grid-template-columns:1fr 1fr 1fr;gap:14px}@media(max-width:840px){.grid{grid-template-columns:1fr}}
label span{display:block;margin-bottom:8px;color:var(--muted)}
input,select{width:100%;padding:12px;border-radius:10px;border:1px solid var(--border);background:#0f1627;color:var(--text)} input:focus,select:focus{outline:none;border-color:var(--primary)}
.btn{display:inline-flex;align-items:center;gap:8px;padding:12px 14px;border-radius:12px;border:1px solid var(--border);cursor:pointer;background:#0f1627;color:var(--text);transition:.2s}
.btn.primary{background:linear-gradient(135deg,var(--primary),#7cd2ff);color:#06101f;border:none}
.btn.primary:hover{filter:brightness(1.05);transform: translateY(-1px)}
.btn.link{background:transparent;border:none;color:var(--primary)}
.btn.danger{background:linear-gradient(135deg,#ef4444,#f87171);border:none}
.btn.danger:hover{filter:brightness(1.05);transform: translateY(-1px)}
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
<div class="header"><div class="brand"><div class="logo">🏓</div><div class="title">{{ app_name }}</div></div>
<div class="nav">
  <a href="{{ url_for('home') }}">Vote</a>
  <a href="{{ url_for('results') }}">Results</a>
  <a href="{{ url_for('admin_availability') }}">Admin</a>
  <a href="{{ url_for('admin_bookmarklet') }}">Import</a>
</div></div>
{% with messages = get_flashed_messages(with_categories=true) %}{% for cat,msg in messages %}<div class="flash {{ cat }}">{{ msg }}</div>{% endfor %}{% endwith %}
{{ content|safe }}
<div class="footer">© {{ app_name }}</div>
</div></body></html>
"""

INDEX = """
<div class="glass"><h1 class="heading">Weekend Pickleball Poll — Walton Fitness Centre</h1>
<p class="sub">Vote by <b>Wednesday 6pm</b>. Need <b>4+ players</b> to book. Preferred courts: <b>1–4</b>.</p></div>
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
      <label><span>Preferred court</span>
        <select name="court" required>
          <option value="" disabled selected>Choose</option>
          {% for c in courts %}<option value="{{ c }}">{{ c }}{% if c in preferred %} ★{% endif %}</option>{% endfor %}
        </select>
      </label>
    </div>
    <div style="display:flex;gap:10px;flex-wrap:wrap">
      <button class="btn primary" type="submit">Submit Vote</button>
      <a class="btn link" href="{{ url_for('results') }}">See Results</a>
    </div>
  </form>
</div>
"""

RESULTS = """
{% set s = summary %}
<div class="glass">
  <h1 class="heading">Dashboard</h1>
  <div class="kpi">
    <div class="pill">Players: <b>{{ s.total_players }}</b></div>
    {% if s.booking_possible %}
      <div class="pill">Status: <b>Booking possible (≥4)</b></div>
    {% else %}
      <div class="pill">Status: <b>Need ≥4</b></div>
    {% endif %}
    <div class="pill">Weather Sat:
      {% if wx.Saturday %} <span class="badge">{{ wx.Saturday.tmin|int }}–{{ wx.Saturday.tmax|int }}°C • {{ wx.Saturday.pop|default('?') }}% rain</span>
      {% else %} <span class="badge">—</span>{% endif %}
    </div>
    <div class="pill">Weather Sun:
      {% if wx.Sunday %} <span class="badge">{{ wx.Sunday.tmin|int }}–{{ wx.Sunday.tmax|int }}°C • {{ wx.Sunday.pop|default('?') }}% rain</span>
      {% else %} <span class="badge">—</span>{% endif %}
    </div>
  </div>
</div>

{% if s.top_choice %}
<div class="card" style="border-color:#22d3ee">
  <h3 style="margin-top:0">Top Choice</h3>
  <p style="font-size:20px">
    <b>{{ s.top_choice.day }}</b> on <b>{{ s.top_choice.court }}</b>
    <span class="sub">({{ s.top_choice.votes }} votes)</span>
  </p>
  {% if s.top_choice.names %}
    <div class="voters">Voters: {{ s.top_choice.names | join(', ') }}</div>
  {% endif %}
  <form method="POST" action="{{ url_for('book_court') }}" style="margin-top:12px;">
    <input type="hidden" name="day" value="{{ s.top_choice.day }}">
    <input type="hidden" name="court" value="{{ s.top_choice.court }}">
    <button class="btn primary" type="submit">Book Court</button>
  </form>
  <p class="note">Ranking: votes → preferred courts (1–4) → Saturday over Sunday.</p>
</div>
{% endif %}

<div class="card">
  <h3 style="margin-top:0">Vote Breakdown (with names)</h3>
  <div class="table">
    <div class="row head"><div>Day</div><div>Court & Voters</div><div>Votes</div></div>
    {% for item in s.counts %}
      <div class="row">
        <div>{{ item.day }}</div>
        <div>
          {{ item.court }}{% if item.court in preferred %} ★{% endif %}
          {% if item.names and item.names|length > 0 %}
            <div class="voters">Voters: {{ item.names | join(', ') }}</div>
          {% endif %}
        </div>
        <div>{{ item.votes }}</div>
      </div>
    {% else %}
      <div class="row"><div>No votes yet. Share the link!</div><div></div><div></div></div>
    {% endfor %}
  </div>
</div>

<div class="card">
  <h3 style="margin-top:0">Pickleball Court Availability</h3>
  {% for d in days %}
    <h4>{{ d }}</h4>
    <div class="table">
      <div class="row head"><div>Court</div><div>Open Slots</div><div></div></div>
      {% for c in courts %}
        <div class="row">
          <div>{{ c }}</div>
          <div>{% if avail[d][c] and avail[d][c]|length > 0 %}{{ avail[d][c] | join(', ') }}{% else %}—{% endif %}</div>
          <div>{% if c in preferred %}Preferred{% endif %}</div>
        </div>
      {% endfor %}
    </div>
  {% endfor %}
</div>
"""

ADMIN_AVAIL_TPL = """
<div class="glass">
  <h1 class="heading">Admin: Court Availability</h1>
  <p class="sub">Add time slots per court and day. Use your PIN below to clear all if needed.</p>
</div>

<div class="card">
  <form method="POST" class="form">
    <div class="grid">
      <label><span>Day</span>
        <select name="day" required>
          {% for d in days %}<option value="{{ d }}">{{ d }}</option>{% endfor %}
        </select>
      </label>
      <label><span>Court</span>
        <select name="court" required>
          {% for c in courts %}<option value="{{ c }}">{{ c }}</option>{% endfor %}
        </select>
      </label>
      <label><span>Time slot (e.g., 9:00–10:00)</span>
        <input name="slot" placeholder="9:00–10:00" required>
      </label>
    </div>
    <button class="btn primary" type="submit">Add Slot</button>
    <a class="btn link" href="{{ url_for('results') }}">Back to Dashboard</a>
  </form>
</div>

<div class="card">
  <h3 style="margin-top:0">Current Availability</h3>
  {% for d in days %}
    <h4>{{ d }}</h4>
    <div class="table">
      <div class="row head"><div>Court</div><div>Slots</div><div></div></div>
      {% for c in courts %}
        <div class="row">
          <div>{{ c }}</div>
          <div>
            {% if data[d][c] and data[d][c]|length > 0 %}
              {{ data[d][c] | join(', ') }}
            {% else %}—{% endif %}
          </div>
          <div>{% if c in preferred %}Preferred{% endif %}</div>
        </div>
      {% endfor %}
    </div>
  {% endfor %}
</div>

<div class="card">
  <h3 style="margin-top:0">Clear All Availability</h3>
  <form method="POST" action="{{ url_for('admin_availability_clear') }}" class="form" onsubmit="return confirm('Clear all slots?')">
    <label><span>PIN</span><input type="password" name="pin" required placeholder="Enter PIN"></label>
    <button class="btn danger" type="submit">Clear All</button>
  </form>
</div>
"""

# --- Bookmarklet Import Page ---
ADMIN_BM_TPL = """
<div class="glass">
  <h1 class="heading">Import from Club Automation (Bookmarklet)</h1>
  <p class="sub">1) Drag the button below to your bookmarks bar.<br>
  2) Go to the Club Automation page (logged in). Choose <b>Saturday</b> or <b>Sunday</b>.<br>
  3) Click the bookmark. It will send visible courts/slots into this app.</p>
</div>

<div class="card">
  <h3 style="margin-top:0">Bookmarklet</h3>
  <p>Drag this to your bookmarks bar:</p>
  <p>
    <a class="btn primary" href="{{ bookmarklet_js }}">Court Import</a>
  </p>
  <p class="note">If your site names differ (e.g., "Court 1" vs "Pickleball Court 1"), the importer tries to match. You can edit names after import via Admin.</p>
  <a class="btn link" href="{{ url_for('results') }}">Back to Dashboard</a>
</div>
"""

def render_view(tpl, **ctx):
    body = render_template_string(tpl, **ctx)
    return render_template_string(BASE, content=body, **ctx, app_name=APP_NAME)

# ---------- routes ----------
@app.get("/health")
def health(): return jsonify(ok=True), 200

@app.route("/", methods=["GET","POST"])
def home():
    if request.method == "POST":
        name  = (request.form.get("name") or "").strip()
        day   = request.form.get("day")
        court = request.form.get("court")
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
    return render_view(INDEX, days=DAYS, courts=ALL_COURTS, preferred=PREFERRED_COURTS)

@app.get("/results")
def results():
    s = tally_with_names()
    wx = fetch_weather_all()
    avail = get_availability_times()
    return render_view(
        RESULTS,
        summary=s, preferred=PREFERRED_COURTS, courts=ALL_COURTS,
        days=DAYS, wx=wx, avail=avail
    )

@app.post("/reset")
def reset():
    # simple inline template for reset prompt
    return render_view("""<div class="card"><h2>Reset Votes (Admin)</h2>
        <form method="POST" action="{{ url_for('confirm_reset') }}" class="form">
          <label><span>Enter PIN</span><input type="password" name="pin" placeholder="PIN" required></label>
          <button class="btn danger" type="submit">Confirm Reset</button>
          <a class="btn link" href="{{ url_for('home') }}">Cancel</a>
        </form></div>""")

@app.post("/confirm-reset")
def confirm_reset():
    if request.form.get("pin","") != RESET_PIN:
        flash("Invalid PIN.", "error")
        return redirect(url_for("home"))
    conn = db(); conn.execute("DELETE FROM votes"); conn.commit(); conn.close()
    flash("All votes cleared. Fresh week! 🏓", "success")
    return redirect(url_for("home"))

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

# ----- Admin Availability Editor -----
@app.get("/admin/availability")
def admin_availability():
    data = get_availability_times()
    return render_view(ADMIN_AVAIL_TPL, days=DAYS, courts=ALL_COURTS, preferred=PREFERRED_COURTS, data=data)

@app.post("/admin/availability")
def admin_availability_post():
    day = (request.form.get("day") or "").strip()
    court = (request.form.get("court") or "").strip()
    slot = (request.form.get("slot") or "").strip()
    if day not in DAYS or court not in ALL_COURTS or not slot:
        flash("Please choose a valid day, court, and slot.", "error")
        return redirect(url_for("admin_availability"))
    conn = db()
    exists = conn.execute("SELECT 1 FROM availability WHERE day=? AND court=? AND slot=?", (day, court, slot)).fetchone()
    if not exists:
        conn.execute("INSERT INTO availability(day, court, slot) VALUES(?,?,?)", (day, court, slot))
        conn.commit()
    conn.close()
    flash("Time slot added.", "success")
    return redirect(url_for('admin_availability'))

@app.post("/admin/availability/clear")
def admin_availability_clear():
    pin = request.form.get("pin","")
    if pin != RESET_PIN:
        flash("Invalid PIN.", "error")
        return redirect(url_for("admin_availability"))
    clear_all_availability()
    flash("All availability cleared.", "success")
    return redirect(url_for("admin_availability"))

# ----- Bookmarklet Import -----
def _bookmarklet_js(origin_base:str):
    """
    Builds a bookmarklet that runs in Club Automation page context and POSTs
    found courts/slots to our /ingest/availability endpoint. Assumes the user
    has selected either Saturday or Sunday on that page; the script asks which.
    """
    # NOTE: You can tune the selectors below once you inspect Club Automation DOM.
    # We start with generic guesses: rows that contain court names and slot labels.
    js = f"""
    javascript:(function(){{
      try {{
        var day = window.prompt('Enter day to import for (Saturday or Sunday):','Saturday');
        if(!day) return;
        day = day.trim();
        if(!/^(Saturday|Sunday)$/i.test(day)) {{ alert('Please enter Saturday or Sunday'); return; }}
        var DAY = day[0].toUpperCase()+day.slice(1).toLowerCase();

        // Heuristics: try to find court containers and slot labels
        // Adjust selectors to match Club Automation structure as needed.
        var nodes = document.querySelectorAll('.court, .resource, .facility, [data-court], [data-resource], .reservation-court');
        if(!nodes.length) nodes = document.querySelectorAll('div');

        var data = {{}};
        function normalizeCourtName(txt) {{
          var t = (txt||'').trim();
          // Try to map to our known labels
          var labels = {json.dumps(ALL_COURTS)};
          for (var i=0;i<labels.length;i++) {{
            var L = labels[i];
            if (t.toLowerCase().indexOf(L.toLowerCase())>=0) return L;
          }}
          return null;
        }}

        nodes.forEach(function(n){{
          var text = n.getAttribute('data-name') || n.getAttribute('data-court') || n.getAttribute('data-resource') || n.textContent || '';
          var cname = normalizeCourtName(text);
          if(!cname) return;
          if(!data[cname]) data[cname] = [];
          // Find times near this node
          var slots = [];
          var slotNodes = n.querySelectorAll('.slot, .time-slot, .slot-label, .reservation-time, [data-time], time');
          if(!slotNodes.length) {{
            // fallback: search nearby siblings
            slotNodes = (n.parentElement||document).querySelectorAll('.slot, .time-slot, .slot-label, .reservation-time, [data-time], time');
          }}
          slotNodes.forEach(function(s){{
            var v = s.getAttribute('data-time') || s.textContent || '';
            v = v.replace(/\\s+/g,' ').trim();
            if(v && slots.indexOf(v)<0) slots.push(v);
          }});
          // If no slots at this node, try detecting time-like strings inside text
          if(!slots.length) {{
            var m = text.match(/\\b(\\d{{1,2}}:?\\d{{0,2}}\\s?(?:AM|PM|am|pm)?\\s?[-–—]\\s?\\d{{1,2}}:?\\d{{0,2}}\\s?(?:AM|PM|am|pm)?)/g);
            if(m) slots = m.map(function(x){{return x.trim();}});
          }}
          slots.forEach(function(s){{ if(data[cname].indexOf(s)<0) data[cname].push(s); }});
        }});

        var payload = JSON.stringify({{day: DAY, mapping: data}});
        fetch('{origin_base}/ingest/availability', {{
          method: 'POST',
          headers: {{'Content-Type': 'application/json'}},
          body: payload,
          credentials: 'omit'
        }}).then(function(res) {{
          if(!res.ok) throw new Error('HTTP '+res.status);
          return res.json();
        }}).then(function(resp){{
          alert('Imported '+ (resp.imported||0) +' slots for '+DAY+' across '+ (resp.courts||0) +' courts.');
        }}).catch(function(err){{
          console.error(err);
          alert('Import failed: '+err.message+' — You can still add slots in Admin page.');
        }});
      }} catch(e) {{
        console.error(e);
        alert('Import error: '+e.message);
      }}
    }})();"""
    # Minify very lightly (remove line breaks)
    return js.replace("\n"," ").replace("  "," ")

@app.get("/admin/bookmarklet")
def admin_bookmarklet():
    # Build absolute origin for this app (used by the bookmarklet POST)
    origin = request.url_root.rstrip("/")
    bm_js = _bookmarklet_js(origin)
    return render_view(ADMIN_BM_TPL, bookmarklet_js=bm_js)

@app.post("/ingest/availability")
def ingest_availability():
    """
    Receives JSON: { "day": "Saturday"|"Sunday", "mapping": {"Court 1": ["9:00–10:00", ...], ...} }
    Saves into DB for the given day (replacing existing).
    """
    try:
        data = request.get_json(force=True, silent=True) or {}
        day = (data.get("day") or "").strip()
        mapping = data.get("mapping") or {}
        if day not in DAYS:
            return jsonify(ok=False, error="Invalid day"), 400
        # Clean mapping keys to our known court labels
        clean = {}
        for k, v in mapping.items():
            if not isinstance(v, list): continue
            # match incoming name to one of our known labels
            label = next((L for L in ALL_COURTS if L.lower() in (k or "").lower()), None)
            if not label: continue
            # normalize slot strings
            slots = []
            for s in v:
                s = (s or "").strip()
                if s and s not in slots:
                    slots.append(s)
            clean[label] = slots
        # Insert
        before = get_availability_times().get(day, {})
        bulk_upsert_availability(day, clean)
        after = get_availability_times().get(day, {})
        imported_slots = sum(len(v) for v in after.values())
        courts_with_data = sum(1 for v in after.values() if v)
        return jsonify(ok=True, imported=imported_slots, courts=courts_with_data)
    except Exception as e:
        return jsonify(ok=False, error=str(e)), 500

if __name__ == "__main__":
    # IMPORTANT: bind to host/port from your platform (Render sets PORT)
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
