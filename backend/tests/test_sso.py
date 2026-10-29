"""Exercise the real OIDC token checks and the owner-session boundary."""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from pydantic import ValidationError

from tauric import sso
from tauric.config import RuntimeConfig, runtime_config

ISSUER = "https://sso.example.test/application/o/tauric/"
CLIENT = "tauric-test-client"
SUBJECT = "owner-stable-subject"
CALLBACK = "https://testserver/api/v1/sso/callback"
METADATA = {
    "issuer": ISSUER,
    "authorization_endpoint": "https://sso.example.test/application/o/authorize/",
    "token_endpoint": "https://sso.example.test/application/o/token/",
    "jwks_uri": "https://sso.example.test/application/o/tauric/jwks/",
}


@pytest.fixture
def signing_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def token(key, **changes):
    current = datetime.now(UTC)
    claims = {
        "iss": ISSUER,
        "sub": SUBJECT,
        "aud": CLIENT,
        "iat": current,
        "exp": current + timedelta(minutes=2),
        "nonce": "expected-nonce",
    } | changes
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": "test-key"})


def install_public_key(monkeypatch, signing_key):
    public = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(signing_key.public_key()))
    public.update({"kid": "test-key", "use": "sig", "alg": "RS256"})
    monkeypatch.setattr(sso, "_json_request", lambda *_args, **_kw: {"keys": [public]})


def config():
    return SimpleNamespace(sso_issuer=ISSUER, sso_client_id=CLIENT, sso_subject=SUBJECT)


def test_token_verification_requires_owner_and_all_oidc_bindings(monkeypatch, signing_key):
    install_public_key(monkeypatch, signing_key)
    assert sso._verify_id_token(token(signing_key), METADATA, config(), "expected-nonce") == SUBJECT
    for changes in (
        {"sub": "another-user"},
        {"iss": "https://another.example/"},
        {"aud": "another-client"},
        {"nonce": "another-nonce"},
        {"exp": datetime.now(UTC) - timedelta(minutes=2)},
        {"aud": [CLIENT, "another-client"], "azp": "another-client"},
    ):
        with pytest.raises(ValueError):
            sso._verify_id_token(
                token(signing_key, **changes), METADATA, config(), "expected-nonce"
            )
    wrong_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(ValueError):
        sso._verify_id_token(token(wrong_key), METADATA, config(), "expected-nonce")
    weak_key = rsa.generate_private_key(public_exponent=65537, key_size=1024)
    install_public_key(monkeypatch, weak_key)
    with pytest.raises(ValueError):
        sso._verify_id_token(token(weak_key), METADATA, config(), "expected-nonce")


def test_metadata_cannot_redirect_token_or_keys_to_another_host(monkeypatch):
    for field in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
        document = METADATA | {field: "https://attacker.example/path"}
        monkeypatch.setattr(sso, "_json_request", lambda *_args, value=document: value)
        with pytest.raises(ValueError):
            sso._metadata(config())


def test_sso_configuration_requires_exact_secure_origin():
    settings = {
        "sso_issuer": ISSUER,
        "sso_client_id": CLIENT,
        "sso_subject": SUBJECT,
        "sso_callback_uri": CALLBACK,
        "allowed_origins": ["https://testserver"],
        "secure_cookies": True,
    }
    assert RuntimeConfig(**settings).sso_issuer == ISSUER
    for change in (
        {"secure_cookies": False},
        {"sso_subject": ""},
        {"allowed_origins": ["https://other.example"]},
        {"sso_issuer": "http://sso.example.test/"},
        {"sso_callback_uri": "https://testserver/untrusted"},
    ):
        with pytest.raises(ValidationError):
            RuntimeConfig(**(settings | change))


def test_complete_sso_flow_creates_existing_owner_session(database, monkeypatch, signing_key):
    from tauric.app import app

    monkeypatch.setenv("TAURIC_SSO_ISSUER", ISSUER)
    monkeypatch.setenv("TAURIC_SSO_CLIENT_ID", CLIENT)
    monkeypatch.setenv("TAURIC_SSO_SUBJECT", SUBJECT)
    monkeypatch.setenv("TAURIC_SSO_CALLBACK_URI", CALLBACK)
    monkeypatch.setenv("TAURIC_ALLOWED_ORIGINS", '["https://testserver"]')
    monkeypatch.setenv("TAURIC_SECURE_COOKIES", "true")
    runtime_config.cache_clear()
    monkeypatch.setattr(sso, "_metadata", lambda _config: METADATA)
    public = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(signing_key.public_key()))
    public.update({"kid": "test-key", "use": "sig", "alg": "RS256"})
    captured = {}

    def provider_request(url, data=None):
        if url == METADATA["jwks_uri"]:
            return {"keys": [public]}
        assert url == METADATA["token_endpoint"]
        assert data["code"] == "one-use-test-code"
        assert (
            sso._b64(hashlib.sha256(data["code_verifier"].encode()).digest())
            == captured["challenge"]
        )
        assert not captured.get("used")
        captured["used"] = True
        return {"id_token": token(signing_key, nonce=captured["nonce"])}

    monkeypatch.setattr(sso, "_json_request", provider_request)
    with TestClient(
        app, base_url="https://testserver", headers={"Origin": "https://testserver"}
    ) as client:
        assert client.get("/api/v1/session").status_code == 401
        start = client.get("/api/v1/sso/start", follow_redirects=False)
        assert start.status_code == 302
        assert "HttpOnly" in start.headers["set-cookie"] and "Secure" in start.headers["set-cookie"]
        query = parse_qs(urlsplit(start.headers["location"]).query)
        captured.update(nonce=query["nonce"][0], challenge=query["code_challenge"][0])
        callback = client.get(
            "/api/v1/sso/callback",
            params={"state": query["state"][0], "code": "one-use-test-code"},
            follow_redirects=False,
        )
        assert callback.status_code == 303 and callback.headers["location"] == "/"
        assert callback.headers["referrer-policy"] == "no-referrer"
        assert client.get("/api/v1/session").json()["username"] == "test-owner"
        assert client.cookies.get(sso.ATTEMPT_COOKIE, path=sso.CALLBACK_PATH) is None
        # The callback cookie is consumed, so replay cannot create another session.
        replay = client.get(
            "/api/v1/sso/callback",
            params={"state": query["state"][0], "code": "one-use-test-code"},
            follow_redirects=False,
        )
        assert replay.headers["location"] == "/?sso=failed"


def test_wrong_state_never_exchanges_code(database, monkeypatch):
    from tauric.app import app

    for key, value in {
        "TAURIC_SSO_ISSUER": ISSUER,
        "TAURIC_SSO_CLIENT_ID": CLIENT,
        "TAURIC_SSO_SUBJECT": SUBJECT,
        "TAURIC_SSO_CALLBACK_URI": CALLBACK,
        "TAURIC_ALLOWED_ORIGINS": '["https://testserver"]',
        "TAURIC_SECURE_COOKIES": "true",
    }.items():
        monkeypatch.setenv(key, value)
    runtime_config.cache_clear()
    monkeypatch.setattr(sso, "_metadata", lambda _config: METADATA)
    monkeypatch.setattr(
        sso, "_json_request", lambda *_args, **_kw: pytest.fail("Unexpected exchange")
    )
    with TestClient(app, base_url="https://testserver") as client:
        client.get("/api/v1/sso/start", follow_redirects=False)
        result = client.get(
            "/api/v1/sso/callback",
            params={"state": "wrong", "code": "fake"},
            follow_redirects=False,
        )
        assert result.headers["location"] == "/?sso=failed"
        assert client.get("/api/v1/session").status_code == 401
