from __future__ import annotations

import main


def _route(path: str, method: str = "GET"):
    for route in main.app.routes:
        if route.path == path and method in getattr(route, "methods", set()):
            return route
    raise AssertionError(f"route not found: {method} {path}")


def test_public_dashboard_read_routes_do_not_require_auth():
    public_paths = [
        "/api/health",
        "/api/summary/cycle/state",
        "/api/summary/cycle",
        "/api/trades",
        "/api/ifvg/health",
        "/api/ifvg/trades",
        "/api/ifvg/trades/open",
        "/api/ifvg/trades/closed",
        "/api/ifvg/performance",
        "/api/ifvg/cycle/summary",
    ]
    for path in public_paths:
        assert _route(path).dependant.dependencies == [], path


def test_mutating_trade_route_remains_authenticated():
    route = _route("/api/trades/paper", "POST")
    assert route.dependant.dependencies, "paper trade creation must remain protected"
