from decimal import Decimal
from typing import Literal
from urllib.parse import urlparse

from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _env(*names: str):
    return Field(default=None, validation_alias=AliasChoices(*names))


class Settings(BaseSettings):
    # Empty variables are ignored, so a blank value (e.g. from docker-compose) never
    # shadows a real one.
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", env_ignore_empty=True)

    database_url: str = "postgresql+asyncpg://ledger:ledger@localhost:5432/ledger_db"
    jwks_url: str = "http://localhost:4000/.well-known/jwks.json"
    jwt_issuer: str = "paymentledger-auth"
    jwt_audience: str = "paymentledger-api"
    cors_origins: str = "http://localhost:5173"  # comma-separated

    # Invoice files: "s3" = any S3-compatible store (Cloudflare R2), "local" = disk.
    # "auto" uses s3 when its endpoint, bucket and keys are all set.
    storage_backend: Literal["auto", "local", "s3"] = "auto"
    storage_prefix: str = "payment-ledger"
    storage_dir: str = "./data/invoices"
    # R2: https://<account_id>.r2.cloudflarestorage.com (a trailing /<bucket> is accepted).
    s3_endpoint_url: str | None = _env("S3_ENDPOINT_URL", "S3_API_URL")
    s3_bucket: str | None = _env("S3_BUCKET")
    s3_access_key_id: str | None = _env("S3_ACCESS_KEY_ID", "ACCESS_KEY")
    s3_secret_access_key: str | None = _env("S3_SECRET_ACCESS_KEY", "SECRET_KEY")
    s3_region: str = "auto"
    max_upload_bytes: int = 20 * 1024 * 1024

    # Invoice extraction. "openrouter" reads the pypdf text with a free OpenRouter model;
    # "anthropic" uses Claude (and can also read scanned PDFs). "auto" prefers OpenRouter
    # when OPENROUTER_API_KEY is set.
    extraction_provider: Literal["auto", "openrouter", "anthropic"] = "auto"
    openrouter_api_key: str | None = _env("OPENROUTER_API_KEY")
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    # Tried in order; free models come and go, so keep a router as the last resort.
    openrouter_models: str = "nvidia/nemotron-3-super-120b-a12b:free,openrouter/free"
    anthropic_model: str = Field(
        default="claude-opus-5-5",
        validation_alias=AliasChoices("ANTHROPIC_MODEL", "EXTRACTION_MODEL"),
    )

    # 3-way match tolerances.
    price_tolerance: Decimal = Decimal("0.02")  # 2% unit-price variance vs the PO
    amount_tolerance: Decimal = Decimal("0.01")  # rounding slack on totals
    wallet_change_hold_hours: int = 72

    # Shared secret the settlement-service uses to report onchain results.
    internal_api_token: str = "dev-internal-token"
    # Enables /demo endpoints (seed data, simulated settlement). Turn off for real customers.
    enable_demo: bool = True

    @model_validator(mode="after")
    def _resolve(self) -> "Settings":
        # Accept the endpoint with the bucket in the path, as the R2 dashboard shows it.
        if self.s3_endpoint_url:
            url = urlparse(self.s3_endpoint_url)
            path_bucket = url.path.strip("/")
            if path_bucket:
                self.s3_endpoint_url = f"{url.scheme}://{url.netloc}"
                self.s3_bucket = self.s3_bucket or path_bucket.split("/")[0]
        if self.storage_backend == "auto":
            s3_ready = all(
                (
                    self.s3_endpoint_url,
                    self.s3_bucket,
                    self.s3_access_key_id,
                    self.s3_secret_access_key,
                )
            )
            self.storage_backend = "s3" if s3_ready else "local"
        if self.extraction_provider == "auto":
            self.extraction_provider = "openrouter" if self.openrouter_api_key else "anthropic"
        return self

    @property
    def openrouter_model_list(self) -> list[str]:
        return [m.strip() for m in self.openrouter_models.split(",") if m.strip()]


settings = Settings()
