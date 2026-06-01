import json
import os
import secrets
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from functools import wraps

from flask import Flask, Response, jsonify, redirect, request, send_from_directory, make_response

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODELS_URL = "https://api.groq.com/openai/v1/models"
DEFAULT_MODEL = "llama-3.1-8b-instant"

APP_COOKIE = "cquai_beta_auth"
CONSENT_COOKIE = "cquai_consent_ok"
ADMIN_COOKIE = "cquai_admin_auth"
SESSION_COOKIE = "cquai_session_id"

app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path="")

def env(name, default=""):
    return os.environ.get(name, default).strip()

def now_iso():
    return datetime.now(timezone.utc).isoformat()

def safe_json_loads(value):
    if not value:
        return None
    try:
        return json.loads(value)
    except Exception:
        return None

def get_session_id():
    sid = request.cookies.get(SESSION_COOKIE)
    if not sid:
        sid = secrets.token_urlsafe(18)
    return sid

def set_session_cookie(resp, sid=None):
    sid = sid or get_session_id()
    resp.set_cookie(
        SESSION_COOKIE,
        sid,
        max_age=60 * 60 * 24 * 365,
        httponly=True,
        secure=True,
        samesite="Lax",
    )
    return resp

def ua_parse(user_agent):
    ua = (user_agent or "").lower()
    if "mobile" in ua or "android" in ua or "iphone" in ua:
        device = "mobile"
    elif "tablet" in ua or "ipad" in ua:
        device = "tablet"
    else:
        device = "desktop"

    if "edg/" in ua:
        browser = "Edge"
    elif "chrome/" in ua and "safari/" in ua:
        browser = "Chrome"
    elif "firefox/" in ua:
        browser = "Firefox"
    elif "safari/" in ua and "chrome/" not in ua:
        browser = "Safari"
    else:
        browser = "unknown"

    if "windows" in ua:
        os_name = "Windows"
    elif "android" in ua:
        os_name = "Android"
    elif "iphone" in ua or "ipad" in ua or "ios" in ua:
        os_name = "iOS"
    elif "mac os" in ua or "macintosh" in ua:
        os_name = "macOS"
    elif "linux" in ua:
        os_name = "Linux"
    else:
        os_name = "unknown"

    return device, browser, os_name

def get_geo():
    # Render/Cloudflare style headers may or may not exist.
    # We save only approximate values if provided by platform/proxy.
    country = request.headers.get("CF-IPCountry") or request.headers.get("X-Geo-Country") or ""
    region = request.headers.get("X-Geo-Region") or ""
    city = request.headers.get("X-Geo-City") or ""
    tz = request.headers.get("X-Geo-Timezone") or ""
    source = "headers" if any([country, region, city, tz]) else ""
    return {
        "geo_country": country,
        "geo_region": region,
        "geo_city": city,
        "geo_timezone": tz,
        "geo_source": source
    }

def tech_data():
    ua = request.headers.get("User-Agent", "")
    device, browser, os_name = ua_parse(ua)
    data = {
        "user_agent": ua,
        "device_type": device,
        "browser": browser,
        "os": os_name,
        "language": request.headers.get("Accept-Language", "")[:160],
    }
    data.update(get_geo())
    return data

def supabase_request(method, path, payload=None):
    supabase_url = env("SUPABASE_URL").rstrip("/")
    supabase_key = env("SUPABASE_SERVICE_ROLE_KEY")

    if not supabase_url or not supabase_key:
        return None, "Supabase non configurato: mancano SUPABASE_URL o SUPABASE_SERVICE_ROLE_KEY."

    url = f"{supabase_url}/rest/v1/{path.lstrip('/')}"
    headers = {
        "apikey": supabase_key,
        "Authorization": f"Bearer {supabase_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "cquAI Render Dashboard",
    }

    data = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        if method in ("POST", "PATCH"):
            headers["Prefer"] = "return=representation,resolution=merge-duplicates"

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=35) as resp:
            content = resp.read().decode("utf-8", errors="replace")
            if not content:
                return None, None
            try:
                return json.loads(content), None
            except Exception:
                return content, None
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        return None, f"Supabase HTTP {e.code}: {detail}"
    except Exception as e:
        return None, f"Supabase error: {e}"

def ensure_session(extra=None):
    sid = get_session_id()
    tech = tech_data()

    # FIX: Supabase PATCH su una sessione non ancora esistente può rispondere OK
    # ma aggiornare 0 righe. Così gli eventi non vengono salvati.
    # Usiamo UPSERT su session_id: crea se manca, aggiorna se esiste.
    payload = {
        "session_id": sid,
        "first_seen_at": now_iso(),
        "last_seen_at": now_iso(),
        **tech,
        "meta": extra or {}
    }

    path = "cquai_sessions?on_conflict=session_id"
    _, err = supabase_request("POST", path, payload)
    if err:
        # Fallback: crea o aggiorna in modo esplicito.
        _, create_err = supabase_request("POST", "cquai_sessions", payload)
        if create_err:
            update_payload = {
                "last_seen_at": now_iso(),
                **tech,
                "meta": extra or {}
            }
            supabase_request("PATCH", "cquai_sessions?session_id=eq." + urllib.parse.quote(sid), update_payload)
    return sid

