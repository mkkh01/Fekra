-- IFVG Spot v1.2.x isolated Paper Trading schema.
-- Additive only: does not alter or reference the legacy Weeg trade contract.

create table if not exists public.ifvg_configs (
  id uuid primary key default gen_random_uuid(),
  user_id uuid null references auth.users(id) on delete cascade,
  strategy_id text not null default 'IFVG_SPOT_V1_2',
  config_version text not null,
  enabled boolean not null default false,
  payload jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint ifvg_configs_strategy_check check (strategy_id = 'IFVG_SPOT_V1_2'),
  constraint ifvg_configs_version_nonempty check (length(trim(config_version)) > 0)
);

create unique index if not exists ifvg_configs_user_strategy_version_uq
  on public.ifvg_configs(user_id, strategy_id, config_version);
create unique index if not exists ifvg_configs_one_enabled_per_user
  on public.ifvg_configs(user_id, strategy_id) where enabled;

create table if not exists public.ifvg_snapshots (
  id uuid primary key default gen_random_uuid(),
  user_id uuid null references auth.users(id) on delete set null,
  strategy_id text not null default 'IFVG_SPOT_V1_2',
  symbol text not null,
  decision_time timestamptz not null,
  data_asof timestamptz,
  config_version text not null,
  content_hash text not null,
  payload jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  constraint ifvg_snapshots_strategy_check check (strategy_id = 'IFVG_SPOT_V1_2'),
  constraint ifvg_snapshots_hash_nonempty check (length(trim(content_hash)) > 0)
);

create unique index if not exists ifvg_snapshots_hash_uq
  on public.ifvg_snapshots(content_hash);
create index if not exists ifvg_snapshots_symbol_time_idx
  on public.ifvg_snapshots(symbol, decision_time desc);

create table if not exists public.ifvg_setups (
  id uuid primary key default gen_random_uuid(),
  user_id uuid null references auth.users(id) on delete set null,
  strategy_id text not null default 'IFVG_SPOT_V1_2',
  symbol text not null,
  source_fvg_id text not null,
  state text not null default 'FVG_DETECTED',
  state_version integer not null default 1,
  direction text not null default 'LONG',
  zone_low numeric not null,
  zone_high numeric not null,
  sweep_time timestamptz,
  inversion_time timestamptz,
  retest_start_time timestamptz,
  expires_at timestamptz,
  setup_snapshot_id uuid references public.ifvg_snapshots(id) on delete set null,
  config_version text not null,
  score numeric,
  score_version text,
  failed_gates jsonb not null default '[]'::jsonb,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint ifvg_setups_strategy_check check (strategy_id = 'IFVG_SPOT_V1_2'),
  constraint ifvg_setups_direction_check check (direction = 'LONG'),
  constraint ifvg_setups_zone_check check (zone_low < zone_high),
  constraint ifvg_setups_state_check check (state in (
    'FVG_DETECTED','FVG_ACTIVE','INVERTED','IFVG_ACTIVE','WAITING_RETEST',
    'RETEST_DETECTED','WAITING_CONFIRMATION','CONFIRMED','ENTRY_ELIGIBLE',
    'ORDER_INTENT','ORDER_SUBMITTED','ORDER_PARTIALLY_FILLED','ORDER_FILLED',
    'POSITION_OPEN','TP_FILLED','STOP_TRIGGERED','POSITION_CLOSED','EXPIRED',
    'INVALIDATED','REJECTED','AMBIGUOUS','RECONCILIATION_REQUIRED'
  ))
);

create unique index if not exists ifvg_setups_idempotency_uq
  on public.ifvg_setups(strategy_id, symbol, source_fvg_id, inversion_time);
create index if not exists ifvg_setups_symbol_state_idx
  on public.ifvg_setups(symbol, state, updated_at desc);
create index if not exists ifvg_setups_user_idx
  on public.ifvg_setups(user_id);

