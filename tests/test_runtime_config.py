from app.config.settings import Settings


def test_redis_cli_value_is_normalized() -> None:
    settings = Settings(redis_url="redis-cli -u redis://default:secret@example.com:6379")
    assert settings.redis_url == "redis://default:secret@example.com:6379"


def test_quoted_redis_url_is_normalized() -> None:
    settings = Settings(redis_url='"rediss://default:secret@example.com:6380"')
    assert settings.redis_url == "rediss://default:secret@example.com:6380"
