# نظام CT — FALCON للتداول الورقي الحي 🦅

تداول ورقي يحاكي الحقيقي: توصيات تيليغرام فورية + متابعة كل صفقة حتى الإغلاق (فوز/خسارة + السبب) + ملخص تشخيصي لكل دورة.

## المعمارية

```
Render (web service واحد)
├── حلقة التداول (كل 60 ثانية): مسح DAY + FALCON → فتح → إدارة → summary
└── Webhook تيليغرام: 5 أزرار (مفتوحة/مغلقة/أداء/أسعار/summary)
Supabase Postgres: الصفقات + الدورات + الأحداث + الحالة
Redis: أسعار حية + قفل الدورات (سقوط ناعم لذاكرة داخلية عند تعطله)
```

## النشر على Render (خطوات)

1. **قاعدة البيانات**: لا تحتاج خطوة يدوية — التطبيق يبني/يُصلح المخطط تلقائيًا عند الإقلاع
   (يطبّق ملفات `migrations/*.sql` غير المطبَّقة + يضمن أعمدة `trades` كلها، مع تتبّع ما طُبِّق في جدول `state`).
   لبناء قاعدة جديدة من الصفر (أو إصلاح شامل يدوي): نفّذ **`supabase_full_schema.sql`** كاملاً في Supabase SQL Editor —
   ملف واحد يبني كل الجداول والفهارس، آمن للتنفيذ المتكرر، ولا يمس البيانات الموجودة.
2. **خدمة جديدة**: New → Web Service → اربط مستودع `CT` (أو Blueprint عبر `render.yaml`).
   - Build: `pip install -r requirements.txt`
   - Start: `gunicorn app.main:app --workers 1 --bind 0.0.0.0:$PORT --timeout 180`
   - الخطة: **Starter** على الأقل (المجانية تنام وتوقف التداول!).
3. **متغيرات البيئة** (Environment):
   | المتغير | القيمة |
   |---|---|
   | `SUPABASE_URL` | رابط Postgres (pooler) |
   | `REDIS_URL` | رابط Redis |
   | `BOT_TOKEN` | توكن البوت من BotFather |
   | `ADMIN_CHAT_ID` | (اختياري) رقم شاتك — يُسجل تلقائياً عند أول `/start` |
   | `PUBLIC_URL` | رابط الخدمة `https://....onrender.com` (يُضبط الـ webhook تلقائياً عند الإقلاع) |
   | `SCAN_INTERVAL_SEC` | `60` |
   | `PAPER_EQUITY` | `10000` |
4. **البوت**: افتح بوتك في تيليغرام → `/start` → تصبح المدير تلقائياً → الأزرار تعمل.
5. **تحقق**: افتح `https://....onrender.com/health` — يجب أن ترى كل المحركات `ok`.

## الأزرار

| الزر | المحتوى |
|---|---|
| 📌 المفتوحة | المراكز + السعر الحالي + الربح/الخسارة اللحظية |
| 📋 المغلقة | آخر 10 (نتيجة + R + سبب الخروج) |
| 📊 الأداء | WR/PF/الصافي لكل نظام + مقارنة مع الباك تست |
| 💰 الأسعار | أسعار الـ 20 عملة من Redis |
| 🔄 Summary | آخر دورة: المدة، الممسوح، أسباب الرفض، نبض المحركات، الأخطاء |

## ملاحظات أمان

- الأسرار في متغيرات بيئة Render فقط — لا تُكتب في الكود أبداً.
- البوت خاص بالمدير فقط (أول من يرسل `/start`).
- الإيقاف (3% يومي / 10% كلي) يمنع **الفتح الجديد** فقط — إدارة المراكز المفتوحة تستمر دائماً.
- بعد انتهاء الإعداد: **دوّر (غيّر) كل الأسرار** التي شاركتها في أي محادثة.

## استكشاف الأخطاء

**❌ `column "signal_key" of relation "trades" does not exist` (وإشارات تضيع بصمت)**

- **السبب**: قاعدة Supabase الحية أقدم من الكود — جدول `trades` أُنشئ من مخطط قديم
  قبل إضافة `signal_key`، ومايجريشن `002_trade_dedup.sql` لم يُطبَّق (لم يكن يوجد
  أي آلية تطبّق المايجريشنز تلقائياً). كل إشارة تُكتشف ثم تفشل عند الإدراج
  (`FALCON-XXX: column "signal_key" ...`) ولا تُفتح صفقة.
- **الإصلاح الجذري (في الكود)**: التطبيق الآن "شفاء ذاتي" — عند كل إقلاع يطبّق
  `db.ensure_schema()` المايجريشنز الناقصة + يضمن أعمدة/فهارس `trades` كلها،
  وعند أي `UndefinedColumn` أثناء الإدراج يُصلح المخطط ويعيد المحاولة مرة واحدة.
  راقب `/health` — يجب أن ترى `"schema": "ok"`.
- **إصلاح فوري بدون إعادة نشر**: نفّذ في Supabase SQL Editor:
  ```sql
  ALTER TABLE trades ADD COLUMN IF NOT EXISTS signal_key TEXT NOT NULL DEFAULT '';
  CREATE UNIQUE INDEX IF NOT EXISTS uq_trades_open_day_symbol
      ON trades (system, symbol) WHERE status = 'OPEN' AND system = 'DAY';
  CREATE UNIQUE INDEX IF NOT EXISTS uq_trades_signal_leg
      ON trades (system, symbol, signal_key, leg) WHERE signal_key <> '';
  ```
  (هذا محتوى `migrations/002_trade_dedup.sql` حرفياً.)

**قاعدة عامة**: عند إضافة أعمدة جديدة مستقبلاً — أضف مايجريشن `00N_xxx.sql`
(idempotent: `IF NOT EXISTS`) وسيتطبَّق تلقائياً عند النشر التالي.
