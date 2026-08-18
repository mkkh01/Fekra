# Fekra Trading Brain

نظام بحث وقرار سوقي مستقل يعمل في الإصدار الحالي بوضع **PAPER** فقط. يستقبل بيانات Binance العامة، يجمع الأخبار من خلاصات RSS مجانية بلا API مدفوع، يعرض حالة السوق والأحداث في Dashboard، ويستخدم Gemini للتحليل مع تدوير آمن بين خمسة مفاتيح كحد أقصى. لا توجد في هذه النسخة أي صلاحية لتنفيذ أموال حقيقية.

## التشغيل المحلي

```bash
pip install -r requirements.txt
cp .env.example .env
python main.py
```

يفتح الخادم على `http://localhost:10000`، ويستخدم `PORT` إذا كان متغيرًا موجودًا.

## تشغيل Render

استخدم **Web Service** على Render، والفرع `main`، والأوامر التالية:

```text
Build Command: pip install -r requirements.txt
Start Command: python main.py
Health Check Path: /health
```

التطبيق يستمع على `0.0.0.0` والمنفذ الموجود في `PORT`. ملف `render.yaml` جاهز للخطة المجانية، لكن خدمات Render المجانية قد تتوقف بعد فترة من عدم النشاط ثم تعود عند وصول HTTP أو WebSocket جديد؛ لذلك لا يمكن اعتبار الخطة المجانية تشغيلًا دائمًا بلا توقف في معنى 24/7.

## المتغيرات السرية في Render

| المتغير | مطلوب الآن | الملاحظات |
|---|---:|---|
| `SUPABASE_URL` | نعم | رابط مشروع Fekramee5 |
| `SUPABASE_KEY` | نعم إذا لم تستخدم المتغير المخصص | مفتاح server-side؛ لا ترسله إلى الواجهة |
| `SUPABASE_SERVICE_ROLE_KEY` | مفضل | مفتاح service-role/secret للكتابة مع RLS؛ يفضّل استخدامه بدل `SUPABASE_KEY` |
| `REDIS_URL` | نعم | الحالة الساخنة وحافلة الأحداث؛ يجب أن تكون `redis://...` أو `rediss://...`، والتطبيق ينظف تلقائيًا قيمة `redis-cli -u ...` إذا أُدخلت بالخطأ |
| `GEMINI_API_KEY_1` | نعم للتحليل | الحساب الأول |
| `GEMINI_API_KEY_2` إلى `GEMINI_API_KEY_5` | اختياري | الحسابات الاحتياطية؛ يدور النظام إليها عند فشل الحساب السابق |
| `GEMINI_MODEL` | مستحسن | الافتراضي `gemini-2.5-flash`، ويمكن تغييره إلى نموذج متاح في حسابك |
| `TRADING_MODE` | نعم | يجب أن تبقى `PAPER` |
| `CORS_ORIGINS` | مستحسن | نطاق Render أو `http://localhost:10000` محليًا |
| `JWT_SECRET` | مستحسن | يجهز للمصادقة المستقبلية |

لا ترسل قيم الأسرار في Git أو داخل المحادثة. أضفها مباشرة من Render Dashboard. إذا ظهر خطأ Supabase `401 Invalid API key`، تحقق من أن `SUPABASE_URL` يطابق المشروع وأن `SUPABASE_SERVICE_ROLE_KEY` هو مفتاح service-role/secret الصحيح، ثم أعد النشر. التطبيق يوقف محاولات الكتابة بعد أول 401 حتى لا يكرر السجل نفس الخطأ عشرات المرات. كما يدعم مسار الجذر طلبات `HEAD` التي قد يستخدمها Render في فحص الخدمة.

يمكن استخدام `GEMINI_API_KEY` بدل `GEMINI_API_KEY_1` عند وجود حساب واحد فقط. إذا وُجدت المتغيرات المرقمة، يستخدم النظام أول خمسة منها ويرفض كشف قيمها؛ Dashboard يعرض رقم الحساب وحالة الجاهزية والنجاح والفشل فقط.

## الأخبار المجانية

تستخدم النسخة الحالية خلاصات RSS مباشرة من مصادر عامة، مع تطبيع العنوان والرابط والملخص والتاريخ والمصدر وربط الرموز المذكورة وإزالة التكرار. المصادر الافتراضية هي Crypto Briefing وCCN وCointelegraph وCoinDesk وDecrypt. يمكن تغيير القائمة من `RSS_FEEDS` بصيغة مفصولة بفاصلة منقوطة.

الأخبار دليل مصدره RSS وليست إشارة تداول منفردة. لا يجوز اعتبار عدم وصول خبر دليلًا على عدم وجود خبر. في عقد التقييم الحالي، لا تتجاوز مساهمة الأخبار `10%` من توزيع عوامل الموافقة. يوزع Gemini النسبة المتبقية `90%` ديناميكيًا بين بنية السوق والزخم والسيولة والتقلب وإدارة المخاطر وجودة البيانات بحسب الدورة، ويظهر التوزيع والدرجة في Cycle Summary. يتعرف النظام على صيغ أسماء الأخبار مثل `News/news sentiment` ويطبعها إلى عامل `news` واحد حتى تتطابق النسبة مع مساهمة الأخبار الفعلية. كما يطابق الأدلة الإخبارية مع المقالات المرتبطة لإظهار الرابط الأصلي واسم المصدر وعمر الخبر عند توفرها، بينما تُنسب الأدلة الفنية إلى `Binance historical candles` ولا تُعرض كأنها أخبار. وتُرتب قائمة RSS في Dashboard من الأحدث إلى الأقدم، مع ترجمة أسماء عوامل التقييم وأنواع الأدلة إلى العربية. هذا التوزيع يعبّر عن درجة موافقة تحليلية وليس احتمال ربح أو ضمانًا للنتيجة.