create table if not exists public.ifvg_trades (
  id uuid primary key default gen_random_uuid(),
  user_id uuid null references auth.users(id) on delete set null,
  strategy_id text not null default 'IFVG_SPOT_V1_2',
  setup_id uuid not null unique references public.ifvg_setups(id) on delete restrict,
  symbol text not null,
  direction text not null default 'LONG',
  state text not null default 'ENTRY_ELIGIBLE',
  entry_reference numeric not null,
  entry_fill numeric,
  stop_price numeric not null,
  stop_fill numeric,
  target_price numeric not null,
  target_fill_gross numeric,
  exit_fill numeric,
  gross_rr numeric,
  net_rr numeric,
  risk_per_unit_quote numeric,
  risk_amount_quote numeric,
  quantity numeric,
  entry_fee_quote numeric not null default 0,
  stop_fee_quote numeric not null default 0,
  target_fee_quote numeric not null default 0,
  exit_fee_quote numeric not null default 0,
  realized_pnl_quote numeric,
  fill_model jsonb not null default '{}'::jsonb,
  config_snapshot jsonb not null default '{}'::jsonb,
  data_snapshot_id uuid references public.ifvg_snapshots(id) on delete set null,
  score numeric,
  score_version text,
  failed_gates jsonb not null default '[]'::jsonb,
  decision_time timestamptz not null,
  opened_at timestamptz,
  closed_at timestamptz,
  exit_reason text,
  result text,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint ifvg_trades_strategy_check check (strategy_id = 'IFVG_SPOT_V1_2'),
  constraint ifvg_trades_direction_check check (direction = 'LONG'),
  constraint ifvg_trades_state_check check (state in (
    'ENTRY_ELIGIBLE','ORDER_INTENT','ORDER_SUBMITTED','ORDER_PARTIALLY_FILLED',
    'ORDER_FILLED','POSITION_OPEN','TP_FILLED','STOP_TRIGGERED',
    'POSITION_CLOSED','REJECTED','AMBIGUOUS','RECONCILIATION_REQUIRED'
  )),
  constraint ifvg_trades_result_check check (result is null or result in ('WIN','LOSS','BREAKEVEN','AMBIGUOUS')),
  constraint ifvg_trades_prices_check check (stop_price < target_price)
);

create unique index if not exists ifvg_trades_active_symbol_uq
  on public.ifvg_trades(strategy_id, symbol)
  where state in ('ENTRY_ELIGIBLE','ORDER_INTENT','ORDER_SUBMITTED','ORDER_PARTIALLY_FILLED','ORDER_FILLED','POSITION_OPEN');
create index if not exists ifvg_trades_user_state_idx
  on public.ifvg_trades(user_id, state, decision_time desc);
create index if not exists ifvg_trades_symbol_time_idx
  on public.ifvg_trades(symbol, decision_time desc);

create table if not exists public.ifvg_state_events (
  id bigint generated by default as identity primary key,
  setup_id uuid references public.ifvg_setups(id) on delete cascade,
  trade_id uuid references public.ifvg_trades(id) on delete cascade,
  from_state text,
  to_state text not null,
  reason_code text not null,
  reason_detail text,
  event_time timestamptz not null,
  candle_time timestamptz,
  data_snapshot_id uuid references public.ifvg_snapshots(id) on delete set null,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  constraint ifvg_state_events_owner_check check (setup_id is not null or trade_id is not null)
);

create index if not exists ifvg_state_events_setup_idx
  on public.ifvg_state_events(setup_id, created_at desc);
create index if not exists ifvg_state_events_trade_idx
  on public.ifvg_state_events(trade_id, created_at desc);

create table if not exists public.ifvg_fills (
  id bigint generated by default as identity primary key,
  trade_id uuid not null references public.ifvg_trades(id) on delete cascade,
  fill_role text not null,
  fill_sequence integer not null default 1,
  reference_price numeric not null,
  executable_price numeric not null,
  quantity numeric not null,
  fee_quote numeric not null default 0,
  fee_asset text,
  spread_component numeric not null default 0,
  slippage_component numeric not null default 0,
  latency_component numeric not null default 0,
  event_time timestamptz not null,
  intent_time timestamptz,
  execution_time timestamptz,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  constraint ifvg_fills_role_check check (fill_role in ('ENTRY','TARGET','STOP')),
  constraint ifvg_fills_positive_check check (quantity > 0 and executable_price > 0 and fee_quote >= 0),
  constraint ifvg_fills_sequence_check check (fill_sequence > 0)
);

