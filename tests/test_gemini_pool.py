import pytest

from app.brain.gemini import GeminiKeyPool
from app.config.settings import get_settings
from app.state import RuntimeState


@pytest.mark.asyncio
async def test_gemini_rotates_after_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY_1", "key-one")
    monkeypatch.setenv("GEMINI_API_KEY_2", "key-two")
    get_settings.cache_clear()
    state = RuntimeState()
    pool = GeminiKeyPool(state)

    async def fake_request(key: str, prompt: str, system_instruction: str) -> str:
        if key == "key-one":
            raise RuntimeError("rate limited")
        return "{\"action\":\"WAIT\"}"

    monkeypatch.setattr(pool, "_request", fake_request)
    result = await pool.analyze("test", "safe")

    assert result["ok"] is True
    assert result["account_index"] == 2
    assert state.gemini_usage["rotations_total"] == 1
    assert state.gemini_usage["failures_total"] == 1

    get_settings.cache_clear()


def test_gemini_configuration_is_limited_to_five(monkeypatch: pytest.MonkeyPatch) -> None:
    for index in range(1, 6):
        monkeypatch.setenv(f"GEMINI_API_KEY_{index}", f"key-{index}")
    get_settings.cache_clear()
    assert len(get_settings().gemini_keys) == 5
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_probe_all_checks_each_configured_key_independently(monkeypatch: pytest.MonkeyPatch) -> None:
    for index in range(1, 6):
        monkeypatch.setenv(f"GEMINI_API_KEY_{index}", f"key-{index}")
    get_settings.cache_clear()
    state = RuntimeState()
    pool = GeminiKeyPool(state)
    checked = []

    async def fake_request(key: str, prompt: str, system_instruction: str) -> str:
        checked.append(key)
        if key in {"key-2", "key-4"}:
            raise RuntimeError("404 NOT_FOUND model unavailable")
        return '{"probe":"ok"}'

    monkeypatch.setattr(pool, "_request", fake_request)
    result = await pool.probe_all()

    assert checked == ["key-1", "key-2", "key-3", "key-4", "key-5"]
    assert result["checked"] == 5
    assert result["successful"] == 3
    assert result["failed"] == 2
    assert result["results"][1]["error_category"] == "MODEL_UNAVAILABLE"
    assert state.gemini_usage["keys"][1]["diagnostic_failures"] == 1
    get_settings.cache_clear()


def test_gemini_error_categories() -> None:
    assert GeminiKeyPool._classify_error(RuntimeError("429 RESOURCE_EXHAUSTED")) == "QUOTA_OR_RATE_LIMIT"
    assert GeminiKeyPool._classify_error(RuntimeError("404 NOT_FOUND model unavailable")) == "MODEL_UNAVAILABLE"
    assert GeminiKeyPool._classify_error(RuntimeError("401 UNAUTHENTICATED")) == "KEY_OR_PERMISSION"