## Gemini Brain

الـBrain لا ينفذ الأدوات بنفسه. قبل كل دورة يحمّل Historical Warm-up من Binance على أطر `5m` و`15m` و`1h` و`4h` و`1d`، ثم يرسل ملخصات الشموع والأخبار إلى Gemini. إذا لم تكتمل الأطر الخمسة، يعيد النظام `WAIT` آمنًا ولا يرسل تحليلًا اتجاهيًا ناقصًا. الأخبار المرسلة تتضمن عنوان المقال والرابط الأصلي والمصدر ووقت النشر وعمر الخبر، ويُطلب من Gemini اعتبار الأخبار الأقدم من 72 ساعة سياقًا خلفيًا لا محفزًا حديثًا.

يطلب الخادم من Gemini إرجاع JSON صريحًا عبر `response_mime_type=application/json`، كما يستطيع استخراج كائن JSON إذا وصل داخل Markdown أو نص تمهيدي. إذا بقيت الاستجابة غير قابلة للتحويل، يسجل النظام سبب المشكلة ويعيد `WAIT` آمنًا بدل عرض أدلة أو درجة موافقة وهمية. لا يُقبل `BUY` أو `SELL_REDUCE` ما لم يتضمن الرد `entry_price` و`stop_loss` و`take_profit` صحيحة، ويُحسب العائد/المخاطرة فقط عند وجود هذه المستويات. كل تحليل يدعم البدائل والأدلة المضادة وعدم اليقين، ولا توجد صلاحية تنفيذ حقيقي.

التدوير بين مفاتيح Gemini يعمل بهذه القاعدة:

1. يبدأ الحساب النشط من الحساب الحالي.
2. عند الخطأ يُسجل الفشل ويُوضع الحساب في cooldown.
3. ينتقل الخادم للحساب التالي الجاهز.
4. Dashboard يعرض `configured_keys` من أصل 5، والحساب النشط، وعدد الطلبات والنجاحات والفشل.
5. عند فشل جميع الحسابات، تكون النتيجة `WAIT` ولا يتوقف تدفق السوق والأخبار.

## Professional decision contract

The Brain now separates `market_bias` (`LONG`, `SHORT`, `NEUTRAL`) from `trade_decision` (`LONG_READY`, `SHORT_READY`, `WAIT`). A directional bias is never sufficient to authorize a paper trade. The deterministic guard rejects directional decisions unless the historical context confirms the required timeframes, data quality is at least 80, the entry trigger is confirmed, the stop is structurally grounded, targets are supported by market levels, and calculated risk/reward is at least 2.0.

Each cycle records a market regime, higher-timeframe alignment, volume-to-average ratio, breakout/retest trigger status, data-quality components, bullish and bearish evidence, three alternative scenarios, invalidation, rejection reasons, a final contradiction review, and the previous/current decision comparison. The runtime evaluates prior PAPER decisions against subsequent Binance prices as `CORRECT_*`, `FALSE_*`, `MISSED_OPPORTUNITY`, or unresolved outcomes. These metrics are analytical diagnostics only and are not realized trading performance.

The Dashboard shows the bias, trade decision, confidence calculated from deterministic factor scores, data quality, regime, Entry/Stop/Targets/RR, trigger status, invalidation, decision history, and accuracy diagnostics. Confidence and Analytical Score use the same deterministic factor calculation, and the contribution weights are normalized to 100%. When no related news exists, the news factor score and contribution are zero. Consensus is shown as `Single AI Analysis` for the current single-analysis architecture rather than being confused with timeframe alignment.

Before a cycle is published, the validation layer removes object and placeholder values, requires meaningful bullish and bearish evidence, validates invalidation price/condition, and forces a safe WAIT when required fields are missing. The Dashboard normalizes arrays, objects, and text fields before rendering, so history, rejection reasons, evidence, events, and scenarios appear as complete strings or structured lists rather than character-by-character output. Supabase persistence remains compatible with the existing action constraint by storing `BUY`/`SELL_REDUCE` internally while exposing `LONG_READY`/`SHORT_READY` to the analytical layer.

## Supabase

تم تطبيق migration `supabase/migrations/0001_trading_brain_foundation.sql` على مشروع Supabase. تنشئ migration الأصول، اللقطات، الأخبار، دورات Brain، استدعاءات الأدوات، الفرضيات، القرارات، الأدلة، الذاكرة، الدروس، المراكز الورقية، الصفقات المغلقة، الأحداث، الإعدادات والتنبيهات، مع تفعيل RLS.

## نقاط API الحالية

```text
GET  /health
GET  /api/assets
GET  /api/system/health
GET  /api/system/gemini-usage
GET  /api/brain/status
GET  /api/cycles
GET  /api/news
GET  /api/open-trades
POST /api/brain/reassess/{symbol}
POST /api/system/kill
WS   /ws/dashboard
```

## الوضع الحالي والحدود

الإصدار الحالي هو MVP تشغيلي: Dashboard وبيانات Binance العامة وRSS وRedis/Supabase health checks وGemini key rotation ودورة تحليل PAPER. ما يزال يلزم قبل اعتبار النظام منصة كاملة إضافة جمع الشموع التاريخية، أدوات structure/FVG/liquidity، Paper Broker كامل مع fees/slippage، تقييم الأداء مقابل baselines، مصادقة المستخدم، واسترجاع الذاكرة الدلالية.

لا تتم إضافة `BINANCE_API_KEY` أو `BINANCE_API_SECRET` الآن؛ بيانات السوق العامة لا تحتاجهما، والتنفيذ الحقيقي مؤجل عمدًا.
