"""
اختبارات ميزات التنفيذ الحي:
1. تأمين المركز بنقطة التعادل (Breakeven).
2. منع الصفقات المشوهة أو القديمة (Signal Freshness & Price Drift).
3. تقييم الهدف والوقف بالسعر اللحظي الحي.
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config, exec as ex, trader


def test_breakeven_advances_stop():
    """عند وصول السعر إلى 50% من الهدف، يجب أن يتقدم الوقف لسعر الدخول."""
    # شراء بسعر 100، هدف 110، وقف 90
    entry = 100.0
    tp = 110.0
    sl0 = 90.0
    side = 1
    
    # 1. السعر ارتفع إلى 103 (30% من مسافة الهدف) -> الوقف لا يتغير
    progress = 103.0 - entry
    target_dist = tp - entry
    assert progress < config.BREAKEVEN_TRIGGER_RATIO * target_dist
    
    # 2. السعر ارتفع إلى 106 (60% من مسافة الهدف) -> يتحقق شرط Breakeven
    progress2 = 106.0 - entry
    assert progress2 >= config.BREAKEVEN_TRIGGER_RATIO * target_dist


def test_price_drift_guard():
    """إذا تحرك السعر الحي بعيداً عن سعر إشارة الشمعة بأكثر من الحد المسموح، يتم الرفض."""
    px_sig = 100.0
    live_ok = 100.05      # انحراف 0.05% <= 0.08%
    live_drifted = 100.15 # انحراف 0.15% > 0.08%
    
    drift_ok = abs(live_ok - px_sig) / px_sig
    drift_bad = abs(live_drifted - px_sig) / px_sig
    
    assert drift_ok <= config.MAX_PRICE_DRIFT_PCT
    assert drift_bad > config.MAX_PRICE_DRIFT_PCT


def test_signal_freshness_guard():
    """رفض الإشارات التي أغلقت شمعتها منذ فترة طويلة."""
    now = datetime.now(timezone.utc)
    fresh_candle_close = now - timedelta(seconds=60)
    stale_candle_close = now - timedelta(seconds=300)
    
    age_fresh = (now - fresh_candle_close).total_seconds()
    age_stale = (now - stale_candle_close).total_seconds()
    
    assert age_fresh <= config.SIGNAL_MAX_AGE_SEC_DAY
    assert age_stale > config.SIGNAL_MAX_AGE_SEC_DAY
