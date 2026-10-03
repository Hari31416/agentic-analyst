"""Seed exactly once; restarts never reset an account's password or role."""

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.auth.schemas import UserCreate
from app.auth.security import hash_password
from app.config import Settings
from app.db.models import User


def lock_accounts(session: Session) -> None:
    # Serializes API starts and role updates across PostgreSQL API processes.
    if session.get_bind().dialect.name == "postgresql":
        session.execute(text("SELECT pg_advisory_xact_lock(73419021)"))


def seed_admin(session: Session, settings: Settings) -> None:
    if settings.jwt_secret_key is None:
        raise RuntimeError("Set JWT_SECRET_KEY before starting the API")
    lock_accounts(session)
    if session.scalar(select(User.id).limit(1)) is not None:
        session.commit()
        return
    if not settings.admin_username or not settings.admin_password:
        raise RuntimeError(
            "Set ADMIN_USERNAME and ADMIN_PASSWORD for the first API start"
        )
    # Convert validation failures to a safe error, never including input secrets.
    try:
        body = UserCreate(
            username=settings.admin_username,
            password=settings.admin_password,
            role="admin",
        )
    except ValueError:
        raise RuntimeError(
            "Admin username/password do not meet account requirements"
        ) from None
    session.add(
        User(
            username=body.username,
            password_hash=hash_password(body.password.get_secret_value()),
            role="admin",
        )
    )
    session.commit()
