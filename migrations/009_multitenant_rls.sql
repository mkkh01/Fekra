-- Fekra multi-user security baseline.
-- System-generated rows may keep user_id NULL; manual rows must belong to auth.uid().

do $$
declare
    policy_row record;
begin
    for policy_row in
        select policyname, tablename
        from pg_policies
        where schemaname = 'public'
          and tablename in ('weeg_trades', 'weeg_settings', 'weeg_shadow_signals', 'weeg_push_subscriptions')
    loop
        execute format('drop policy if exists %I on public.%I', policy_row.policyname, policy_row.tablename);
    end loop;
end $$;

alter table public.weeg_trades enable row level security;
alter table public.weeg_settings enable row level security;
alter table public.weeg_shadow_signals enable row level security;
alter table public.weeg_push_subscriptions enable row level security;

create policy weeg_trades_select_visible on public.weeg_trades
    for select to authenticated
    using (user_id is null or user_id = (select auth.uid()));
create policy weeg_trades_insert_owner on public.weeg_trades
    for insert to authenticated
    with check (user_id = (select auth.uid()));
create policy weeg_trades_update_owner on public.weeg_trades
    for update to authenticated
    using (user_id = (select auth.uid()))
    with check (user_id = (select auth.uid()));
create policy weeg_trades_delete_owner on public.weeg_trades
    for delete to authenticated
    using (user_id = (select auth.uid()));

create policy weeg_settings_owner_all on public.weeg_settings
    for all to authenticated
    using (user_id = (select auth.uid()))
    with check (user_id = (select auth.uid()));

create policy weeg_shadow_select_visible on public.weeg_shadow_signals
    for select to authenticated
    using (user_id is null or user_id = (select auth.uid()));
create policy weeg_shadow_insert_owner on public.weeg_shadow_signals
    for insert to authenticated
    with check (user_id = (select auth.uid()));
create policy weeg_shadow_update_owner on public.weeg_shadow_signals
    for update to authenticated
    using (user_id = (select auth.uid()))
    with check (user_id = (select auth.uid()));
create policy weeg_shadow_delete_owner on public.weeg_shadow_signals
    for delete to authenticated
    using (user_id = (select auth.uid()));

create policy weeg_push_owner_all on public.weeg_push_subscriptions
    for all to authenticated
    using (user_id = (select auth.uid()))
    with check (user_id = (select auth.uid()));

revoke all on table public.weeg_trades, public.weeg_settings, public.weeg_shadow_signals, public.weeg_push_subscriptions from anon;
revoke all on table public.weeg_trades, public.weeg_settings, public.weeg_shadow_signals, public.weeg_push_subscriptions from authenticated;
grant select, insert, update, delete on table public.weeg_trades, public.weeg_settings, public.weeg_shadow_signals, public.weeg_push_subscriptions to authenticated;

create index if not exists idx_weeg_trades_user_id on public.weeg_trades(user_id);
create index if not exists idx_weeg_settings_user_id on public.weeg_settings(user_id);
create index if not exists idx_weeg_shadow_signals_user_id on public.weeg_shadow_signals(user_id);
create index if not exists idx_weeg_push_subscriptions_user_id on public.weeg_push_subscriptions(user_id);
create unique index if not exists uq_weeg_settings_user_id on public.weeg_settings(user_id) where user_id is not null;

alter table public.weeg_trades drop constraint if exists weeg_trades_manual_owner_check;
alter table public.weeg_trades add constraint weeg_trades_manual_owner_check check (auto_created or user_id is not null) not valid;
alter table public.weeg_trades validate constraint weeg_trades_manual_owner_check;
