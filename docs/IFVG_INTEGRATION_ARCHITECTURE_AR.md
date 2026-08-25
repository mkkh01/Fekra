# معمارية دمج استراتيجية IFVG Spot v1.2.x

## الهدف

إضافة استراتيجية IFVG Spot Long-only إلى Fekra كنظام Paper Trading مستقل بالكامل عن استراتيجية Weeg الحالية. لا تستخدم IFVG جدول `weeg_trades`، ولا محرك `app.analysis.engine`، ولا نقاط الدخول/الخروج القديمة، ولا مدير الخروج القديم. يشترك النظامان في مصدر بيانات السوق وطبقة المصادقة العامة فقط، مع بقاء كل قرارات IFVG وحالاتها ودفاترها ولوحاتها وإشعاراتها منفصلة.

## ثوابت الاستراتيجية

```text
strategy_id = IFVG_SPOT_V1_2
market = Binance Spot paper trading
side = LONG only
higher_timeframe = 4h
structure_timeframe = 1h
setup_timeframe = 15m
confirmation_timeframe = 5m
```

نسخة التنفيذ ستضيف صراحة بوابة Gross RR، وستستخدم آخر ATR14 مغلق من 15m عند قرار الدخول، وستجعل `risk_per_unit_quote` موحدًا مع رسوم الدخول ووقف الخسارة، وستفشل مغلقًا عند فجوة داخل أي نافذة قرار. لا يوجد في التصميم أي مسار لاستدعاء Binance Trade API أو إرسال أمر حقيقي.

## حدود العزل

| المجال | الاستراتيجية الحالية | IFVG |
|---|---|---|
| المحرك | `app.analysis.engine` | `app/strategies/ifvg/engine.py` |
| الجداول | `weeg_trades`, `weeg_trade_events` | جداول `ifvg_*` مستقلة |
| دورة الفحص | `_auto_signal_loop` | `ifvg_service` ودورة مستقلة |
| مدير الخروج | `_manage_open_trades` | مدير Paper Fill/Exit مستقل |
| API | `/api/trades`, `/api/signals` | `/api/ifvg/*` |
| WebSocket | أحداث السوق العامة | أحداث `ifvg_*` المنفصلة |
| اللوحة | تبويبات Weeg الحالية | مساحة IFVG مستقلة وفلاتر `strategy_id` ثابتة |
| الإشعارات | نصوص Weeg العامة | formatter وقناة IFVG منفصلان |
| الإعدادات | `weeg_settings` ومتغيرات WEEG | `ifvg_config` و`IFVG_*` |
| Backtest | `run_backtest` العام | Backtest بآلة حالات وfills وcosts خاصة |

## مخطط البيانات المقترح

### `ifvg_setups`

يمثل setup lifecycle من FVG إلى IFVG وretest والرفض أو الانتهاء. يحتوي على `id`, `user_id`, `strategy_id`, `symbol`, `source_fvg_id`, `state`, `state_version`, `zone_low`, `zone_high`, `sweep_time`, `inversion_time`, `retest_start_time`, `expires_at`, `setup_snapshot_id`, `config_version`, `score`, `failed_gates`, `created_at`, و`updated_at`.

يُمنع تكرار setup عبر مفتاح idempotency يضم `strategy_id`, `symbol`, `source_fvg_id`, و`inversion_time`. لا يؤدي ظهور FVG متداخل جديد إلى حذف setup قائم في `IFVG_ACTIVE`؛ يسجل المحرك قرار shadowing أو overlap كحدث منفصل.

### `ifvg_trades`

يمثل قرار Paper Trading، وليس مجرد نسخة من `weeg_trades`. يحتوي على `id`, `user_id`, `strategy_id`, `setup_id`, `symbol`, `direction`, `state`, `entry_reference`, `entry_fill`, `stop_price`, `stop_fill`, `target_price`, `target_fill_gross`, `gross_rr`, `net_rr`, `risk_per_unit_quote`, `risk_amount_quote`, `quantity`, `entry_fee_quote`, `stop_fee_quote`, `target_fee_quote`, `fill_model`, `config_snapshot`, `data_snapshot_id`, `score`, `score_version`, `failed_gates`, `decision_time`, `opened_at`, `closed_at`, `exit_reason`, `exit_fill`, `realized_pnl_quote`, و`result`.

القيد الأساسي: `direction = LONG` فقط، ولا ينشئ المسار صفقة إذا كانت `strategy_id` أو `setup_id` غير موجودة.

### `ifvg_state_events`