def update_session_counts(sid):
    # Lightweight recalculation based on recent events for this session.
    path = "cquai_events?select=event_type,prompt_tokens,completion_tokens,total_tokens&session_id=eq." + urllib.parse.quote(sid)
    rows, err = supabase_request("GET", path)
    if err or not isinstance(rows, list):
        return
    message_count = sum(1 for r in rows if r.get("event_type") == "chat_message")
    click_count = sum(1 for r in rows if r.get("event_type") == "button_click")
    error_count = sum(1 for r in rows if "error" in (r.get("event_type") or ""))
    prompt_tokens = sum(int(r.get("prompt_tokens") or 0) for r in rows)
    completion_tokens = sum(int(r.get("completion_tokens") or 0) for r in rows)
    total_tokens = sum(int(r.get("total_tokens") or 0) for r in rows)
    status = "error" if error_count else "active"
    payload = {
        "message_count": message_count,
        "click_count": click_count,
        "error_count": error_count,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "last_seen_at": now_iso(),
        "status": status
    }
    supabase_request("PATCH", "cquai_sessions?session_id=eq." + urllib.parse.quote(sid), payload)

def log_event(event_type, **kwargs):
    sid = ensure_session()
    tech = tech_data()
    payload = {
        "session_id": sid,
        "event_type": event_type,
        "page": kwargs.get("page") or request.path,
        "click_target": kwargs.get("click_target"),
        "click_label": kwargs.get("click_label"),
        "user_text": kwargs.get("user_text"),
        "assistant_text": kwargs.get("assistant_text"),
        "closing_summary": kwargs.get("closing_summary"),
        "memory_items": kwargs.get("memory_items"),
        "model": kwargs.get("model"),
        "prompt_tokens": kwargs.get("prompt_tokens"),
        "completion_tokens": kwargs.get("completion_tokens"),
        "total_tokens": kwargs.get("total_tokens"),
        "usage": kwargs.get("usage"),
        "error_code": kwargs.get("error_code"),
        "error_detail": kwargs.get("error_detail"),
        "meta": kwargs.get("meta") or {},
        **tech
    }
    _, err = supabase_request("POST", "cquai_events", payload)
    update_session_counts(sid)
    return err

def groq_request(url, api_key, payload=None):
    headers = {
        "Authorization": f"Bearer {api_key}",
        "User-Agent": "Mozilla/5.0 cquAI Render",
        "Accept": "application/json",
    }

    if payload is None:
        req = urllib.request.Request(url, headers=headers, method="GET")
    else:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")

    with urllib.request.urlopen(req, timeout=120) as resp:
        content = resp.read()
        content_type = resp.headers.get("Content-Type", "application/json; charset=utf-8")
        return resp.status, content_type, content

def beta_password_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        beta_password = env("BETA_PASSWORD")
        if not beta_password:
            return fn(*args, **kwargs)
        if request.cookies.get(APP_COOKIE) == beta_password:
            return fn(*args, **kwargs)
        return redirect("/login")
    return wrapper

def consent_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if request.cookies.get(CONSENT_COOKIE) == "yes":
            return fn(*args, **kwargs)
        return redirect("/consent")
    return wrapper

def admin_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        admin_password = env("ADMIN_PASSWORD")
        if not admin_password:
            return Response("ADMIN_PASSWORD non configurata su Render.", status=500)
        if request.cookies.get(ADMIN_COOKIE) == admin_password:
            return fn(*args, **kwargs)
        return redirect("/admin/login")
    return wrapper

@app.get("/")
@beta_password_required
@consent_required
def index():
    sid = ensure_session({"route": "home"})
    log_event("home_view", page="/")
    resp = make_response(send_from_directory(STATIC_DIR, "index.html"))
    return set_session_cookie(resp, sid)

@app.get("/index.html")
@beta_password_required
@consent_required
def index_html():
    return index()

@app.get("/login")
def login_page():
    html = (STATIC_DIR / "login.html").read_text(encoding="utf-8")
    html = html.replace("{%ERROR%}", "")
    resp = make_response(Response(html, mimetype="text/html"))
    return set_session_cookie(resp)

@app.post("/login")
def login_post():
    beta_password = env("BETA_PASSWORD")
    password = request.form.get("password", "")
    if beta_password and password == beta_password:
        sid = ensure_session({"beta_login": "ok"})
        log_event("beta_login_ok")
        supabase_request("PATCH", "cquai_sessions?session_id=eq." + urllib.parse.quote(sid), {
            "beta_logged_in": True,
            "beta_logged_in_at": now_iso()
        })
        resp = make_response(redirect("/consent"))
        resp.set_cookie(APP_COOKIE, beta_password, max_age=60*60*24*30, httponly=True, secure=True, samesite="Lax")
        return set_session_cookie(resp, sid)
    log_event("beta_login_failed")
    html = (STATIC_DIR / "login.html").read_text(encoding="utf-8")
    html = html.replace("{%ERROR%}", '<div class="err">Password errata.</div>')
    return Response(html, status=401, mimetype="text/html")

