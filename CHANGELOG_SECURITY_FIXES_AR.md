# ملخص إصلاحات Fekra

## الحالة

تم تنفيذ الإصلاحات ورفعها إلى GitHub في commit:

`65ff8d798853e381eda82aa05656c26e47b92e77`

الرابط: https://github.com/mkkh01/Fekra/commit/65ff8d798853e381eda82aa05656c26e47b92e77

## ما تم إصلاحه

تمت إضافة مصادقة Supabase Auth إلى الواجهة وFastAPI، مع حماية مسارات الصفقات والإعدادات والـ Shadow والـ health التفصيلي، وإضافة تسجيل الدخول والخروج وإنشاء الحساب وتمرير Bearer token.

تمت إضافة ملكية المستخدم للصفقات اليدوية والإعدادات واشتراكات Push، مع إبقاء الصفقات الآلية العامة مرئية للمستخدمين المصادقين ومنع تعديل صفقة مستخدم آخر. كما أضيفت اختبارات ملكية متعددة المستخدمين.

تم تطبيق migrations `009_multitenant_rls` و`010_cleanup_redundant_indexes` و`011_lockdown_aux_tables_and_fk_indexes` على Supabase الحي. أصبح مستشار الأمان في Supabase يعيد صفر تنبيهات، كما عولجت تنبيهات المفاتيح الأجنبية غير المفهرسة. تبقى بعض ملاحظات `unused_index` الإعلامية القديمة، وهي تحسينات workload وليست ثغرات أمنية.

تم منع fallback الصامت إلى SQLite عند تهيئة التخزين الدائم، وتوحيد تقييم Shadow Mode مع حلقة الخروج كل 5 ثوانٍ، وإنهاء سجلات Shadow القديمة بحالة `EXPIRED`، وتطبيق توافق MTF في backtest.

## التحقق

نجحت اختبارات Python وعددها 35 اختبارًا، ونجح فحص Python syntax وفحص JavaScript syntax و`git diff --check`. كما نجح `/api/healthz` العام، بينما يعيد `/api/health` و`/api/trades` حالة 401 بلا جلسة.

## مطلوب قبل التشغيل على Render

أضف إلى متغيرات Render المحمية:

```text
SUPABASE_SERVICE_ROLE_KEY=<service-role-key>
SUPABASE_PUBLISHABLE_KEY=<publishable-key>
CORS_ORIGINS=https://<your-render-domain>
```

لا تضع `SUPABASE_SERVICE_ROLE_KEY` في المتصفح. يجب إنشاء/تفعيل تسجيل المستخدمين في Supabase Auth قبل الدخول إلى اللوحة. لا تزال المنظومة **Paper Trading فقط**، ولم يتم تفعيل تداول حقيقي أو تنفيذ أوامر في منصة خارجية.
