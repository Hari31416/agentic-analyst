from functools import lru_cache
from pathlib import Path
from typing import Literal

from cryptography.fernet import Fernet
from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=("../.env", ".env"),
        extra="ignore",
        env_ignore_empty=True,
        hide_input_in_errors=True,
    )

    database_url: SecretStr = SecretStr(
        "postgresql+psycopg://analyst:analyst@localhost:55432/analyst"
    )
    database_encryption_key: SecretStr | None = None
    jwt_secret_key: SecretStr | None = None
    jwt_access_token_expire_minutes: int = 60
    auth_cookie_secure: bool = False
    auth_allowed_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]
    admin_username: str | None = None
    admin_password: SecretStr | None = None
    eval_username: str | None = None
    eval_password: SecretStr | None = None
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
    embedding_model_path: Path | None = None
    embedding_dimension: int = 384
    embedding_revision: str | None = None
    embedding_batch_size: int = 16
    document_max_chunks: int = 4096
    embedding_threads: int = 2
    crawl_enabled: bool = False
    crawl_approved_hosts: list[str] = []
    retrieval_aliases: dict[str, list[str]] = {}
    reranker_model: str | None = None
    reranker_model_path: Path | None = None
    reranker_revision: str | None = None
    supported_languages: list[str] = ["en-IN", "hi-IN"]
    speech_model_path: Path | None = None
    speech_max_upload_bytes: int = 10 * 1024 * 1024
    speech_max_duration_seconds: int = 60
    speech_timeout_seconds: int = 90
    speech_cpu_threads: int = 2
    max_tool_calls: int = 20
    max_model_calls: int = 20
    max_context_characters: int = 300000
    max_result_bytes: int = 65536
    max_upload_bytes: int = 25 * 1024 * 1024
    ocr_enabled: bool = True
    ocr_languages: str = "eng+hin"
    ocr_timeout_seconds: int = 20
    ingestion_profile: Literal["baseline", "layout"] = "baseline"
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

    @field_validator("jwt_secret_key")
    @classmethod
    def jwt_key(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and len(value.get_secret_value().encode()) < 32:
            raise ValueError("JWT_SECRET_KEY must contain at least 32 bytes")
        return value

    @field_validator("jwt_access_token_expire_minutes")
    @classmethod
    def jwt_lifetime(cls, value: int) -> int:
        if not 1 <= value <= 1440:
            raise ValueError("JWT lifetime must be between 1 and 1440 minutes")
        return value

    @field_validator("auth_allowed_origins")
    @classmethod
    def auth_origins(cls, values: list[str]) -> list[str]:
        from urllib.parse import urlsplit

        for value in values:
            parts = urlsplit(value)
            if (
                parts.scheme not in {"http", "https"}
                or not parts.hostname
                or parts.username
                or parts.password
                or parts.path
                or parts.query
                or parts.fragment
            ):
                raise ValueError("Auth origins must be exact HTTP(S) origins")
        return values

    @field_validator("crawl_approved_hosts")
    @classmethod
    def valid_crawl_hosts(cls, value: list[str]) -> list[str]:
        from urllib.parse import urlsplit

        normalized = []
        for host in value:
            if not host or any(char in host for char in "/:@*?#"):
                raise ValueError(
                    "Crawl approval requires exact hostnames without schemes, ports or wildcards"
                )
            parsed = urlsplit("https://" + host)
            if parsed.hostname != host.lower():
                raise ValueError("Invalid crawl hostname")
            normalized.append(host.lower().encode("idna").decode("ascii"))
        return list(dict.fromkeys(normalized))

    @field_validator("retrieval_aliases")
    @classmethod
    def valid_aliases(cls, value: dict[str, list[str]]) -> dict[str, list[str]]:
        if len(value) > 50 or any(
            not key.strip()
            or len(key) > 100
            or len(aliases) > 5
            or any(not alias.strip() or len(alias) > 100 for alias in aliases)
            for key, aliases in value.items()
        ):
            raise ValueError("Retrieval aliases exceed bounded domain vocabulary")
        return value

    @field_validator("ocr_languages")
    @classmethod
    def valid_ocr_languages(cls, value: str) -> str:
        if not value or any(not part.isalnum() for part in value.split("+")):
            raise ValueError(
                "OCR languages must be plus-separated Tesseract language codes"
            )
        if not {"eng", "hin"}.issubset(set(value.split("+"))):
            raise ValueError("OCR language assets must include both eng and hin")
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
            "ocr_timeout_seconds",
            "run_timeout_seconds",
            "job_lease_seconds",
            "job_max_attempts",
            "embedding_dimension",
            "embedding_batch_size",
            "document_max_chunks",
            "embedding_threads",
            "speech_max_upload_bytes",
            "speech_max_duration_seconds",
            "speech_timeout_seconds",
            "speech_cpu_threads",
        ):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")
        if self.ocr_timeout_seconds > 60:
            raise ValueError("OCR timeout cannot exceed 60 seconds per page")
        if self.document_max_chunks > 16384:
            raise ValueError("Document chunk limit cannot exceed 16384")
        if self.speech_max_upload_bytes > 25 * 1024 * 1024:
            raise ValueError("Speech upload limit cannot exceed 25 MiB")
        if self.speech_max_duration_seconds > 120:
            raise ValueError("Speech duration limit cannot exceed 120 seconds")
        if self.speech_timeout_seconds > 180 or self.speech_cpu_threads > 8:
            raise ValueError("Speech resource settings exceed safe bounds")
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
