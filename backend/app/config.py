from functools import lru_cache
from pathlib import Path
from typing import Literal

from cryptography.fernet import Fernet
from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=("../.env", ".env"), extra="ignore", env_ignore_empty=True
    )

    database_url: SecretStr = SecretStr(
        "postgresql+psycopg://analyst:analyst@localhost:55432/analyst"
    )
    database_encryption_key: SecretStr | None = None
    storage_root: Path = Path("../data")
    storage_backend: Literal["filesystem", "s3"] = "filesystem"
    s3_endpoint_url: str | None = None
    s3_access_key: SecretStr | None = None
    s3_secret_key: SecretStr | None = None
    s3_bucket: str = "analyst"
    s3_region: str = "us-east-1"
    openai_base_url: str | None = None
    openai_api_key: SecretStr | None = None
    openai_model: str | None = None
    sandbox_base_url: str | None = None
    sandbox_auth_token: SecretStr | None = None
    sandbox_image: str | None = None
    external_provider_policy: Literal["local_only", "configured"] = "local_only"
    embedding_model: str | None = None
    reranker_model: str | None = None
    supported_languages: list[str] = ["en-IN", "hi-IN"]
    max_tool_calls: int = 20
    max_model_calls: int = 20
    max_context_characters: int = 60000
    max_result_bytes: int = 65536
    max_upload_bytes: int = 25 * 1024 * 1024
    run_timeout_seconds: int = 300
    job_lease_seconds: int = 60
    job_max_attempts: int = 3

    @field_validator("openai_base_url", "sandbox_base_url", "s3_endpoint_url")
    @classmethod
    def service_url(cls, value: str | None) -> str | None:
        from urllib.parse import urlsplit

        if value is None:
            return value
        parts = urlsplit(value)
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            raise ValueError("service URLs must use HTTP or HTTPS")
        if parts.username or parts.password or parts.query or parts.fragment:
            raise ValueError(
                "service URLs must not contain credentials or query strings"
            )
        return value.rstrip("/")

    @field_validator("database_encryption_key")
    @classmethod
    def encryption_key(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None:
            try:
                Fernet(value.get_secret_value().encode())
            except Exception as exc:
                raise ValueError(
                    "DATABASE_ENCRYPTION_KEY must be a Fernet key"
                ) from exc
        return value

    @model_validator(mode="after")
    def valid_budgets(self) -> "Settings":
        if self.storage_backend == "s3" and not (
            self.s3_endpoint_url and self.s3_access_key and self.s3_secret_key
        ):
            raise ValueError("S3 storage needs an endpoint, access key, and secret key")
        for name in (
            "max_tool_calls",
            "max_model_calls",
            "max_context_characters",
            "max_result_bytes",
            "max_upload_bytes",
            "run_timeout_seconds",
            "job_lease_seconds",
            "job_max_attempts",
        ):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")
        if self.job_lease_seconds < 6:
            raise ValueError("job lease must allow at least six seconds")
        return self

    @property
    def model_configured(self) -> bool:
        return bool(self.openai_base_url and self.openai_api_key and self.openai_model)

    @property
    def sandbox_configured(self) -> bool:
        return bool(self.sandbox_base_url and self.sandbox_image)


@lru_cache
def get_settings() -> Settings:
    return Settings()