create unique index if not exists ifvg_fills_sequence_uq
  on public.ifvg_fills(trade_id, fill_role, fill_sequence);
create index if not exists ifvg_fills_trade_idx
  on public.ifvg_fills(trade_id, event_time desc);

create table if not exists public.ifvg_reservations (
  id uuid primary key default gen_random_uuid(),
  user_id uuid null references auth.users(id) on delete set null,
  strategy_id text not null default 'IFVG_SPOT_V1_2',
  reservation_key text not null,
  symbol text not null,
  trade_id uuid references public.ifvg_trades(id) on delete set null,
  reserved_quantity numeric not null default 0,
  reserved_quote numeric not null default 0,
  reserved_risk_quote numeric not null default 0,
  status text not null default 'ACTIVE',
  expires_at timestamptz,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  released_at timestamptz,
  constraint ifvg_reservations_strategy_check check (strategy_id = 'IFVG_SPOT_V1_2'),
  constraint ifvg_reservations_status_check check (status in ('ACTIVE','RELEASED','CONSUMED','EXPIRED')),
  constraint ifvg_reservations_nonnegative_check check (reserved_quantity >= 0 and reserved_quote >= 0 and reserved_risk_quote >= 0)
);

create unique index if not exists ifvg_reservations_key_uq
  on public.ifvg_reservations(strategy_id, reservation_key);
create unique index if not exists ifvg_reservations_active_symbol_uq
  on public.ifvg_reservations(strategy_id, symbol) where status = 'ACTIVE';
create index if not exists ifvg_reservations_user_status_idx
  on public.ifvg_reservations(user_id, status, created_at desc);

-- Keep updated_at deterministic for direct SQL updates.
create or replace function public.ifvg_touch_updated_at()
returns trigger
language plpgsql
security invoker
set search_path = public
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

drop trigger if exists ifvg_configs_touch_updated_at on public.ifvg_configs;
create trigger ifvg_configs_touch_updated_at before update on public.ifvg_configs
for each row execute function public.ifvg_touch_updated_at();

drop trigger if exists ifvg_setups_touch_updated_at on public.ifvg_setups;
create trigger ifvg_setups_touch_updated_at before update on public.ifvg_setups
for each row execute function public.ifvg_touch_updated_at();

drop trigger if exists ifvg_trades_touch_updated_at on public.ifvg_trades;
create trigger ifvg_trades_touch_updated_at before update on public.ifvg_trades
for each row execute function public.ifvg_touch_updated_at();

-- RLS: system-owned rows (user_id NULL) are visible, user-owned rows are private.
-- The server uses the service-role key and bypasses RLS; browser clients remain authenticated-only.
do $$
declare
  table_name text;
  policy_row record;
  ifvg_tables text[] := array[
    'ifvg_configs','ifvg_snapshots','ifvg_setups','ifvg_trades',
    'ifvg_state_events','ifvg_fills','ifvg_reservations'
  ];
begin
  foreach table_name in array ifvg_tables loop
    execute format('alter table public.%I enable row level security', table_name);
    for policy_row in select policyname from pg_policies where schemaname = 'public' and tablename = table_name loop
      execute format('drop policy if exists %I on public.%I', policy_row.policyname, table_name);
    end loop;
  end loop;
end $$;

create policy ifvg_configs_select_visible on public.ifvg_configs
  for select to authenticated using (user_id is null or user_id = (select auth.uid()));
create policy ifvg_configs_insert_owner on public.ifvg_configs
  for insert to authenticated with check (user_id = (select auth.uid()));
