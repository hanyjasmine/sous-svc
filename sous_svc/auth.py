from functools import cache, wraps

import jwt
from flask import abort, current_app, g, request


@cache
def _jwks_client(supabase_url: str) -> jwt.PyJWKClient:
    # supabase publishes its public signing keys here, cached after first fetch
    return jwt.PyJWKClient(f"{supabase_url}/auth/v1/.well-known/jwks.json", cache_keys=True)


def _signing_key(token: str):
    return _jwks_client(_supabase_url()).get_signing_key_from_jwt(token).key


def _supabase_url() -> str:
    return current_app.config["SUPABASE_URL"].rstrip("/")


def verify_token(token: str) -> str:
    claims = jwt.decode(
        token,
        _signing_key(token),
        algorithms=["ES256", "RS256"],
        audience="authenticated",
        issuer=f"{_supabase_url()}/auth/v1",
    )
    return claims["sub"]


def current_user_id() -> str | None:
    if "user_id" in g:
        return g.user_id
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        g.user_id = None
        return None
    try:
        g.user_id = verify_token(header.removeprefix("Bearer ").strip())
    except (jwt.PyJWTError, KeyError):
        # a bad token is an error, not a silent guest
        abort(401, "invalid or expired session, sign in again")
    return g.user_id


def require_user(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if current_user_id() is None:
            abort(401, "sign in required")
        return view(*args, **kwargs)

    return wrapper
