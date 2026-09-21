"""طبقة Supabase Postgres — اتصالات قصيرة (صديقة للـ pooler)."""
import contextlib
import json
from pathlib import Path

import psycopg2
import psycopg2.extras

from . import config

# مايجريشنز المخطط — تُطبَّق تلقائيًا عند الإقلاع (انظر ensure_schema)
MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"
STATE_KEY_MIGRATIONS = "schema_migrations"
_schema_healed = False  # حتى لا نعيد محاولة الإصلاح الذاتي في كل دورة


@contextlib.contextmanager
def conn():
    c = psycopg2.connect(config.DATABASE_URL, connect_timeout=10)
    try:
        c.autocommit = True
        yield c
    finally:
        c.close()


def q_all(sql, params=()):
    with conn() as c, c.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]


def q_one(sql, params=()):
    rows = q_all(sql, params)
    return rows[0] if rows else None


def exec(sql, params=()):
    with conn() as c, c.cursor() as cur:
        cur.execute(sql, params)
        return cur.rowcount


def health():
    r = q_one("SELECT 1 AS ok")
    return bool(r and r["ok"] == 1)


# ── المخطط: مايجريشنز تلقائية + فحص سلامة ──
REQUIRED_TRADE_COLS = {
    "system", "symbol", "side", "leg", "entry", "qty", "tp", "sl",
    "trail_atr", "atr0", "hold_hours", "day", "reason_ar", "status",
    "signal_key", "exit_time", "exit_price", "reason", "net", "r", "fee",
}

# أعمدة/فهارس trades التي يكتبها الكود — ALTER صريح يشفي أي جدول قديم مهما كان
# شكله (CREATE TABLE IF NOT EXISTS لا يضيف أعمدة لجدول موجود، لذا نحتاج هذه القائمة)
CORE_TRADES_ALTER = [
    "ALTER TABLE trades ADD COLUMN IF NOT EXISTS leg TEXT DEFAULT ''",
    "ALTER TABLE trades ADD COLUMN IF NOT EXISTS tp DOUBLE PRECISION DEFAULT 0",
    "ALTER TABLE trades ADD COLUMN IF NOT EXISTS sl DOUBLE PRECISION DEFAULT 0",
    "ALTER TABLE trades ADD COLUMN IF NOT EXISTS trail_atr DOUBLE PRECISION DEFAULT 0",
    "ALTER TABLE trades ADD COLUMN IF NOT EXISTS atr0 DOUBLE PRECISION DEFAULT 0",
    "ALTER TABLE trades ADD COLUMN IF NOT EXISTS hold_hours DOUBLE PRECISION DEFAULT 24",
    "ALTER TABLE trades ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'OPEN'",
    "ALTER TABLE trades ADD COLUMN IF NOT EXISTS exit_time TIMESTAMPTZ",
    "ALTER TABLE trades ADD COLUMN IF NOT EXISTS exit_price DOUBLE PRECISION",
    "ALTER TABLE trades ADD COLUMN IF NOT EXISTS reason TEXT",
    "ALTER TABLE trades ADD COLUMN IF NOT EXISTS net DOUBLE PRECISION",
    "ALTER TABLE trades ADD COLUMN IF NOT EXISTS r DOUBLE PRECISION",
    "ALTER TABLE trades ADD COLUMN IF NOT EXISTS fee DOUBLE PRECISION",
    "ALTER TABLE trades ADD COLUMN IF NOT EXISTS day DATE",
    "ALTER TABLE trades ADD COLUMN IF NOT EXISTS reason_ar TEXT DEFAULT ''",
    "ALTER TABLE trades ADD COLUMN IF NOT EXISTS signal_key TEXT NOT NULL DEFAULT ''",
    "CREATE INDEX IF NOT EXISTS idx_trades_status ON trades(status)",
    "CREATE INDEX IF NOT EXISTS idx_trades_day ON trades(day)",
]


def schema_ok():
    """هل يملك جدول trades كل الأعمدة التي يكتبها الكود؟"""
    try:
        rows = q_all("SELECT column_name FROM information_schema.columns "
                     "WHERE table_name='trades'")
        names = {r["column_name"] for r in rows}
        return REQUIRED_TRADE_COLS <= names
    except Exception:
        return False


def _split_sql_statements(sql):
    """تقسيم ملف SQL بسيط إلى عبارات — يتجاهل التعليقات ولا ينقسم داخل النصوص."""
    out, buf, in_str = [], [], False
    i, n = 0, len(sql)
    while i < n:
        ch = sql[i]
        if in_str:
            buf.append(ch)
            if ch == "'":
                in_str = False
        elif ch == "'":
            buf.append(ch)
            in_str = True
        elif ch == "-" and sql[i:i + 2] == "--":
            while i < n and sql[i] != "\n":
                i += 1
            continue
        elif ch == ";":
            stmt = "".join(buf).strip()
            if stmt:
                out.append(stmt)
            buf = []
        else:
            buf.append(ch)
        i += 1
    stmt = "".join(buf).strip()
    if stmt:
        out.append(stmt)
    return out


