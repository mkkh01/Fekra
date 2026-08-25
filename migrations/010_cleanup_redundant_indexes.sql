-- Existing composite indexes already cover the user-scoped queries.
drop index if exists public.idx_weeg_trades_user_id;
drop index if exists public.idx_weeg_settings_user_id;
drop index if exists public.idx_weeg_shadow_signals_user_id;
drop index if exists public.idx_weeg_push_subscriptions_user_id;
