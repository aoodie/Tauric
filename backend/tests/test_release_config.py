from sqlalchemy.engine import make_url

from tauric.config import RuntimeConfig


def test_database_password_file_is_encoded_without_changing_legacy_url(tmp_path, monkeypatch):
    monkeypatch.delenv("TAURIC_DB_PASSWORD_FILE", raising=False)
    original = "postgresql+psycopg://legacy:password@localhost/example"
    assert RuntimeConfig(database_url=original).database_url == original
    private = tmp_path / "password"
    private.write_text("pass:@/#?with-specials\n")
    config = RuntimeConfig(db_password_file=str(private), db_user="tauric_app")
    url = make_url(config.database_url)
    assert url.password == "pass:@/#?with-specials"
    assert url.username == "tauric_app"
    assert url.host == "db" and url.database == "tauric"