def ensure_schema():
    """يبني/يُصلح مخطط القاعدة تلقائيًا ليطابق الكود.

    1) يطبّق ملفات migrations/*.sql غير المطبَّقة بعد (كلها idempotent) —
       العبارات تُنفَّذ واحدة واحدة حتى لا يمنع فشل عبارة (مثل فهرس فريد فوق
       بيانات مكررة) بقية الإصلاحات، ويُعلَّم الملف مطبَّقًا في جدول state
       فقط إذا نجحت كل عباراته.
    2) ثم يشغّل ALTER صريحًا لكل عمود يكتبه الكود — يشفي جدول trades القديم
       مهما كان شكله (مثل غياب signal_key الذي أوقف فتح الصفقات).
    3) يعيد الملفات التي فشلت بعد شفاء الأعمدة (تبعيات ترتيب: فهرس في الملف
       على عمود يُضاف لاحقًا).
    يعيد قائمة أخطاء فارغة عند النجاح الكامل.
    """
    applied = set()
    try:
        applied = set(json.loads(get_state(STATE_KEY_MIGRATIONS, "[]")))
    except Exception:
        applied = set()

    def _apply(path):
        errs = []
        try:
            stmts = _split_sql_statements(path.read_text(encoding="utf-8"))
        except Exception as e:
            return [str(e)]
        with conn() as c, c.cursor() as cur:
            for stmt in stmts:
                try:
                    cur.execute(stmt)
                except Exception as e:
                    errs.append(str(e).strip().splitlines()[0])
                    try:
                        log_event("ERR", f"migration {path.name}: {e}")
                    except Exception:
                        pass
        return errs

    def _mark(path):
        applied.add(path.name)
        set_state(STATE_KEY_MIGRATIONS, json.dumps(sorted(applied)))
        print(f"[schema] applied {path.name}", flush=True)

    def _core_alters():
        errs = []
        try:
            with conn() as c, c.cursor() as cur:
                for stmt in CORE_TRADES_ALTER:
                    try:
                        cur.execute(stmt)
                    except Exception as e:
                        errs.append(f"core-alter: {str(e).strip().splitlines()[0]}")
        except Exception as e:
            errs.append(f"core-alter: {e}")
        return errs

    errors = []
    failed = []
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        if path.name in applied:
            continue
        ferr = _apply(path)
        if ferr:
            failed.append(path)
            errors.append(f"{path.name}: " + " | ".join(ferr[:3]))
        else:
            _mark(path)
    if failed:
        # شفاء الأعمدة الناقصة ثم جولة أخيرة على الملفات التي فشلت
        _core_alters()
        retry_errors = []
        for path in failed:
            ferr = _apply(path)
            if ferr:
                retry_errors.append(f"{path.name}: " + " | ".join(ferr[:3]))
            else:
                _mark(path)
        errors = retry_errors  # أخطاء الجولة الأولى أصبحت مهملة إن نجحت الإعادة
    # الخطوة الشافية النهائية: تأكيد كل أعمدة/فهارس trades مهما كانت الحالة
    errors.extend(_core_alters())
    if not errors and not schema_ok():
        errors.append("schema still incomplete after ensure")
    return errors


# ── الصفقات ──
def open_trade(system, symbol, side, entry, qty, tp, sl, leg="", trail_atr=0.0,
               atr0=0.0, hold_hours=24.0, day=None, reason_ar="", signal_key=""):
    global _schema_healed
    sql = """INSERT INTO trades (system,symbol,side,entry,qty,tp,sl,leg,trail_atr,atr0,
                                 hold_hours,day,reason_ar,status,signal_key)
             VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'OPEN',%s)
             RETURNING id"""
    params = (system, symbol, side, entry, qty, tp or 0, sl, leg, trail_atr, atr0,
              hold_hours, day, reason_ar, signal_key)
    try:
        r = q_one(sql, params)
    except psycopg2.errors.UndefinedColumn as e:
        # انجراف المخطط: قاعدة حية أقدم من الكود (مثل غياب signal_key) —
        # أعد بناء/إصلاح المخطط مرة واحدة ثم أعد المحاولة إن اكتملت الأعمدة.
        if _schema_healed:
            raise
        _schema_healed = True
        try:
            log_event("ERR", f"schema drift on insert: {str(e)[:150]} — re-applying migrations")
        except Exception:
            pass
        errs = ensure_schema()
        if errs and not schema_ok():
            raise
        r = q_one(sql, params)
    except psycopg2.errors.UniqueViolation:
        # Race-safe deduplication: another cycle already inserted this position.
        return None
    return r["id"] if r else None


