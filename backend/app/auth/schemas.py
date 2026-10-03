from datetime import datetime
from typing import Literal

from pydantic import Field, SecretStr, field_validator

from app.contracts import Contract

Role = Literal["admin", "user"]


class Login(Contract):
    username: str = Field(min_length=3, max_length=50, pattern=r"^[a-zA-Z0-9_.-]+$")
    password: SecretStr = Field(min_length=1, max_length=128)

    @field_validator("username")
    @classmethod
    def normalize_username(cls, value: str) -> str:
        return value.lower()


class UserCreate(Login):
    password: SecretStr = Field(min_length=8, max_length=128)
    role: Role = "user"


class UserUpdate(Contract):
    password: SecretStr | None = Field(default=None, min_length=8, max_length=128)
    role: Role | None = None
    is_active: bool | None = None

    @field_validator("password", "role", "is_active")
    @classmethod
    def no_explicit_null(cls, value: object) -> object:
        if value is None:
            raise ValueError("Omit unchanged fields instead of sending null")
        return value


class UserView(Contract):
    id: str
    username: str
    role: Role
    is_active: bool
    created_at: datetime


class TokenView(Contract):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int
    user: UserView
