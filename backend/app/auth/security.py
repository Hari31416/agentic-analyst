"""Password hashing, bounded JWTs and current account checks."""

from datetime import datetime, timedelta, timezone
from typing import Annotated, Any
from uuid import UUID, uuid4

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db.models import RevokedToken, User
from app.db.session import get_session

COOKIE = "analyst_access"
ISSUER = "agentic-rag-analyst"
AUDIENCE = "analyst-api"
PASSWORDS = PasswordHasher()
# Equal-cost verification for usernames that do not exist.
DUMMY_HASH = PASSWORDS.hash(str(uuid4()))
BEARER = HTTPBearer(auto_error=False)
PUBLIC = {("GET", "/api/health"), ("POST", "/api/auth/login")}


def hash_password(password: str) -> str:
    return PASSWORDS.hash(password)


def verify_password(password: str, stored: str) -> bool:
    try:
        return PASSWORDS.verify(stored, password)
    except (VerificationError, InvalidHashError):
        return False


def key(settings: Settings) -> str:
    if settings.jwt_secret_key is None:
        raise HTTPException(503, "Authentication is not configured")
    return settings.jwt_secret_key.get_secret_value()


def create_token(user: User, settings: Settings) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "sub": user.id,
            "iss": ISSUER,
            "aud": AUDIENCE,
            "iat": now,
            "nbf": now,
            "exp": now + timedelta(minutes=settings.jwt_access_token_expire_minutes),
            "jti": str(uuid4()),
            "ver": user.token_version,
            "type": "access",
        },
        key(settings),
        algorithm="HS256",
    )


def unauthorized() -> HTTPException:
    return HTTPException(
        401, "Invalid or expired credentials", headers={"WWW-Authenticate": "Bearer"}
    )


def decode_token(token: str, settings: Settings) -> dict[str, Any]:
    try:
        if len(token) > 4096:
            raise ValueError("Token too long")
        claims = jwt.decode(
            token,
            key(settings),
            algorithms=["HS256"],
            issuer=ISSUER,
            audience=AUDIENCE,
            options={
                "require": [
                    "sub",
                    "exp",
                    "iat",
                    "nbf",
                    "iss",
                    "aud",
                    "jti",
                    "ver",
                    "type",
                ]
            },
        )
        UUID(claims["sub"])
        UUID(claims["jti"])
        if (
            claims["type"] != "access"
            or type(claims["ver"]) is not int
            or claims["ver"] < 0
            or any(type(claims[name]) is not int for name in ("iat", "nbf", "exp"))
        ):
            raise ValueError("Invalid claims")
        return claims
    except (jwt.InvalidTokenError, ValueError, TypeError, KeyError):
        raise unauthorized() from None


def check_origin(request: Request, settings: Settings, *, cookie: bool) -> None:
    # Bearer clients do not rely on ambient browser credentials. Login is also
    # checked to prevent login CSRF. Never trust forwarded Host headers here.
    if request.method in {"GET", "HEAD", "OPTIONS"}:
        return
    origin = request.headers.get("origin")
    if origin and origin not in settings.auth_allowed_origins:
        raise HTTPException(403, "Request origin is not allowed")
    if cookie and (not origin or request.headers.get("sec-fetch-site") == "cross-site"):
        raise HTTPException(403, "Cookie requests require an allowed Origin")


def require_auth(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(BEARER)],
) -> User | None:
    if (request.method, request.url.path) in PUBLIC:
        return None
    # A malformed explicit authorization header must not fall back to a cookie.
    header = request.headers.get("authorization")
    if header and credentials is None:
        raise unauthorized()
    token = credentials.credentials if credentials else request.cookies.get(COOKIE)
    if not token:
        raise unauthorized()
    claims = decode_token(token, settings)
    check_origin(request, settings, cookie=credentials is None)
    user = session.get(User, claims["sub"])
    if (
        user is None
        or not user.is_active
        or user.token_version != claims["ver"]
        or session.get(RevokedToken, claims["jti"]) is not None
    ):
        raise unauthorized()
    request.state.auth_claims = claims
    return user


def require_admin(user: Annotated[User | None, Depends(require_auth)]) -> User:
    if user is None or user.role != "admin":
        raise HTTPException(403, "Admin access required")
    return user


def stream_authorized(claims: dict[str, Any]) -> bool:
    """Recheck a long-lived event stream using a short-lived DB session."""
    from app.db.session import factory

    if datetime.now(timezone.utc).timestamp() >= claims["exp"]:
        return False
    with factory()() as session:
        user = session.get(User, claims["sub"])
        return bool(
            user is not None
            and user.is_active
            and user.token_version == claims["ver"]
            and session.get(RevokedToken, claims["jti"]) is None
        )
