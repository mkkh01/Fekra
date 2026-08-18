from app.storage.store import StorageManager


def test_permission_403_disables_supabase_writes() -> None:
    storage = StorageManager.__new__(StorageManager)
    storage.supabase_write_enabled = True
    storage._supabase_auth_error_logged = False

    storage._mark_supabase_failure(Exception("403 permission denied for table news"), "news write")

    assert storage.supabase_write_enabled is False
    assert storage._supabase_auth_error_logged is True