def open_positions(system=None):
    if system:
        return q_all("SELECT * FROM trades WHERE status='OPEN' AND system=%s ORDER BY id", (system,))
    return q_all("SELECT * FROM trades WHERE status='OPEN' ORDER BY id")


def close_trade(tid, exit_price, reason, net, r, fee):
    exec("""UPDATE trades SET status='CLOSED', exit_time=now(), exit_price=%s,
            reason=%s, net=%s, r=%s, fee=%s WHERE id=%s""",
         (exit_price, reason, net, r, fee, tid))


def recent_closed(limit=10):
    return q_all("SELECT * FROM trades WHERE status='CLOSED' ORDER BY id DESC LIMIT %s", (limit,))


def get_stats(system=None):
    where = "status='CLOSED'" + (" AND system=%s" if system else "")
    p = (system,) if system else ()
    rows = q_all("SELECT net FROM trades WHERE " + where, p)
    n = len(rows)
    if n == 0:
        return dict(n=0, wins=0, losses=0, wr=0.0, pf=0.0, net=0.0)
    wins = [x["net"] for x in rows if x["net"] > 0]
    loss = [-x["net"] for x in rows if x["net"] <= 0]
    g, l = sum(wins), sum(loss)
    return dict(n=n, wins=len(wins), losses=len(loss), wr=round(len(wins) / n * 100, 1),
                pf=round(g / l, 2) if l > 0 else 0.0,
                net=round(sum(x["net"] for x in rows), 2))


# ── حدود اليوم ──
def day_count(day, system, symbol):
    r = q_one("SELECT n FROM day_counts WHERE day=%s AND system=%s AND symbol=%s",
              (day, system, symbol))
    return r["n"] if r else 0


def bump_day(day, system, symbol):
    exec("""INSERT INTO day_counts (day,system,symbol,n) VALUES (%s,%s,%s,1)
            ON CONFLICT (day,system,symbol) DO UPDATE SET n = day_counts.n + 1""",
         (day, system, symbol))


# ── الرصيد ──
def realized_equity():
    r = q_one("SELECT COALESCE(SUM(net),0) AS s FROM trades WHERE status='CLOSED'")
    return config.PAPER_EQUITY + float(r["s"] or 0)


def mark_equity(equity):
    exec("INSERT INTO equity_marks (equity) VALUES (%s)", (equity,))
    r = q_one("SELECT MAX(equity) AS peak FROM equity_marks")
    return float(r["peak"]) if r and r["peak"] else equity


# ── الأحداث ──
def log_event(level, msg):
    try:
        exec("INSERT INTO events (level,msg) VALUES (%s,%s)", (level, msg[:2000]))
    except Exception:
        pass


# ── الدورات ──
def save_cycle(c):
    exec("""INSERT INTO ct_cycles (started_at,ended_at,duration_ms,scanned_day,scanned_falcon,
            reject_day,reject_falcon,signals,opened,closed,health,errors,equity,note)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
         (c["started_at"], c["ended_at"], c["duration_ms"], c["scanned_day"],
          c["scanned_falcon"], json.dumps(c["reject_day"]), json.dumps(c["reject_falcon"]),
          c["signals"], c["opened"], c["closed"], json.dumps(c["health"]),
          json.dumps(c["errors"]), c["equity"], c.get("note", "")))


def last_cycle():
    return q_one("SELECT * FROM ct_cycles ORDER BY id DESC LIMIT 1")


def recent_errors(n=5):
    return q_all("SELECT ts, msg FROM events WHERE level='ERR' ORDER BY id DESC LIMIT %s", (n,))


def system_totals(today):
    row = q_one("""SELECT
        (SELECT COUNT(*) FROM ct_cycles) AS cycles,
        (SELECT COUNT(*) FROM trades WHERE status='OPEN') AS open_n,
        (SELECT COUNT(*) FROM trades WHERE day=%s) AS today_n,
        (SELECT COUNT(*) FROM trades WHERE status='CLOSED') AS closed_n""", (today,))
    return dict(cycles=row["cycles"], open_n=row["open_n"],
                today_n=row["today_n"], closed_n=row["closed_n"])


# ── حالة عامة ──
def get_state(key, default=None):
    r = q_one("SELECT value FROM state WHERE key=%s", (key,))
    return r["value"] if r else default


def set_state(key, value):
    exec("INSERT INTO state (key,value) VALUES (%s,%s) "
         "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value", (key, str(value)))