سجل append-only لكل انتقال. يحتوي على `id`, `setup_id` أو `trade_id`, `from_state`, `to_state`, `reason_code`, `reason_detail`, `event_time`, `candle_time`, `data_snapshot_id`, و`created_at`. كل انتقال شرعي وغير شرعي في الاختبارات يمر من نفس validator.

### `ifvg_fills`

سجل Paper Fill مفصل يسمح بالـpartial fills. يحتوي على `trade_id`, `fill_role` (`ENTRY`, `TARGET`, `STOP`), `reference_price`, `executable_price`, `quantity`, `fee_quote`, `fee_asset`, `spread_component`, `slippage_component`, `latency_component`, `event_time`, `intent_time`, `execution_time`, و`fill_sequence`.

### `ifvg_snapshots`

يحفظ لقطة القرار كاملة: الشموع المستخدمة لكل فاصل، جودة البيانات، شبكة الفواصل والفجوات، ticker/bid/ask إن وجدا، exchangeInfo والـprecision، ATR reference، إعدادات المحرك، hash المحتوى، ووقت اللقطة. لا يعتمد OOS أو إعادة بناء القرار على حالة cache متغيرة.

### `ifvg_config`

إعدادات versioned لكل مستخدم أو إعداد نظامي واضح: gates، rubrics، RR، ATR reference، tolerance، fill model، limits، notification preferences، و`config_version`. لا توجد قيمة افتراضية صامتة لـ`max_position_value_quote` أو `max_global_open_positions`؛ الغياب يسبب `CONFIG_UNAVAILABLE`.

## الخدمات ودورات التشغيل

تعمل خدمة IFVG على ثلاث طبقات منفصلة. طبقة discovery تقرأ 4h/1h/15m/5m المغلقة وتبني snapshots وتحدّث setups. طبقة decision تطبق آلة الحالات والبوابات والـScore وتنتج `ENTRY_ELIGIBLE` أو سبب رفض. طبقة paper execution تراقب السعر وتبني fills وتغلق Paper Positions عند TP أو Stop، مع reconciliation وعدم افتراض نجاح عند أي حالة غير محسومة.

لا تستخدم خدمة IFVG `asyncio.create_task` غير القابل للتتبع لكل صفقة؛ لكل loop مرجع lifecycle وإلغاء graceful أثناء shutdown، وتُحجز الموارد ذريًا بمفتاح `strategy_id + symbol + setup_id`.

## واجهات API

```text
GET  /api/ifvg/health
GET  /api/ifvg/config
POST /api/ifvg/config
GET  /api/ifvg/signals/{symbol}
GET  /api/ifvg/setups
GET  /api/ifvg/setups/{setup_id}
GET  /api/ifvg/trades
GET  /api/ifvg/trades/{trade_id}
GET  /api/ifvg/fills/{trade_id}
GET  /api/ifvg/events/{setup_id}
GET  /api/ifvg/backtest/{symbol}
GET  /api/ifvg/summary
```

كل endpoint IFVG يفرض المصادقة، ويُرجع `strategy_id` في الاستجابة، ويطبق user ownership. لا تغير endpoints الاستراتيجية الحالية semantics أو نتائجها.

## الإشعارات واللوحة

تستخدم إشعارات IFVG formatter مستقلًا بعنوان واضح `IFVG Spot Paper` وبحقول setup/state/score/entry/stop/target/NetRR. لا يعاد استخدام formatter الخاص بـWeeg لأن حقول IFVG ليست `take_profit_1` و`take_profit_2`. وتعرض اللوحة IFVG: setups النشطة، حالات الآلة، failed gates، fills، المخاطرة المحجوزة، الصفقات المفتوحة والمغلقة، وhealth للفواصل الأربعة، دون دمجها في عدادات Weeg.

## قواعد عدم التداخل

لا يقرأ محرك IFVG من `weeg_trades` لاتخاذ قرار، ولا يكتب إليها. ولا يقرأ مدير Weeg صفقات `ifvg_trades`. ويمكن للتقرير العام أن يعرض ملخصين منفصلين، لكن لا يجوز جمع المخاطر أو عدد المراكز إلا في طبقة PortfolioRiskConfig صريحة ومشتركة.

## بوابة الجاهزية

لا تُعتبر الاستراتيجية جاهزة للعمل إلا بعد تطبيق migration، نجاح اختبارات الوحدة والتكامل، اختبار ownership/RLS، اختبار عدم تداخل الاستراتيجيتين، اختبار restart/reconciliation، تشغيل backtest بصافي التكاليف، ثم Paper Canary في وضع مراقبة مضبوط. الجاهزية التشغيلية لا تعني إثبات الربحية.
