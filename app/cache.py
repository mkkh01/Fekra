"""الكاش: Redis مع سقوط ناعم لذاكرة داخلية (النظام لا يتوقف أبداً)."""
import time
from . import config

try:
    import redis as _redis
except Exception:
    _redis = None


class Cache:
    def __init__(self, url):
        self.r = None
        self.mem = {}
        self.mode = "memory"
        if url and _redis:
            try:
                r = _redis.from_url(url, socket_timeout=5, socket_connect_timeout=5)
                r.ping()
                self.r = r
                self.mode = "redis"
            except Exception:
                self.r = None

    def ping(self):
        if self.r:
            try:
                return bool(self.r.ping())
            except Exception:
                return False
        return True

    def get(self, k):
        if self.r:
            try:
                v = self.r.get(k)
                return v.decode() if isinstance(v, bytes) else v
            except Exception:
                pass
        v = self.mem.get(k)
        if v and (v[1] is None or v[1] > time.time()):
            return v[0]
        return None

    def set(self, k, v, ex=None):
        if self.r:
            try:
                self.r.set(k, v, ex=ex)
                return
            except Exception:
                pass
        self.mem[k] = (v, time.time() + ex if ex else None)

    def acquire(self, name, ttl=120):
        """قفل لمنع تداخل الدورات."""
        if self.r:
            try:
                return bool(self.r.set(name, "1", nx=True, ex=ttl))
            except Exception:
                pass
        now = time.time()
        cur = self.mem.get(name)
        if cur and cur[1] and cur[1] > now:
            return False
        self.mem[name] = ("1", now + ttl)
        return True

    def release(self, name):
        try:
            if self.r:
                self.r.delete(name)
        except Exception:
            pass
        self.mem.pop(name, None)


cache = Cache(config.REDIS_URL)
