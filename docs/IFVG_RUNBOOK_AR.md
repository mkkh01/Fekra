# دليل تشغيل IFVG Spot Paper

## الحالة

تم دمج `IFVG_SPOT_V1_2` كاستراتيجية مستقلة عن Weeg. لا تستدعي هذه الاستراتيجية أي endpoint لتنفيذ أوامر Binance؛ مصادرها هي بيانات السوق العامة و`exchangeInfo` و`bookTicker` فقط، وكل الصفقات المسجلة Paper Trading.

## وضع الوصول إلى الداشبورد

الداشبورد يعمل حاليًا بوضع القراءة العامة: فتح الرابط يعرض بيانات WEEG وIFVG دون تسجيل دخول أو إنشاء حساب. تشمل الإتاحة العامة حالات النظام، قائمة الأسعار، سجلات الصفقات، Summary Cycle، وأداء IFVG. لذلك يجب اعتبار رابط Render رابطًا عامًا وعدم مشاركته إذا كانت البيانات المعروضة حساسة.

مسارات الكتابة أو التعديل، مثل إنشاء صفقة ورقية يدويًا، تعديل صفقة، حفظ الإعدادات، والاشتراك في Push، تبقى محمية بالمصادقة. لا تُعرض مفاتيح Supabase أو Telegram أو أي أسرار في الداشبورد. إزالة شاشة الدخول لا تفتح تنفيذ أوامر حقيقية؛ النظام يظل Paper Trading فقط.

## التفعيل الآمن

التفعيل الافتراضي هو `IFVG_ENABLED=false`. قبل التفعيل يجب ضبط التخزين الدائم وحقول المحفظة التالية في بيئة الخادم: `IFVG_ENABLED=true`، و`IFVG_QUOTE_BALANCE`، و`IFVG_MAX_POSITION_VALUE_QUOTE`، و`IFVG_MAX_GLOBAL_OPEN_POSITIONS`. يمكن تحديد الأزواج عبر `IFVG_SYMBOLS`، وإلا تستخدم قائمة `SYMBOLS` العامة.

لا يكفي تفعيل العامل دون رصيد أو حدود محفظة؛ عند غياب أي من هذه القيم يبقى العامل في `WAITING_CONFIGURATION`. لا تُضع مفاتيح سرية في الواجهة أو في ملفات المستودع.

## العزل

تستخدم الاستراتيجية الجداول `ifvg_*` فقط، وتحتوي كل سجلاتها على `strategy_id=IFVG_SPOT_V1_2`. لا تعتمد لوحة IFVG أو API الخاص بها على `weeg_trades`، ولا تُضاف صفقات IFVG إلى ملخص Weeg القديم. كما أن إشعاراتها تحمل عنوانًا ووسمًا مستقلين، وأمر Telegram `/ifvg` يعرض ملخصها فقط.

## دورة القرار

تجمع الخدمة شموعًا مغلقة من `4h` و`1h` و`15m` و`5m`. تُستخدم الشمعة 5m المفتوحة الحالية، إن توفرت، لمرجع `next executable open` فقط، ولا تدخل في اكتشاف FVG أو inversion أو confirmation. فجوة داخل نافذة القرار تفشل مغلقًا. كل قرار يحفظ snapshot hash، نسخة الإعداد، البوابات، الحالة، وأحداث الانتقال.

## الواجهات والأزرار

تعرض `GET /api/ifvg/health` حالة العامل، و`GET /api/ifvg/decision/{symbol}` قرارًا تشخيصيًا لا ينشئ صفقة، و`GET /api/ifvg/setups` الإعدادات المحفوظة، و`GET /api/ifvg/trades` السجل العام، و`GET /api/ifvg/trades/{trade_id}/fills` سجل الـfills، و`GET /api/ifvg/summary` ملخصًا مستقلاً. كما توجد مسارات مستقلة للأزرار: `GET /api/ifvg/cycle/summary` لـSummary Cycle، و`GET /api/ifvg/trades/open` للمفتوحة، و`GET /api/ifvg/trades/closed` للمغلقة، و`GET /api/ifvg/performance` لأداء IFVG. endpoint `GET /api/ifvg/backtest/{symbol}?days=180` ينزل تاريخًا paginated ويعيد نتائج Backtest تحذيرية لا تُعد دليلًا على الربحية.

في Telegram تظهر أربعة أزرار منفصلة داخل قائمة البوت: `IFVG Summary Cycle`، و`IFVG صفقات مفتوحة`، و`IFVG صفقات مغلقة`، و`IFVG أداء النظام`. يمكن أيضًا إرسال `/ifvg` لفتح ملخص IFVG، بينما تظل أزرار Weeg القديمة مرتبطة بجداول Weeg فقط.

## مراقبة ما قبل التشغيل

يجب أولًا تنفيذ `pytest -q`، ثم فحص `/api/health` و`/api/ifvg/health`. يجب أن تكون قيمة `paper_only=true` وأن تكون `order_endpoints_enabled=false`. يجب التحقق من ظهور الجداول السبعة في Supabase ومن خلو Security Advisor من التنبيهات. عند وجود `WAITING_STORAGE` أو `WAITING_CONFIGURATION` لا تُعتبر الاستراتيجية عاملة.

## القيود المعروفة

هذا الدمج يحقق محرك القرار، دورة Paper Trading، التخزين، الحجز، fills، اللوحة، الإشعارات، والـBacktest. لا يدعي أن IFVG مربحة. نتائج Backtest تحتاج لاحقًا إلى تقسيم OOS/WFA وCSCV/Reality Check أو SPA، وتسجيل كل التجارب قبل استخدامها في أي تقييم.
