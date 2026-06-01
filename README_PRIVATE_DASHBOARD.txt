# cquAI Render Private Dashboard

Versione privata con:
- password beta
- schermata accettazione dati
- app cquAI Micro invariata nella grafica e nella logica principale
- tracciamento click
- salvataggio messaggi/risposte/memoryItems/errori/token su Supabase
- dashboard privata /admin
- export JSON /admin/export.json

## Variabili Render richieste

GROQ_API_KEY
GROQ_MODEL
BETA_PASSWORD
ADMIN_PASSWORD
SUPABASE_URL
SUPABASE_SERVICE_ROLE_KEY

## Test dopo deploy

/api/status

Deve mostrare:
version = render-private-dashboard
has_groq_key = true
has_beta_password = true
has_admin_password = true
has_supabase = true

App:
/login → password
/consent → accettazione dati
/ → cquAI

Admin:
/admin
/admin/export.json

## Note privacy

Questa versione salva messaggi, risposte, click principali, dati tecnici minimi e posizione approssimativa se disponibile dagli header.
Non salva GPS preciso.
