alter table public.weeg_trades
    add column if not exists mtf_alignment text,
    add column if not exists mtf_vetoes jsonb not null default '[]'::jsonb,
    add column if not exists mtf_timeframes jsonb;

create index if not exists weeg_trades_mtf_alignment_idx
    on public.weeg_trades (mtf_alignment);