@app.get("/consent")
@beta_password_required
def consent_page():
    resp = make_response(send_from_directory(STATIC_DIR, "consent.html"))
    return set_session_cookie(resp)

@app.post("/consent")
@beta_password_required
def consent_post():
    choice = request.form.get("choice")
    sid = ensure_session()
    if choice == "accept":
        log_event("consent_accepted")
        supabase_request("PATCH", "cquai_sessions?session_id=eq." + urllib.parse.quote(sid), {
            "consent_accepted": True,
            "consent_accepted_at": now_iso()
        })
        resp = make_response(redirect("/"))
        resp.set_cookie(CONSENT_COOKIE, "yes", max_age=60*60*24*30, httponly=True, secure=True, samesite="Lax")
        return set_session_cookie(resp, sid)
    log_event("consent_rejected")
    supabase_request("PATCH", "cquai_sessions?session_id=eq." + urllib.parse.quote(sid), {
        "status": "rejected_consent",
        "closed_at": now_iso()
    })
    return Response("Hai scelto di non accettare. La beta non può essere usata senza consenso.", status=403, mimetype="text/plain")

@app.get("/logout")
def logout():
    resp = make_response(redirect("/login"))
    resp.delete_cookie(APP_COOKIE)
    resp.delete_cookie(CONSENT_COOKIE)
    return resp

@app.get("/admin/login")
def admin_login_page():
    return Response("""<!doctype html>
<html lang="it"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>cquAI admin</title>
<style>
body{margin:0;background:#17130f;color:#fff;font-family:system-ui,-apple-system,Segoe UI,Arial,sans-serif;display:grid;place-items:center;min-height:100vh}
.card{width:min(420px,calc(100vw - 32px));background:#252019;border:1px solid #514333;border-radius:28px;padding:28px;box-shadow:0 18px 50px rgba(0,0,0,.35)}
.logo{font-size:42px;font-weight:900;letter-spacing:-.075em;margin-bottom:8px}.logo span{color:#f28b78}
p{line-height:1.45;color:#d5cab9}
input{width:100%;box-sizing:border-box;border:1px solid #6d604f;border-radius:16px;padding:14px 16px;font-size:18px;background:#fff;color:#111}
button{width:100%;margin-top:12px;border:0;border-radius:16px;padding:14px 16px;font-size:17px;font-weight:700;background:#fff;color:#16130f;cursor:pointer}
</style></head><body>
<form class="card" method="post" action="/admin/login">
<div class="logo">cqu<span>AI</span> admin</div>
<p>Dashboard privata.</p>
<input type="password" name="password" placeholder="Password admin" autofocus>
<button>Entra</button>
</form></body></html>""", mimetype="text/html")

@app.post("/admin/login")
def admin_login_post():
    admin_password = env("ADMIN_PASSWORD")
    password = request.form.get("password", "")
    if admin_password and password == admin_password:
        resp = make_response(redirect("/admin"))
        resp.set_cookie(ADMIN_COOKIE, admin_password, max_age=60*60*24*30, httponly=True, secure=True, samesite="Lax")
        return resp
    return Response("Password admin errata.", status=401)

@app.get("/admin/logout")
def admin_logout():
    resp = make_response(redirect("/admin/login"))
    resp.delete_cookie(ADMIN_COOKIE)
    return resp

def get_admin_data(limit_sessions=100, limit_events=500):
    sessions, s_err = supabase_request("GET", f"cquai_sessions?select=*&order=last_seen_at.desc&limit={limit_sessions}")
    events, e_err = supabase_request("GET", f"cquai_events?select=*&order=created_at.desc&limit={limit_events}")
    return sessions or [], events or [], s_err or e_err

