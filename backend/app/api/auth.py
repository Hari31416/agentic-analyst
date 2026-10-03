"""JWT login and admin-managed local accounts. No public registration."""

import time
from datetime import datetime, timezone
from threading import Lock
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.bootstrap import lock_accounts
from app.auth.schemas import Login, TokenView, UserCreate, UserUpdate, UserView
from app.auth.security import (
    COOKIE,
    DUMMY_HASH,
    PASSWORDS,
    check_origin,
    create_token,
    hash_password,
    require_admin,
    require_auth,
    verify_password,
)
from app.config import Settings, get_settings
from app.db.models import RevokedToken, User
from app.db.session import get_session

router = APIRouter(prefix="/api/auth", tags=["Authentication"])
Db = Annotated[Session, Depends(get_session)]
Config = Annotated[Settings, Depends(get_settings)]
Admin = Annotated[User, Depends(require_admin)]
Current = Annotated[User | None, Depends(require_auth)]


class LoginLimiter:
    """Small process-local throttle, bounded even with forged client addresses."""

    def __init__(self) -> None:
        self.entries: dict[str, list[float]] = {}
        self.lock = Lock()

    def admit(self, address: str) -> None:
        now = time.monotonic()
        with self.lock:
            self.entries = {
                k: [t for t in v if t > now - 60]
                for k, v in self.entries.items()
                if v and v[-1] > now - 60
            }
            history = self.entries.get(address, [])
            if len(history) >= 10 or (
                address not in self.entries and len(self.entries) >= 1024
            ):
                raise HTTPException(
                    429, "Too many login attempts", headers={"Retry-After": "60"}
                )
            self.entries[address] = [*history, now]


login_limiter = LoginLimiter()


@router.post("/login", response_model=TokenView)
def login(
    body: Login, request: Request, response: Response, session: Db, settings: Config
) -> TokenView:
    check_origin(request, settings, cookie=False)
    login_limiter.admit(request.client.host if request.client else "unknown")
    user = session.scalar(select(User).where(User.username == body.username))
    valid = verify_password(
        body.password.get_secret_value(), user.password_hash if user else DUMMY_HASH
    )
    if not valid or user is None or not user.is_active:
        raise HTTPException(
            401,
            "Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if PASSWORDS.check_needs_rehash(user.password_hash):
        user.password_hash = hash_password(body.password.get_secret_value())
    session.execute(
        delete(RevokedToken).where(RevokedToken.expires_at < datetime.now(timezone.utc))
    )
    session.commit()
    token = create_token(user, settings)
    seconds = settings.jwt_access_token_expire_minutes * 60
    response.set_cookie(
        COOKIE,
        token,
        max_age=seconds,
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite="strict",
        path="/api",
    )
    response.headers["Cache-Control"] = "no-store"
    return TokenView(
        access_token=token, expires_in=seconds, user=UserView.model_validate(user)
    )


@router.get("/me", response_model=UserView)
def me(user: Current, response: Response) -> User:
    response.headers["Cache-Control"] = "no-store"
    assert user is not None
    return user


@router.post("/logout", status_code=204)
def logout(request: Request, session: Db, settings: Config, user: Current) -> Response:
    claims = request.state.auth_claims
    # Duplicate logout requests are idempotent at the DB boundary.
    if session.get(RevokedToken, claims["jti"]) is None:
        session.add(
            RevokedToken(
                jti=claims["jti"],
                expires_at=datetime.fromtimestamp(claims["exp"], timezone.utc),
            )
        )
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
    response = Response(status_code=204, headers={"Cache-Control": "no-store"})
    response.delete_cookie(
        COOKIE,
        path="/api",
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite="strict",
    )
    return response


@router.get("/users", response_model=list[UserView])
def list_users(
    session: Db,
    admin: Admin,
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> list[User]:
    return list(
        session.scalars(
            select(User).order_by(User.created_at, User.id).limit(limit).offset(offset)
        )
    )


@router.post("/users", response_model=UserView, status_code=201)
def create_user(body: UserCreate, session: Db, admin: Admin) -> User:
    lock_accounts(session)
    user = User(
        username=body.username,
        password_hash=hash_password(body.password.get_secret_value()),
        role=body.role,
    )
    session.add(user)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(409, "Username already exists") from None
    return user


@router.patch("/users/{user_id}", response_model=UserView)
def update_user(user_id: UUID, body: UserUpdate, session: Db, admin: Admin) -> User:
    lock_accounts(session)
    user = session.get(User, str(user_id), populate_existing=True)
    if user is None:
        raise HTTPException(404, "User not found")
    if (
        user.is_active
        and user.role == "admin"
        and (body.role == "user" or body.is_active is False)
    ):
        other = session.scalar(
            select(User.id)
            .where(User.role == "admin", User.is_active.is_(True), User.id != user.id)
            .limit(1)
        )
        if other is None:
            raise HTTPException(409, "Cannot disable or demote the last active admin")
    if body.password is not None:
        user.password_hash = hash_password(body.password.get_secret_value())
    if body.role is not None:
        user.role = body.role
    if body.is_active is not None:
        user.is_active = body.is_active
    if body.model_fields_set:
        user.token_version += 1
    session.commit()
    return user
