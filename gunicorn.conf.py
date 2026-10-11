"""
تكوين Gunicorn لمنظومة CT-FALCON.
تشغيل الخيوط الخلفية (المجدول وحلقة المراقبة) يتم بأمان داخل العامل بعد الـ fork
(post_worker_init) لتفادي انهيار العملية الرئيسية بخطأ SIGSEGV (Status 139).
"""
import os

bind = f"0.0.0.0:{os.environ.get('PORT', '10000')}"
workers = int(os.environ.get("WEB_CONCURRENCY", "1"))
timeout = 180
accesslog = "-"
errorlog = "-"


def post_worker_init(worker):
    """
    تُستدعى فور تهيئة العامل وبعد استقرار العملية المتفرعة (child process).
    هنا يبدأ المجدول وحلقة المراقبة دون التأثير على عملية Gunicorn Master.
    """
    from app.main import boot
    boot()
