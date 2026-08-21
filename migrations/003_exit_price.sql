-- Store the actual market price captured when a trade exits.
alter table public.weeg_trades
  add column if not exists exit_price numeric;

-- Support recent closed-trade queries ordered by exit time.
create index if not exists weeg_trades_closed_at_idx
  on public.weeg_trades(closed_at desc);

comment on column public.weeg_trades.exit_price is 'Actual market price captured when the trade reaches TP1 or STOP_LOSS';
