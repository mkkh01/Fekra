-- Lock down service-only auxiliary tables and cover remaining foreign keys.

do $$
declare
    table_name text;
    service_tables text[] := array[
        'alerts', 'assets', 'brain_counter_cases', 'brain_cycles', 'brain_decisions',
        'brain_evidence', 'brain_hypotheses', 'brain_lessons', 'brain_memory',
        'brain_tool_calls', 'candles_metadata', 'closed_trades', 'execution_events',
        'market_snapshots', 'news', 'news_entities', 'paper_positions',
        'system_config', 'system_events'
    ];
begin
    foreach table_name in array service_tables loop
        if to_regclass('public.' || table_name) is not null then
            execute format('revoke all on table public.%I from anon, authenticated', table_name);
            execute format('drop policy if exists deny_public_access on public.%I', table_name);
            execute format('create policy deny_public_access on public.%I for all to anon, authenticated using (false) with check (false)', table_name);
        end if;
    end loop;
end $$;

create index if not exists brain_counter_cases_cycle_id_idx on public.brain_counter_cases(cycle_id);
create index if not exists brain_decisions_cycle_id_idx on public.brain_decisions(cycle_id);
create index if not exists brain_decisions_symbol_idx on public.brain_decisions(symbol);
create index if not exists brain_evidence_cycle_id_idx on public.brain_evidence(cycle_id);
create index if not exists brain_hypotheses_cycle_id_idx on public.brain_hypotheses(cycle_id);
create index if not exists brain_lessons_cycle_id_idx on public.brain_lessons(cycle_id);
create index if not exists brain_lessons_symbol_idx on public.brain_lessons(symbol);
create index if not exists brain_memory_symbol_idx on public.brain_memory(symbol);
create index if not exists brain_tool_calls_cycle_id_idx on public.brain_tool_calls(cycle_id);
create index if not exists closed_trades_exit_cycle_id_idx on public.closed_trades(exit_cycle_id);
create index if not exists closed_trades_lesson_id_idx on public.closed_trades(lesson_id);
create index if not exists closed_trades_original_cycle_id_idx on public.closed_trades(original_cycle_id);
create index if not exists closed_trades_symbol_idx on public.closed_trades(symbol);
create index if not exists news_entities_news_id_idx on public.news_entities(news_id);
create index if not exists news_entities_symbol_idx on public.news_entities(symbol);
create index if not exists paper_positions_original_cycle_id_idx on public.paper_positions(original_cycle_id);
create index if not exists paper_positions_symbol_idx on public.paper_positions(symbol);
