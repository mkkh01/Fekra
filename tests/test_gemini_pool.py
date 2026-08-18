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
