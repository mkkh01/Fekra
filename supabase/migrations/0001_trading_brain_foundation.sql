create extension if not exists pgcrypto with schema extensions;

create table if not exists public.assets (
  symbol text primary key,
  base_asset text not null,
  quote_asset text not null default 'USDT',
  market_type text not null default 'spot',
  is_active boolean not null default true,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.market_snapshots (
  id uuid primary key default gen_random_uuid(),
  symbol text not null references public.assets(symbol),
  price numeric not null,
  change_24h_pct numeric,
  volume_24h numeric,
  high_24h numeric,
  low_24h numeric,
  source text not null default 'binance',
  observed_at timestamptz not null,
  created_at timestamptz not null default now()
);

create table if not exists public.candles_metadata (
  id uuid primary key default gen_random_uuid(),
  symbol text not null references public.assets(symbol),
  timeframe text not null,
  first_open_time timestamptz,
  last_close_time timestamptz,
  candle_count integer not null default 0,
  source text not null default 'binance',
  updated_at timestamptz not null default now(),
  unique(symbol, timeframe)
);

create table if not exists public.news (
  id uuid primary key default gen_random_uuid(),
  fingerprint text not null unique,
  title text not null,
  url text not null,
  summary text,
  source text not null,
  published_at timestamptz,
  retrieved_at timestamptz not null default now(),
  evidence_status text not null default 'sourced',
  metadata jsonb not null default '{}'::jsonb
);

create table if not exists public.news_entities (
  id uuid primary key default gen_random_uuid(),
  news_id uuid not null references public.news(id) on delete cascade,
  symbol text references public.assets(symbol),
  relevance text,
  created_at timestamptz not null default now(),
  unique(news_id, symbol)
);

create table if not exists public.brain_cycles (
  id uuid primary key default gen_random_uuid(),
  started_at timestamptz not null default now(),
  finished_at timestamptz,
  trigger_type text not null,
  trigger_symbol text references public.assets(symbol),
  status text not null default 'STARTED',
  objective text,
  summary text,
  final_decision text,
  model text,
  model_version text,
  error text,
  created_at timestamptz not null default now()
);

create table if not exists public.brain_tool_calls (
  id uuid primary key default gen_random_uuid(),
  cycle_id uuid not null references public.brain_cycles(id) on delete cascade,
  tool_name text not null,
  arguments jsonb not null default '{}'::jsonb,
  result_summary text,
  started_at timestamptz,
  completed_at timestamptz,
  status text not null default 'STARTED',
  latency_ms integer,
  created_at timestamptz not null default now()
);

create table if not exists public.brain_hypotheses (
  id uuid primary key default gen_random_uuid(),
  cycle_id uuid not null references public.brain_cycles(id) on delete cascade,
  label text not null,
  statement text not null,
  status text,
  evidence_strength text,
  created_at timestamptz not null default now()
);

create table if not exists public.brain_decisions (
  id uuid primary key default gen_random_uuid(),
  cycle_id uuid not null references public.brain_cycles(id) on delete cascade,
  symbol text not null references public.assets(symbol),
  action text not null check (action in ('BUY', 'SELL_REDUCE', 'WAIT', 'NO_TRADE', 'MONITOR', 'CLOSE')),
  thesis text,
  summary text,
  evidence jsonb not null default '[]'::jsonb,
  counter_evidence jsonb not null default '[]'::jsonb,
  alternative_hypotheses jsonb not null default '[]'::jsonb,
  audience_facing_reasoning text,
  uncertainty text,
  invalidation_context jsonb not null default '[]'::jsonb,
  execution_status text not null default 'NOT_REQUESTED',
  created_at timestamptz not null default now()
);

create table if not exists public.brain_evidence (
  id uuid primary key default gen_random_uuid(),
  cycle_id uuid not null references public.brain_cycles(id) on delete cascade,
  evidence_type text not null,
  source text,
  retrieved_at timestamptz,
  raw_reference text,
  normalized_summary text,
  evidence_status text not null default 'verified',
  created_at timestamptz not null default now()
);

create table if not exists public.brain_counter_cases (
  id uuid primary key default gen_random_uuid(),
  cycle_id uuid not null references public.brain_cycles(id) on delete cascade,
  statement text not null,
  supporting_evidence jsonb not null default '[]'::jsonb,
  created_at timestamptz not null default now()
);

create table if not exists public.brain_memory (
  id uuid primary key default gen_random_uuid(),
  memory_type text not null check (memory_type in ('EPISODIC', 'SEMANTIC', 'FAILURE')),
  symbol text references public.assets(symbol),
  content text not null,
  metadata jsonb not null default '{}'::jsonb,
  validated boolean not null default false,
  created_at timestamptz not null default now()
);

create table if not exists public.brain_lessons (
  id uuid primary key default gen_random_uuid(),
  cycle_id uuid references public.brain_cycles(id) on delete set null,
  symbol text references public.assets(symbol),
  lesson text not null,
  category text not null,
  confidence text,
  promoted_to_memory boolean not null default false,
  created_at timestamptz not null default now()
);

create table if not exists public.paper_positions (
  id uuid primary key default gen_random_uuid(),
  symbol text not null references public.assets(symbol),
  side text not null check (side in ('LONG', 'SPOT_HOLDING')),
  quantity numeric not null,
  entry_price numeric not null,
  current_price numeric,
  unrealized_pnl numeric,
  fees numeric not null default 0,
  original_cycle_id uuid references public.brain_cycles(id),
  thesis text,
  opened_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  status text not null default 'OPEN'
);

create table if not exists public.closed_trades (
  id uuid primary key default gen_random_uuid(),
  symbol text not null references public.assets(symbol),
  side text not null,
  mode text not null default 'PAPER',
  entry_price numeric not null,
  exit_price numeric not null,
  quantity numeric not null,
  fees numeric not null default 0,
  slippage numeric not null default 0,
  pnl numeric,
  pnl_pct numeric,
  original_cycle_id uuid references public.brain_cycles(id),
  exit_cycle_id uuid references public.brain_cycles(id),
  opened_at timestamptz,
  closed_at timestamptz,
  outcome text,
  lesson_id uuid references public.brain_lessons(id),
  created_at timestamptz not null default now()
);

create table if not exists public.execution_events (
  id uuid primary key default gen_random_uuid(),
  mode text not null default 'PAPER',
  symbol text,
  event_type text not null,
  request jsonb not null default '{}'::jsonb,
  response jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create table if not exists public.system_events (
  id uuid primary key default gen_random_uuid(),
  event_type text not null,
  message text not null,
  data jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create table if not exists public.system_config (
  key text primary key,
  value jsonb not null default '{}'::jsonb,
  is_sensitive boolean not null default false,
  updated_at timestamptz not null default now()
);

create table if not exists public.alerts (
  id uuid primary key default gen_random_uuid(),
  severity text not null default 'INFO',
  title text not null,
  message text not null,
  acknowledged boolean not null default false,
  created_at timestamptz not null default now()
);

create index if not exists market_snapshots_symbol_observed_idx on public.market_snapshots(symbol, observed_at desc);
create index if not exists news_published_idx on public.news(published_at desc);
create index if not exists brain_cycles_symbol_created_idx on public.brain_cycles(trigger_symbol, created_at desc);
create index if not exists brain_decisions_symbol_created_idx on public.brain_decisions(symbol, created_at desc);
create index if not exists system_events_created_idx on public.system_events(created_at desc);

alter table public.assets enable row level security;
alter table public.market_snapshots enable row level security;
alter table public.candles_metadata enable row level security;
alter table public.news enable row level security;
alter table public.news_entities enable row level security;
alter table public.brain_cycles enable row level security;
alter table public.brain_tool_calls enable row level security;
alter table public.brain_hypotheses enable row level security;
alter table public.brain_decisions enable row level security;
alter table public.brain_evidence enable row level security;
alter table public.brain_counter_cases enable row level security;
alter table public.brain_memory enable row level security;
alter table public.brain_lessons enable row level security;
alter table public.paper_positions enable row level security;
alter table public.closed_trades enable row level security;
alter table public.execution_events enable row level security;
alter table public.system_events enable row level security;
alter table public.system_config enable row level security;
alter table public.alerts enable row level security;

insert into public.assets(symbol, base_asset) values
  ('BTCUSDT','BTC'), ('ETHUSDT','ETH'), ('BNBUSDT','BNB'), ('SOLUSDT','SOL'), ('XRPUSDT','XRP'),
  ('DOGEUSDT','DOGE'), ('ADAUSDT','ADA'), ('AVAXUSDT','AVAX'), ('LINKUSDT','LINK'), ('DOTUSDT','DOT'),
  ('TRXUSDT','TRX'), ('LTCUSDT','LTC'), ('BCHUSDT','BCH'), ('SUIUSDT','SUI'), ('NEARUSDT','NEAR'),
  ('APTUSDT','APT'), ('TONUSDT','TON'), ('XLMUSDT','XLM'), ('HBARUSDT','HBAR'), ('ICPUSDT','ICP')
on conflict (symbol) do nothing;

insert into public.system_config(key, value, is_sensitive) values
  ('trading_mode', '"PAPER"'::jsonb, false),
  ('live_execution_enabled', 'false'::jsonb, false),
  ('kill_switch', 'false'::jsonb, false),
  ('brain_policy', '{"default_decision":"WAIT","private_chain_of_thought_storage":false}'::jsonb, false)
on conflict (key) do nothing;
