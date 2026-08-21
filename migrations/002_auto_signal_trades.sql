alter table public.weeg_trades
  add column if not exists source text not null default 'manual',
  add column if not exists auto_created boolean not null default false,
  add column if not exists asset_profile text,
  add column if not exists signal_reasons jsonb not null default '[]'::jsonb;

create unique index if not exists weeg_auto_open_symbol_interval_idx
  on public.weeg_trades(symbol, timeframe)
  where status in ('PENDING', 'OPEN', 'PARTIAL') and auto_created = true;

create index if not exists weeg_trades_source_idx on public.weeg_trades(source, created_at desc);
