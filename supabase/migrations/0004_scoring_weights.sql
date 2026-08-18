alter table public.brain_decisions
  add column if not exists scoring jsonb not null default '{}'::jsonb;