@app.get("/admin")
@admin_required
def admin_dashboard():
    sessions, events, err = get_admin_data(limit_sessions=200, limit_events=1200)
    if err:
        return Response(f"Errore lettura Supabase: {err}", status=500, mimetype="text/plain")

    def esc(x):
        if x is None:
            return ""
        if isinstance(x, (dict, list)):
            x = json.dumps(x, ensure_ascii=False, indent=2)
        return str(x).replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")

    total_sessions = len(sessions)
    total_events = len(events)
    message_events = [e for e in events if e.get("event_type") == "chat_message"]
    click_events = [e for e in events if e.get("event_type") == "button_click"]
    error_events = [e for e in events if "error" in (e.get("event_type") or "")]
    closed_sessions = [s for s in sessions if s.get("status") == "closed"]
    total_tokens = sum(int(s.get("total_tokens") or 0) for s in sessions)

    memory_rows = []
    for e in message_events:
        mem = e.get("memory_items")
        if isinstance(mem, list):
            for item in mem:
                if isinstance(item, dict):
                    memory_rows.append({
                        "created_at": e.get("created_at"),
                        "session_id": e.get("session_id"),
                        "text": item.get("text") or "",
                        "macro": item.get("macro") or "",
                        "type": item.get("type") or "",
                        "confidence": item.get("confidence") or "",
                        "subject": item.get("subject") or ""
                    })

    geo_counts = {}
    for s in sessions:
        key = s.get("geo_city") or s.get("geo_region") or s.get("geo_country") or "area non rilevata"
        geo_counts[key] = geo_counts.get(key, 0) + 1

    click_counts = {}
    for e in click_events:
        label = e.get("click_label") or e.get("click_target") or "click"
        label = " ".join(str(label).split())[:80]
        click_counts[label] = click_counts.get(label, 0) + 1

    sessions_by_id = {s.get("session_id"): s for s in sessions}

    def session_geo(sid):
        s = sessions_by_id.get(sid) or {}
        return s.get("geo_city") or s.get("geo_region") or s.get("geo_country") or "area non rilevata"

    def session_device(sid):
        s = sessions_by_id.get(sid) or {}
        parts = [s.get("device_type"), s.get("browser"), s.get("os")]
        return " · ".join([str(p) for p in parts if p]) or "dispositivo non rilevato"

    conversation_cards = []
    for s in sessions[:80]:
        sid = s.get("session_id")
        evs = [e for e in events if e.get("session_id") == sid]
        chats = [e for e in evs if e.get("event_type") == "chat_message"]
        last_user = chats[0].get("user_text") if chats else ""
        last_bot = chats[0].get("assistant_text") if chats else ""
        geo = s.get("geo_city") or s.get("geo_region") or s.get("geo_country") or "area non rilevata"
        status = s.get("status") or "active"
        conversation_cards.append(f"""
        <a class="conversation-card" href="/admin/session/{esc(sid)}">
          <div class="conv-head">
            <strong>{esc(geo)}</strong>
            <span>{esc(s.get('last_seen_at'))}</span>
          </div>
          <div class="conv-meta">
            {esc(s.get('device_type'))} · {esc(s.get('browser'))} · messaggi {esc(s.get('message_count'))} · click {esc(s.get('click_count'))} · <b>{esc(status)}</b>
          </div>
          {f'<p><b>Ultimo messaggio:</b> {esc(last_user[:220])}</p>' if last_user else '<p class="muted">Nessun messaggio chat in questa sessione.</p>'}
          {f'<p class="bot-preview"><b>Risposta:</b> {esc(last_bot[:220])}</p>' if last_bot else ''}
        </a>
        """)

    message_cards = []
    for e in message_events[:120]:
        sid = e.get("session_id")
        message_cards.append(f"""
        <article class="message-card">
          <div class="msg-top">
            <a href="/admin/session/{esc(sid)}">Sessione</a>
            <span>{esc(e.get('created_at'))}</span>
          </div>
          <div class="msg-meta">{esc(session_geo(sid))} · {esc(session_device(sid))}</div>
          <div class="bubble user"><b>Utente</b><p>{esc(e.get('user_text'))}</p></div>
          <div class="bubble bot"><b>cquAI</b><p>{esc(e.get('assistant_text'))}</p></div>
          {f'<div class="summary"><b>Sintesi finale</b><p>{esc(e.get("closing_summary"))}</p></div>' if e.get("closing_summary") else ''}
        </article>
        """)

    memory_cards = []
    for m in memory_rows[:160]:
        memory_cards.append(f"""
        <article class="memory-card">
          <div class="mem-head"><strong>{esc(m.get('macro')) or 'Memoria'}</strong><span>{esc(m.get('created_at'))}</span></div>
          <p>{esc(m.get('text'))}</p>
          <small>{esc(m.get('type'))} · {esc(m.get('confidence'))} · {esc(m.get('subject'))}</small>
          <div><a href="/admin/session/{esc(m.get('session_id'))}">apri conversazione</a></div>
        </article>
        """)

    click_rows = "".join(
        f"<tr><td>{esc(k)}</td><td>{v}</td></tr>"
        for k, v in sorted(click_counts.items(), key=lambda x: x[1], reverse=True)[:40]
    )
    geo_rows = "".join(
        f"<tr><td>{esc(k)}</td><td>{v}</td></tr>"
        for k, v in sorted(geo_counts.items(), key=lambda x: x[1], reverse=True)[:40]
    )

    error_cards = []
    for e in error_events[:80]:
        sid = e.get("session_id")
        error_cards.append(f"""
        <article class="error-card">
          <div class="msg-top"><a href="/admin/session/{esc(sid)}">{esc(e.get('event_type'))}</a><span>{esc(e.get('created_at'))}</span></div>
          <pre>{esc(e.get('error_detail'))}</pre>
        </article>
        """)

    html = f"""<!doctype html>
<html lang="it">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>cquAI admin — Dashboard v2</title>
<style>
:root {{
  --bg:#f4efe7;
  --ink:#17130f;
  --card:#fffaf2;
  --line:#dccfbd;
  --soft:#fffdf8;
  --coral:#f28b78;
  --green:#dfeee6;
  --blue:#e6f3f9;
  --violet:#eee5f7;
  --yellow:#fff2c8;
  --muted:#70665b;
  --danger:#9b1c1c;
}}
*{{box-sizing:border-box}}
body {{
  margin:0;
  background:var(--bg);
  color:var(--ink);
  font-family:system-ui,-apple-system,Segoe UI,Arial,sans-serif;
}}
header {{
  position:sticky;
  top:0;
  z-index:10;
  background:rgba(244,239,231,.94);
  backdrop-filter:blur(14px);
  border-bottom:1px solid var(--line);
  padding:18px 22px;
}}
.header-inner {{
  max-width:1280px;
  margin:0 auto;
  display:flex;
  align-items:flex-start;
  justify-content:space-between;
  gap:20px;
}}
h1 {{
  margin:0;
  font-size:34px;
  letter-spacing:-.065em;
}}
h1 span {{color:var(--coral)}}
.subtitle {{
  margin:4px 0 0;
  color:var(--muted);
  font-size:14px;
}}
.actions {{
  display:flex;
  gap:8px;
  flex-wrap:wrap;
  justify-content:flex-end;
}}
.btn {{
  display:inline-block;
  padding:9px 13px;
  border-radius:999px;
  background:var(--ink);
  color:white;
  text-decoration:none;
  font-weight:750;
  font-size:14px;
}}
.btn.light {{
  background:var(--card);
  color:var(--ink);
  border:1px solid var(--line);
}}
nav {{
  max-width:1280px;
  margin:14px auto 0;
  display:flex;
  gap:8px;
  overflow-x:auto;
  padding-bottom:2px;
}}
nav a {{
  white-space:nowrap;
  text-decoration:none;
  color:var(--ink);
  background:var(--card);
  border:1px solid var(--line);
  border-radius:999px;
  padding:9px 13px;
  font-weight:750;
  font-size:14px;
}}
.wrap {{
  max-width:1280px;
  margin:0 auto;
  padding:22px;
}}
.stats {{
  display:grid;
  grid-template-columns:repeat(7,minmax(0,1fr));
  gap:12px;
  margin-bottom:18px;
}}
.stat {{
  background:var(--card);
  border:1px solid var(--line);
  border-radius:24px;
  padding:17px;
  box-shadow:0 8px 20px rgba(0,0,0,.035);
}}
.stat b {{
  display:block;
  font-size:30px;
  line-height:1.1;
}}
.stat span {{
  color:var(--muted);
  font-size:13px;
}}
.section {{
  background:var(--card);
  border:1px solid var(--line);
  border-radius:28px;
  padding:18px;
  margin:18px 0;
  box-shadow:0 10px 24px rgba(0,0,0,.035);
}}
.section h2 {{
  margin:0 0 4px;
  font-size:24px;
}}
.section .hint {{
  margin:0 0 14px;
  color:var(--muted);
  font-size:14px;
}}
.two-cols {{
  display:grid;
  grid-template-columns:1fr 1fr;
  gap:16px;
}}
.conversation-card,.message-card,.memory-card,.error-card {{
  display:block;
  color:inherit;
  text-decoration:none;
  border:1px solid #eadfce;
  background:var(--soft);
  border-radius:22px;
  padding:15px;
  margin:11px 0;
}}
.conv-head,.msg-top,.mem-head {{
  display:flex;
  justify-content:space-between;
  gap:12px;
  align-items:flex-start;
}}
.conv-head span,.msg-top span,.mem-head span,.conv-meta,.msg-meta,.muted,small {{
  color:var(--muted);
  font-size:13px;
}}
.conversation-card p {{
  margin:9px 0 0;
  white-space:pre-wrap;
}}
.bot-preview {{
  color:#34513f;
}}
.bubble {{
  border-radius:18px;
  padding:12px;
  margin:10px 0;
}}
.bubble p,.summary p,.memory-card p {{
  margin:.35em 0 0;
  white-space:pre-wrap;
}}
.user {{
  background:#f1e6d9;
}}
.bot {{
  background:#e4efe8;
}}
.summary {{
  background:var(--blue);
  border-radius:18px;
  padding:12px;
  margin-top:10px;
}}
.memory-card {{
  background:#fffdf8;
}}
.mem-head strong {{
  color:#3d624d;
}}
table {{
  width:100%;
  border-collapse:collapse;
}}
td {{
  border-bottom:1px solid #eadfce;
  padding:10px 4px;
  vertical-align:top;
}}
td:last-child {{
  text-align:right;
  font-weight:850;
}}
pre {{
  white-space:pre-wrap;
  overflow:auto;
  background:#201b16;
  color:white;
  border-radius:16px;
  padding:12px;
  margin:10px 0 0;
}}
.empty {{
  color:var(--muted);
  background:#fffdf8;
  border:1px dashed var(--line);
  border-radius:20px;
  padding:20px;
}}
@media(max-width:1000px) {{
  .stats {{grid-template-columns:1fr 1fr}}
  .two-cols {{grid-template-columns:1fr}}
  .header-inner {{display:block}}
  .actions {{justify-content:flex-start;margin-top:10px}}
}}
</style>
</head>
<body>
<header>
  <div class="header-inner">
    <div>
      <h1>cqu<span>AI</span> admin</h1>
      <p class="subtitle">Dashboard v2 · lettura pulita di conversazioni, messaggi, memorie e comportamento</p>
    </div>
    <div class="actions">
      <a class="btn" href="/admin">Aggiorna</a>
      <a class="btn light" href="/admin/export.json">Esporta JSON</a>
      <a class="btn light" href="/admin/logout">Esci</a>
    </div>
  </div>
  <nav>
    <a href="#panoramica">Panoramica</a>
    <a href="#conversazioni">Conversazioni</a>
    <a href="#messaggi">Solo messaggi</a>
    <a href="#memorie">Memorie</a>
    <a href="#click">Click</a>
    <a href="#errori">Errori</a>
  </nav>
</header>

<main class="wrap">
  <section id="panoramica" class="stats">
    <div class="stat"><b>{total_sessions}</b><span>sessioni</span></div>
    <div class="stat"><b>{len(closed_sessions)}</b><span>chiuse</span></div>
    <div class="stat"><b>{len(message_events)}</b><span>messaggi</span></div>
    <div class="stat"><b>{len(memory_rows)}</b><span>memorie</span></div>
    <div class="stat"><b>{len(click_events)}</b><span>click</span></div>
    <div class="stat"><b>{len(error_events)}</b><span>errori</span></div>
    <div class="stat"><b>{total_tokens}</b><span>token</span></div>
  </section>

  <section id="conversazioni" class="section">
    <h2>Conversazioni</h2>
    <p class="hint">Una scheda per ogni sessione. Aprila per leggere la chat completa in ordine.</p>
    {"".join(conversation_cards) if conversation_cards else '<div class="empty">Nessuna conversazione ancora.</div>'}
  </section>

  <section id="messaggi" class="section">
    <h2>Solo messaggi</h2>
    <p class="hint">Qui vedi solo messaggi utente, risposte di cquAI e sintesi finale. Niente login, consenso o click.</p>
    {"".join(message_cards) if message_cards else '<div class="empty">Nessun messaggio chat ancora.</div>'}
  </section>

  <section id="memorie" class="section">
    <h2>Memorie estratte</h2>
    <p class="hint">Solo le informazioni che cquAI ha estratto come memoria.</p>
    {"".join(memory_cards) if memory_cards else '<div class="empty">Nessuna memoria estratta ancora.</div>'}
  </section>

  <section class="two-cols">
    <div id="click" class="section">
      <h2>Click principali</h2>
      <p class="hint">Quali pulsanti o elementi vengono usati di più.</p>
      <table>{click_rows if click_rows else '<tr><td>Nessun click</td><td>0</td></tr>'}</table>
    </div>
    <div class="section">
      <h2>Aree geografiche</h2>
      <p class="hint">Area approssimativa se disponibile dagli header del servizio.</p>
      <table>{geo_rows if geo_rows else '<tr><td>area non rilevata</td><td>0</td></tr>'}</table>
    </div>
  </section>

  <section id="errori" class="section">
    <h2>Errori</h2>
    <p class="hint">Errori Groq o server, separati dai messaggi normali.</p>
    {"".join(error_cards) if error_cards else '<div class="empty">Nessun errore registrato.</div>'}
  </section>
</main>
</body>
</html>"""
    return Response(html, mimetype="text/html")