create policy ifvg_configs_update_owner on public.ifvg_configs
  for update to authenticated using (user_id = (select auth.uid())) with check (user_id = (select auth.uid()));
create policy ifvg_configs_delete_owner on public.ifvg_configs
  for delete to authenticated using (user_id = (select auth.uid()));

create policy ifvg_snapshots_select_visible on public.ifvg_snapshots
  for select to authenticated using (user_id is null or user_id = (select auth.uid()));
create policy ifvg_snapshots_insert_owner on public.ifvg_snapshots
  for insert to authenticated with check (user_id = (select auth.uid()));

create policy ifvg_setups_select_visible on public.ifvg_setups
  for select to authenticated using (user_id is null or user_id = (select auth.uid()));
create policy ifvg_setups_insert_owner on public.ifvg_setups
  for insert to authenticated with check (user_id = (select auth.uid()));
create policy ifvg_setups_update_owner on public.ifvg_setups
  for update to authenticated using (user_id = (select auth.uid())) with check (user_id = (select auth.uid()));

create policy ifvg_trades_select_visible on public.ifvg_trades
  for select to authenticated using (user_id is null or user_id = (select auth.uid()));
create policy ifvg_trades_insert_owner on public.ifvg_trades
  for insert to authenticated with check (user_id = (select auth.uid()));
create policy ifvg_trades_update_owner on public.ifvg_trades
  for update to authenticated using (user_id = (select auth.uid())) with check (user_id = (select auth.uid()));
create policy ifvg_trades_delete_owner on public.ifvg_trades
  for delete to authenticated using (user_id = (select auth.uid()));

create policy ifvg_state_events_select_visible on public.ifvg_state_events
  for select to authenticated using (
    exists (select 1 from public.ifvg_setups s where s.id = setup_id and (s.user_id is null or s.user_id = (select auth.uid())))
    or exists (select 1 from public.ifvg_trades t where t.id = trade_id and (t.user_id is null or t.user_id = (select auth.uid())))
  );
create policy ifvg_state_events_insert_owner on public.ifvg_state_events
  for insert to authenticated with check (
    exists (select 1 from public.ifvg_setups s where s.id = setup_id and s.user_id = (select auth.uid()))
    or exists (select 1 from public.ifvg_trades t where t.id = trade_id and t.user_id = (select auth.uid()))
  );

create policy ifvg_fills_select_visible on public.ifvg_fills
  for select to authenticated using (
    exists (select 1 from public.ifvg_trades t where t.id = trade_id and (t.user_id is null or t.user_id = (select auth.uid())))
  );
create policy ifvg_fills_insert_owner on public.ifvg_fills
  for insert to authenticated with check (
    exists (select 1 from public.ifvg_trades t where t.id = trade_id and t.user_id = (select auth.uid()))
  );

create policy ifvg_reservations_select_visible on public.ifvg_reservations
  for select to authenticated using (user_id is null or user_id = (select auth.uid()));
create policy ifvg_reservations_insert_owner on public.ifvg_reservations
  for insert to authenticated with check (user_id = (select auth.uid()));
create policy ifvg_reservations_update_owner on public.ifvg_reservations
  for update to authenticated using (user_id = (select auth.uid())) with check (user_id = (select auth.uid()));

revoke all on table public.ifvg_configs, public.ifvg_snapshots, public.ifvg_setups,
  public.ifvg_trades, public.ifvg_state_events, public.ifvg_fills, public.ifvg_reservations
  from anon;
revoke all on table public.ifvg_configs, public.ifvg_snapshots, public.ifvg_setups,
  public.ifvg_trades, public.ifvg_state_events, public.ifvg_fills, public.ifvg_reservations
  from authenticated;
grant select, insert, update, delete on table public.ifvg_configs, public.ifvg_snapshots,
  public.ifvg_setups, public.ifvg_trades, public.ifvg_state_events, public.ifvg_fills,
  public.ifvg_reservations to authenticated;
grant usage, select on sequence public.ifvg_state_events_id_seq, public.ifvg_fills_id_seq to authenticated;
