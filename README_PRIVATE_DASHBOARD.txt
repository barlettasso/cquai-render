# cquAI Render Private Dashboard

Versione privata FIX con:
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
version = render-private-dashboard-fix
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


## FIX incluso

Questa versione corregge il salvataggio su Supabase.

Problema corretto:
- la dashboard si apriva;
- però restava a zero;
- perché la sessione non veniva creata quando era nuova.

Ora il backend usa UPSERT su `session_id`: se la sessione non esiste la crea, se esiste la aggiorna.


## Dashboard v2

Miglioramenti:
- Panoramica più leggibile.
- Sezione conversazioni.
- Sezione "Solo messaggi" separata da login, consenso e click.
- Sezione "Memorie estratte".
- Sezione click principali.
- Sezione errori.
- Dettaglio conversazione in stile chat.
- Export solo messaggi: /admin/messages.json

L'app principale cquAI non viene modificata.
