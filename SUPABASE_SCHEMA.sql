-- cquAI Beta Privata — Schema Supabase
-- Fase 1: crea le tabelle per sessioni, eventi, messaggi, click e dashboard.
-- Da eseguire in Supabase → SQL Editor → New query → Run.

create extension if not exists pgcrypto;

-- Una riga per ogni sessione/conversazione anonima.
create table if not exists public.cquai_sessions (
  id uuid primary key default gen_random_uuid(),

  session_id text not null unique,

  created_at timestamptz not null default now(),
  first_seen_at timestamptz not null default now(),
  last_seen_at timestamptz not null default now(),
  closed_at timestamptz,

  status text not null default 'active',
  -- valori previsti: active, closed, error, rejected_consent

  consent_accepted boolean not null default false,
  consent_accepted_at timestamptz,

  beta_logged_in boolean not null default false,
  beta_logged_in_at timestamptz,

  -- Posizione approssimativa, non GPS preciso.
  geo_country text,
  geo_region text,
  geo_city text,
  geo_timezone text,
  geo_source text,

  -- Dati tecnici minimi.
  user_agent text,
  device_type text,
  browser text,
  os text,
  language text,

  message_count integer not null default 0,
  click_count integer not null default 0,
  error_count integer not null default 0,

  prompt_tokens integer not null default 0,
  completion_tokens integer not null default 0,
  total_tokens integer not null default 0,

  meta jsonb not null default '{}'::jsonb
);

-- Una riga per ogni evento:
-- accesso, login, consenso, click, messaggio, risposta, errore, chiusura.
create table if not exists public.cquai_events (
  id uuid primary key default gen_random_uuid(),

  session_id text not null references public.cquai_sessions(session_id) on delete cascade,

  created_at timestamptz not null default now(),

  event_type text not null,
  -- esempi:
  -- access
  -- beta_login_ok
  -- beta_login_failed
  -- consent_accepted
  -- consent_rejected
  -- home_view
  -- button_click
  -- chat_message
  -- conversation_closed
  -- memory_reset
  -- groq_error
  -- server_error

  page text,

  -- Click e comportamento.
  click_target text,
  click_label text,

  -- Chat.
  user_text text,
  assistant_text text,
  closing_summary text,
  memory_items jsonb,

  -- AI / Groq.
  model text,
  prompt_tokens integer,
  completion_tokens integer,
  total_tokens integer,
  usage jsonb,

  -- Errori.
  error_code text,
  error_detail text,

  -- Posizione approssimativa.
  geo_country text,
  geo_region text,
  geo_city text,
  geo_timezone text,
  geo_source text,

  -- Dati tecnici minimi.
  user_agent text,
  device_type text,
  browser text,
  os text,
  language text,

  meta jsonb not null default '{}'::jsonb
);

create index if not exists cquai_sessions_created_at_idx
on public.cquai_sessions (created_at desc);

create index if not exists cquai_sessions_session_id_idx
on public.cquai_sessions (session_id);

create index if not exists cquai_sessions_status_idx
on public.cquai_sessions (status);

create index if not exists cquai_sessions_geo_city_idx
on public.cquai_sessions (geo_city);

create index if not exists cquai_events_created_at_idx
on public.cquai_events (created_at desc);

create index if not exists cquai_events_session_id_idx
on public.cquai_events (session_id);

create index if not exists cquai_events_event_type_idx
on public.cquai_events (event_type);

create index if not exists cquai_events_click_target_idx
on public.cquai_events (click_target);

-- Sicurezza:
-- Non abilitiamo accessi pubblici dal browser.
-- L'app leggerà e scriverà da Render usando la SERVICE_ROLE_KEY custodita nelle Environment Variables.
-- Non inserire mai la SERVICE_ROLE_KEY in GitHub, nell'HTML o in chat.

alter table public.cquai_sessions enable row level security;
alter table public.cquai_events enable row level security;

-- Nessuna policy pubblica: gli utenti anonimi non possono leggere o scrivere direttamente.
-- La service_role key del backend Render bypassa RLS.
