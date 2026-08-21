create table if not exists public.weeg_push_subscriptions (
    endpoint text primary key,
    p256dh text not null,
    auth text not null,
    expiration_time double precision,
    user_agent text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create index if not exists weeg_push_subscriptions_updated_idx
    on public.weeg_push_subscriptions (updated_at desc);

alter table public.weeg_push_subscriptions enable row level security;

create policy "public push subscription access"
    on public.weeg_push_subscriptions
    for all
    using (true)
    with check (true);