@app.get("/admin/session/<session_id>")
@admin_required
def admin_session(session_id):
    sid_q = urllib.parse.quote(session_id)
    sessions, s_err = supabase_request("GET", f"cquai_sessions?select=*&session_id=eq.{sid_q}&limit=1")
    events, e_err = supabase_request("GET", f"cquai_events?select=*&session_id=eq.{sid_q}&order=created_at.asc&limit=1000")
    if s_err or e_err:
        return Response(f"Errore Supabase: {s_err or e_err}", status=500)
    session = sessions[0] if sessions else {}

    def esc(x):
        if x is None:
            return ""
        if isinstance(x, (dict, list)):
            x = json.dumps(x, ensure_ascii=False, indent=2)
        return str(x).replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")

    chat_events = [e for e in (events or []) if e.get("event_type") == "chat_message"]
    click_events = [e for e in (events or []) if e.get("event_type") == "button_click"]
    technical_events = [e for e in (events or []) if e.get("event_type") not in ("chat_message","button_click")]

    chat_blocks = []
    for e in chat_events:
        mem = e.get("memory_items")
        mem_html = ""
        if mem:
            mem_html = f'<pre class="memory">{esc(mem)}</pre>'
        summary_html = ""
        if e.get("closing_summary"):
            summary_html = f'<div class="summary"><b>Sintesi finale</b><p>{esc(e.get("closing_summary"))}</p></div>'
        chat_blocks.append(f"""
        <article class="turn">
          <div class="turn-time">{esc(e.get('created_at'))} · token {esc(e.get('total_tokens'))}</div>
          <div class="bubble user"><b>Utente</b><p>{esc(e.get('user_text'))}</p></div>
          <div class="bubble bot"><b>cquAI</b><p>{esc(e.get('assistant_text'))}</p></div>
          {summary_html}
          {mem_html}
        </article>
        """)

    click_rows = "".join(
        f"<tr><td>{esc(e.get('created_at'))}</td><td>{esc(e.get('click_label') or e.get('click_target'))}</td></tr>"
        for e in click_events
    )

    technical_rows = "".join(
        f"<tr><td>{esc(e.get('created_at'))}</td><td>{esc(e.get('event_type'))}</td><td>{esc(e.get('page'))}</td></tr>"
        for e in technical_events
    )

    geo = session.get("geo_city") or session.get("geo_region") or session.get("geo_country") or "area non rilevata"
    html = f"""<!doctype html>
<html lang="it">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>cquAI admin — conversazione</title>
<style>
:root{{--bg:#f4efe7;--ink:#17130f;--card:#fffaf2;--line:#dccfbd;--coral:#f28b78;--muted:#70665b;--soft:#fffdf8}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--ink);font-family:system-ui,-apple-system,Segoe UI,Arial,sans-serif}}
header{{position:sticky;top:0;background:rgba(244,239,231,.94);backdrop-filter:blur(12px);border-bottom:1px solid var(--line);padding:18px 22px;z-index:5}}
.wrap{{max-width:980px;margin:0 auto;padding:22px}}
a{{color:var(--ink);font-weight:850;text-decoration:none}}
h1{{font-size:34px;letter-spacing:-.04em;margin:8px 0 8px}}
.meta{{background:var(--card);border:1px solid var(--line);border-radius:24px;padding:16px;margin-bottom:18px;color:var(--muted)}}
.turn{{background:var(--card);border:1px solid var(--line);border-radius:26px;padding:16px;margin:16px 0;box-shadow:0 8px 20px rgba(0,0,0,.035)}}
.turn-time{{color:var(--muted);font-size:13px;margin-bottom:10px}}
.bubble{{border-radius:20px;padding:13px;margin:10px 0}}
.bubble p,.summary p{{white-space:pre-wrap;margin:.4em 0 0}}
.user{{background:#f1e6d9}}
.bot{{background:#e4efe8}}
.summary{{background:#e6f3f9;border-radius:20px;padding:13px;margin:10px 0}}
.memory{{white-space:pre-wrap;background:#17130f;color:white;border-radius:18px;padding:12px;overflow:auto}}
.panel{{background:var(--card);border:1px solid var(--line);border-radius:24px;padding:16px;margin:18px 0}}
table{{width:100%;border-collapse:collapse}}
td{{border-bottom:1px solid #eadfce;padding:9px 4px;vertical-align:top}}
.empty{{color:var(--muted);background:var(--soft);border:1px dashed var(--line);border-radius:18px;padding:16px}}
</style>
</head>
<body>
<header><a href="/admin">← Torna alla dashboard</a></header>
<main class="wrap">
  <h1>Conversazione</h1>
  <section class="meta">
    <b>ID sessione:</b> {esc(session_id)}<br>
    <b>Area:</b> {esc(geo)}<br>
    <b>Dispositivo:</b> {esc(session.get('device_type'))} · {esc(session.get('browser'))} · {esc(session.get('os'))}<br>
    <b>Stato:</b> {esc(session.get('status'))}<br>
    <b>Messaggi:</b> {esc(session.get('message_count'))} · <b>Click:</b> {esc(session.get('click_count'))} · <b>Token:</b> {esc(session.get('total_tokens'))}
  </section>

  <section class="panel">
    <h2>Chat pulita</h2>
    {"".join(chat_blocks) if chat_blocks else '<div class="empty">Nessun messaggio chat in questa sessione.</div>'}
  </section>

  <section class="panel">
    <h2>Click della sessione</h2>
    <table>{click_rows if click_rows else '<tr><td class="empty">Nessun click registrato.</td></tr>'}</table>
  </section>

  <section class="panel">
    <h2>Eventi tecnici</h2>
    <table>{technical_rows if technical_rows else '<tr><td class="empty">Nessun evento tecnico.</td></tr>'}</table>
  </section>
</main>
</body>
</html>"""
    return Response(html, mimetype="text/html")


