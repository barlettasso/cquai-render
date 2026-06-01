import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from flask import Flask, Response, jsonify, request, send_from_directory

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODELS_URL = "https://api.groq.com/openai/v1/models"
DEFAULT_MODEL = "llama-3.1-8b-instant"

app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path="")

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

@app.get("/")
def index():
    return send_from_directory(STATIC_DIR, "index.html")

@app.get("/api/status")
def status():
    return jsonify({
        "ok": True,
        "provider": "Groq",
        "version": "render-micro",
        "model": os.environ.get("GROQ_MODEL", DEFAULT_MODEL),
        "has_key": bool(os.environ.get("GROQ_API_KEY")),
    })

@app.get("/api/models")
def models():
    api_key = os.environ.get("GROQ_API_KEY", "").strip()
    if not api_key:
        return jsonify({"error": "GROQ_API_KEY mancante nelle variabili ambiente Render."}), 401

    try:
        status_code, content_type, content = groq_request(GROQ_MODELS_URL, api_key)
        return Response(content, status=status_code, content_type=content_type)
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        return jsonify({"error": "Groq models error", "detail": detail}), e.code
    except Exception as e:
        return jsonify({"error": "Non riesco a raggiungere Groq models", "detail": str(e)}), 502

@app.post("/api/chat")
def chat():
    api_key = os.environ.get("GROQ_API_KEY", "").strip()
    model = os.environ.get("GROQ_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL

    if not api_key:
        return jsonify({
            "error": "Chiave Groq mancante.",
            "detail": "Imposta GROQ_API_KEY nelle Environment Variables di Render."
        }), 401

    try:
        payload = request.get_json(force=True, silent=False)
        payload["model"] = model
        payload["stream"] = False
        payload["temperature"] = float(payload.get("temperature", 0.25))
        payload["max_tokens"] = int(payload.get("max_tokens", 180))

        status_code, content_type, content = groq_request(GROQ_URL, api_key, payload)
        return Response(content, status=status_code, content_type=content_type)

    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        return jsonify({"error": "Groq ha risposto con errore.", "detail": detail}), e.code
    except Exception as e:
        return jsonify({"error": "Non riesco a raggiungere Groq.", "detail": str(e)}), 502

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
