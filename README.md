# Weeg Crypto Trading Intelligence

Weeg is a modular crypto-market analysis dashboard. It is an **analysis and paper-trading system**, not an order-execution bot and it makes no profit guarantee. The current build supports 30 USDT pairs, multi-timeframe chart loading, live 1-minute Binance WebSocket updates, explainable LONG/SHORT/NO TRADE scoring, interactive chart navigation, paper-trade journal tabs, and persistence through Supabase with SQLite fallback.

## Run locally

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
export SUPABASE_URL="https://<project-ref>.supabase.co"
export SUPABASE_KEY="<publishable-or-service-key>"
export REDIS_URL="redis://..."
.venv/bin/python main.py
```

Open `http://localhost:10000`. Render uses `venv/bin/python main.py` from `render.yaml` and `Procfile`; the repository tracks `venv` as a symlink to the build-created `.venv`, so both the current Render setting and the canonical `.venv` build environment resolve to the same interpreter. The application binds to Render's `PORT` automatically. In an existing Render service, set **Language = Python**, **Build Command = `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`**, and **Start Command = `venv/bin/python main.py`**. The tracked `venv -> .venv` symlink ensures the packages installed during build are the same packages used during startup. Do not leave the service language as Rust, because Render will then run `cargo build --release` and ignore the Python build command.

## Supabase

Apply `migrations/001_weeg_schema.sql` to the selected project before enabling remote persistence. The backend uses the REST interface when `SUPABASE_URL` and `SUPABASE_KEY` are present. The schema stores settings, trade journal records, and event-ready lifecycle data. For production, use a server-side key in Render's protected environment variables and do not expose it to the browser.

## Redis

`REDIS_URL` is accepted for low-latency caching and future fan-out. The current implementation initializes the Redis client when available and keeps an in-process cache for the market stream so the app remains usable when Redis is temporarily unavailable.

## Market and analysis behavior

The market adapter loads historical candles from Binance REST and maintains a reconnecting combined WebSocket for the 30 configured symbols. Each event is normalized into UTC timestamped OHLCV data, deduplicated by candle time, and broadcast to the browser. The analysis engine calculates EMA, RSI, ATR, a swing-based structure state, market regime, volume confirmation, confluence confidence, entry, stop loss, take-profit levels, and risk/reward. Thresholds are config-driven and the engine returns `NO TRADE` when confluence or data sufficiency is inadequate.

The browser provides a TradingView-inspired layout without copying TradingView's interface: a watchlist, symbol search, timeframe controls, candle chart with pan/zoom/fit controls, signal explanation, and open/closed paper-trade tabs. The implementation is deliberately paper-only; no exchange API secret or live order endpoint is used.

## الحفظ التلقائي للإشارات المستوفية

يفحص Weeg الأزواج كل دقيقة باستخدام الإطار الافتراضي، وعندما تكون الإشارة `LONG` أو `SHORT` وتستوفي حد الثقة وR:R الخاصين بالأصل، يحفظ صفقة Paper تلقائيًا في `weeg_trades` عند توفر Supabase، أو في SQLite كخطة بديلة. لا ينفذ هذا المسار أي أمر تداول حقيقي.

كل صفقة آلية تحمل `source=auto_signal` و`auto_created=true` وملف الأصل وأسباب الإشارة. يمنع النظام تكرار صفقة آلية نشطة لنفس الزوج والإطار الزمني، بينما تفحص إدارة الخروج جميع الصفقات `PENDING` و`OPEN` و`PARTIAL` كل 5 ثوانٍ باستخدام سعر Binance اللحظي؛ وتغلق الصفقة عند `TAKE_PROFIT_1` أو `STOP_LOSS` وتخزن سبب الخروج والربح/الخسارة. بعد الإغلاق يمكن للفحص التالي إنشاء إشارة آلية جديدة إذا ظلت شروط الإشارة مستوفاة.

يستخدم التطبيق مصدر Binance Spot موحدًا للسعر الحالي: يغذي WebSocket أحداث `@ticker` و`@kline_1m`، ويعرض السعر نفسه في خانة السعر الحالي وآخر شمعة على الشارت. طُبق قيد الصفقة النشطة في `migrations/002_auto_signal_trades.sql`.
