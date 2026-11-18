import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec

SUPABASE_URL = "https://test.supabase.co"
USER_ID = "11111111-1111-1111-1111-111111111111"

_private_key = ec.generate_private_key(ec.SECP256R1())


def make_token(sub: str = USER_ID, **overrides) -> str:
    claims = {
        "sub": sub,
        "aud": "authenticated",
        "iss": f"{SUPABASE_URL}/auth/v1",
        "exp": int(time.time()) + 3600,
        **overrides,
    }
    return jwt.encode(claims, _private_key, algorithm="ES256")


@pytest.fixture(autouse=True)
def fake_jwks(monkeypatch):
    # verify against the local test key instead of fetching supabase's jwks
    monkeypatch.setattr("sous_svc.auth._signing_key", lambda token: _private_key.public_key())


@pytest.fixture
def auth_header():
    return {"Authorization": f"Bearer {make_token()}"}
