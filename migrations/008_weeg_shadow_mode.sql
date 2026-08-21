-- Additive telemetry schema for Weeg safe rollout and Shadow Mode.
-- No existing rows are deleted or rewritten by this migration.

alter table public.weeg_trades add column if not exists signal_candle_time timestamptz;
alter table public.weeg_trades add column if not exists signal_age_seconds numeric;
alter table public.weeg_trades add column if not exists market_data_asof timestamptz;
alter table public.weeg_trades add column if not exists signal_price numeric;
alter table public.weeg_trades add column if not exists entry_deviation_pct numeric;
alter table public.weeg_trades add column if not exists monitor_entry_limit_pct numeric;
alter table public.weeg_trades add column if not exists expected_rr_after_execution numeric;
alter table public.weeg_trades add column if not exists reversal_risk integer;
alter table public.weeg_trades add column if not exists reversal_risk_components jsonb not null default '{}'::jsonb;
alter table public.weeg_trades add column if not exists overextension_metrics jsonb not null default '{}'::jsonb;
alter table public.weeg_trades add column if not exists exit_checked_at timestamptz;
alter table public.weeg_trades add column if not exists stop_moved_to_breakeven boolean not null default false;

create table if not exists public.weeg_shadow_signals (
  id uuid primary key default gen_random_uuid(),
  user_id uuid null,
  symbol text not null,
  timeframe text not null,
  direction text check (direction in ('LONG', 'SHORT')),
  decision_time timestamptz not null default now(),
  signal_candle_time timestamptz not null,
  signal_age_seconds numeric,
  market_data_asof timestamptz,
  signal_price numeric,
  simulated_entry_price numeric,
  simulated_stop_loss numeric,
  simulated_take_profit_1 numeric,
  simulated_take_profit_2 numeric,
  entry_deviation_pct numeric,
  monitor_entry_limit_pct numeric,
  expected_rr_after_execution numeric,
  regime text,
  mtf_alignment text,
  reversal_risk integer not null default 0,
  reversal_risk_components jsonb not null default '{}'::jsonb,
  overextension_metrics jsonb not null default '{}'::jsonb,
  would_have_executed boolean not null default false,
  would_block boolean not null default false,
  blocked_reasons jsonb not null default '[]'::jsonb,
  warning_reasons jsonb not null default '[]'::jsonb,
  outcome_status text not null default 'PENDING' check (outcome_status in ('PENDING', 'WIN', 'LOSS', 'EXPIRED', 'NOT_EVALUATED')),
  outcome_pnl numeric,
  outcome_checked_at timestamptz,
  created_at timestamptz not null default now()
);

create unique index if not exists weeg_shadow_signal_key_idx
  on public.weeg_shadow_signals(symbol, timeframe, signal_candle_time);
create index if not exists weeg_shadow_signal_decision_idx
  on public.weeg_shadow_signals(decision_time desc);
create index if not exists weeg_shadow_signal_outcome_idx
  on public.weeg_shadow_signals(outcome_status, created_at desc);
create index if not exists weeg_shadow_signal_block_idx
  on public.weeg_shadow_signals(would_block, created_at desc);
create index if not exists weeg_trades_signal_candle_idx
  on public.weeg_trades(symbol, timeframe, signal_candle_time desc);

alter table public.weeg_shadow_signals enable row level security;

do $$
begin
  if not exists (
    select 1 from pg_policies
    where schemaname = 'public' and tablename = 'weeg_shadow_signals'
      and policyname = 'weeg shadow owner select'
  ) then
    create policy "weeg shadow owner select"
      on public.weeg_shadow_signals for select to authenticated
      using (user_id = auth.uid());
  end if;

  if not exists (
    select 1 from pg_policies
    where schemaname = 'public' and tablename = 'weeg_shadow_signals'
      and policyname = 'weeg shadow owner insert'
  ) then
    create policy "weeg shadow owner insert"
      on public.weeg_shadow_signals for insert to authenticated
      with check (user_id = auth.uid());
  end if;

  if not exists (
    select 1 from pg_policies
    where schemaname = 'public' and tablename = 'weeg_shadow_signals'
      and policyname = 'weeg shadow owner update'
  ) then
    create policy "weeg shadow owner update"
      on public.weeg_shadow_signals for update to authenticated
      using (user_id = auth.uid()) with check (user_id = auth.uid());
  end if;
end
$$;

comment on table public.weeg_shadow_signals is 'Weeg shadow candidates and counterfactual outcomes; never a live order instruction.';
comment on column public.weeg_shadow_signals.would_block is 'Safety rule outcome in shadow mode; does not block paper execution until explicitly enabled and validated.';
comment on column public.weeg_trades.signal_price is 'Close of the last fully closed signal candle, distinct from live entry.';
comment on column public.weeg_trades.entry_deviation_pct is 'Absolute deviation between live paper entry and signal candle close.';
