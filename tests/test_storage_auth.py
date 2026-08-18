from app.config.settings import Settings
from app.storage.store import StorageManager


def test_service_role_key_detection() -> None:
    assert StorageManager._looks_like_server_key("sb_secret_example") is True
    assert StorageManager._looks_like_server_key("anon-public-key") is False
    assert StorageManager._looks_like_server_key("eyJnot-a-valid-jwt") is False


def test_dedicated_service_role_key_is_preferred() -> None:
    settings = Settings(
        supabase_url="https://example.supabase.co",
        supabase_key="anon-key",
        supabase_service_role_key="sb_secret_server-key",
    )
    assert settings.supabase_server_key == "sb_secret_server-key"