@app.get("/admin/messages.json")
@admin_required
def admin_messages_export():
    events, err = supabase_request("GET", "cquai_events?select=*&event_type=eq.chat_message&order=created_at.desc&limit=10000")
    if err:
        return jsonify({"error": err}), 500
    return jsonify(events or [])

@app.get("/admin/export.json")
@admin_required
def admin_export():
    sessions, _ = supabase_request("GET", "cquai_sessions?select=*&order=created_at.desc&limit=5000")
    events, _ = supabase_request("GET", "cquai_events?select=*&order=created_at.desc&limit=10000")
    return jsonify({"sessions": sessions or [], "events": events or []})

@app.post("/api/event")
@beta_password_required
@consent_required
def api_event():
    data = request.get_json(force=True, silent=True) or {}
    event_type = str(data.get("event_type") or "event")[:80]
    log_event(
        event_type,
        click_target=data.get("click_target"),
        click_label=data.get("click_label"),
        page=data.get("page") or request.path,
        meta={k:v for k,v in data.items() if k not in ("event_type","click_target","click_label","page")}
    )
    return jsonify({"ok": True})

@app.get("/api/status")
def status():
    return jsonify({
        "ok": True,
        "provider": "Groq",
        "version": "render-dashboard-v2",
        "model": env("GROQ_MODEL", DEFAULT_MODEL),
        "has_groq_key": bool(env("GROQ_API_KEY")),
        "has_beta_password": bool(env("BETA_PASSWORD")),
        "has_admin_password": bool(env("ADMIN_PASSWORD")),
        "has_supabase": bool(env("SUPABASE_URL") and env("SUPABASE_SERVICE_ROLE_KEY")),
    })

