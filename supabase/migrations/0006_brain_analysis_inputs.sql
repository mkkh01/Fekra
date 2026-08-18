alter table public.brain_cycles
  add column if not exists analysis_inputs jsonb not null default '{}'::jsonb;
