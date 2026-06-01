# cquAI Beta Groq Micro — Render Ready

Questa versione è pronta per essere pubblicata su Render senza dominio.

## Cosa contiene

- `app.py`: piccolo backend Flask che protegge la chiave Groq.
- `static/index.html`: interfaccia cquAI Micro.
- `requirements.txt`: dipendenze Python.
- `render.yaml`: configurazione Render Blueprint.
- `.gitignore`.

## Cosa fa

Telefono / PC / soci
→ link Render
→ backend cquAI
→ Groq
→ risposta dentro cquAI

La chiave Groq non è nell'HTML. Va inserita nelle variabili ambiente di Render.

## Variabili ambiente Render

Imposta:

GROQ_API_KEY = la tua nuova chiave Groq
GROQ_MODEL = llama-3.1-8b-instant

## Deploy consigliato

Metodo semplice:

1. Crea un repository GitHub.
2. Carica tutti i file di questa cartella nel repository.
3. Vai su Render.
4. New → Web Service.
5. Collega il repository.
6. Render dovrebbe leggere Python e i comandi.
7. Build Command:
   pip install -r requirements.txt
8. Start Command:
   gunicorn app:app
9. Aggiungi Environment Variable:
   GROQ_API_KEY = ...
   GROQ_MODEL = llama-3.1-8b-instant
10. Deploy.

Render ti darà un link tipo:

https://cquai-beta-groq-micro.onrender.com

## Test

Dopo il deploy apri:

/api/status

Deve mostrare:

"version": "render-micro"
"has_key": true

Poi apri la home e prova la chat.

## Nota

Sui piani gratuiti Render può andare in sleep dopo inattività.
Il primo avvio può essere più lento; poi la chat usa Groq ed è veloce.