@app.get("/api/models")
@beta_password_required
@consent_required
def models():
    api_key = env("GROQ_API_KEY")
    if not api_key:
        return jsonify({"error": "GROQ_API_KEY mancante nelle variabili ambiente Render."}), 401

    try:
        status_code, content_type, content = groq_request(GROQ_MODELS_URL, api_key)
        return Response(content, status=status_code, content_type=content_type)
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        log_event("groq_error", error_detail=detail)
        return jsonify({"error": "Groq models error", "detail": detail}), e.code
    except Exception as e:
        detail = str(e)
        log_event("server_error", error_detail=detail)
        return jsonify({"error": "Non riesco a raggiungere Groq models", "detail": detail}), 502

@app.post("/api/chat")
@beta_password_required
@consent_required
def chat():
    api_key = env("GROQ_API_KEY")
    model = env("GROQ_MODEL", DEFAULT_MODEL) or DEFAULT_MODEL
    sid = ensure_session()

    if not api_key:
        log_event("server_error", error_detail="GROQ_API_KEY mancante", model=model)
        return jsonify({
            "error": "Chiave Groq mancante.",
            "detail": "Imposta GROQ_API_KEY nelle Environment Variables di Render."
        }), 401

    user_text = ""
    assistant_reply = ""
    memory_items = None
    closing_summary = ""
    usage = None
    prompt_tokens = completion_tokens = total_tokens = None

    try:
        payload = request.get_json(force=True, silent=False)
        messages = payload.get("messages", [])
        for m in reversed(messages):
            if m.get("role") == "user":
                user_text = str(m.get("content", ""))
                break

        payload["model"] = model
        payload["stream"] = False
        payload["temperature"] = float(payload.get("temperature", 0.25))
        payload["max_tokens"] = int(payload.get("max_tokens", 180))

        status_code, content_type, content = groq_request(GROQ_URL, api_key, payload)

        try:
            parsed = json.loads(content.decode("utf-8", errors="replace"))
            usage = parsed.get("usage") or {}
            prompt_tokens = usage.get("prompt_tokens")
            completion_tokens = usage.get("completion_tokens")
            total_tokens = usage.get("total_tokens")
            assistant_content = parsed.get("choices", [{}])[0].get("message", {}).get("content", "")
            try:
                inner = json.loads(assistant_content)
                assistant_reply = inner.get("reply", assistant_content)
                memory_items = inner.get("memoryItems")
                closing_summary = inner.get("closingSummary") or ""
                if inner.get("shouldClose"):
                    supabase_request("PATCH", "cquai_sessions?session_id=eq." + urllib.parse.quote(sid), {
                        "status": "closed",
                        "closed_at": now_iso()
                    })
            except Exception:
                assistant_reply = assistant_content
        except Exception:
            pass

        log_event(
            "chat_message",
            user_text=user_text,
            assistant_text=assistant_reply,
            closing_summary=closing_summary,
            memory_items=memory_items,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            usage=usage,
            meta={"status_code": status_code}
        )

        resp = Response(content, status=status_code, content_type=content_type)
        return set_session_cookie(resp, sid)

    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        log_event("groq_error", user_text=user_text, error_detail=detail, model=model)
        return jsonify({"error": "Groq ha risposto con errore.", "detail": detail}), e.code
    except Exception as e:
        detail = str(e)
        log_event("server_error", user_text=user_text, error_detail=detail, model=model)
        return jsonify({"error": "Non riesco a raggiungere Groq.", "detail": detail}), 502

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
