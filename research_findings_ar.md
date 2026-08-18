# نتائج التحقق الخارجي — 18 أغسطس 2026

## Gemini

صفحة نماذج Gemini الرسمية تعرض عائلة Gemini 3، وتظهر نماذج Flash مستقرة، مع توجيه التوثيق إلى أحدث نماذجها حسب التوافر الحالي. صفحة Function Calling الرسمية تعرض نموذج `gemini-3.7-flash` في أمثلة Interactions API، وتوضح أن النموذج لا ينفذ الدالة بنفسه؛ التطبيق يستخرج اسم الدالة والمعاملات وينفذها ثم يعيد النتيجة إلى النموذج. هذا مناسب لتصميم Trading Brain لأن التطبيق سيبقى صاحب القرار التنفيذي والتحقق والمخاطر، بينما Gemini يقترح الأدوات والتحليل فقط.

المصدران:
- https://ai.google.dev/gemini-api/docs/models
- https://ai.google.dev/gemini-api/docs/function-calling

## قرار أولي

سنستخدم اسم النموذج من متغير بيئة `GEMINI_MODEL` بدل تثبيته داخل الكود. القيمة الافتراضية المبدئية ستكون `gemini-3.7-flash` إذا كان متاحًا في حساب المستخدم، مع إمكانية الرجوع إلى `gemini-2.5-flash` عند عدم توفر النموذج أو اختلاف حدود الحساب. لن نربط أي Function Calling بتنفيذ أوامر حقيقية؛ كل الأدوات في PAPER/OBSERVE ستعيد بيانات أو محاكاة، ويجري التحقق من عقد القرار داخل الخادم.

## Render

توثيق Render يذكر أن Web Service يجب أن يستمع على `0.0.0.0` وأن يستخدم المنفذ الموجود في متغير `PORT`، مع قيمة افتراضية `10000`. كما أن Start Command هو الأمر الذي يشغّل الخدمة بعد البناء، ويدعم Render خدمات FastAPI وWebSockets. أمر التشغيل المناسب في هذا المشروع سيكون `python main.py`، بشرط أن يقوم `main.py` بتشغيل Uvicorn على `0.0.0.0:$PORT`.

المصادر:
- https://render.com/docs/web-services
- https://render.com/docs/your-first-deploy

## الأخبار المجانية

سنستخدم RSS مباشرًا من مصادر منشورة دون اشتراك API مدفوع. تم التحقق من أن Crypto Briefing ينشر خلاصات RSS حسب الموضوع، وأن CCN ينشر خلاصات مباشرة للأخبار والتحليل. سنضيف أيضًا مصادر RSS أخرى قابلة للضبط، مع إزالة التكرار وتسجيل المصدر والوقت والرابط، وعدم اعتبار غياب الخبر دليلًا على عدم وجوده.

المصادر:
- https://www.cryptobriefing.com/feeds/
- https://www.ccn.com/rss-feeds/

## Supabase security verification

بعد تطبيق ترحيلات الأمان، استعلام `information_schema.routine_privileges` أظهر أن `public.rls_auto_enable()` لديها صلاحية EXECUTE للمستخدم `postgres` فقط. ما يزال مستشار Supabase يعرض التحذير القديم عن anon/authenticated، لذلك يُعامل كاحتمال cache في advisor، بينما النتيجة المباشرة للصلاحيات هي المرجع العملي. أما ملاحظات `rls_enabled_no_policy` فهي INFO متوقعة لأن الجداول محمية ولا توجد سياسة عامة؛ التطبيق يعتمد على خادم server-side وليس وصولًا مباشرًا من المتصفح.
