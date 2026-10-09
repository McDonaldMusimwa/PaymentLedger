from decimal import Decimal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://ledger:ledger@localhost:5432/ledger_db"
    jwks_url: str = "http://localhost:4000/.well-known/jwks.json"
    jwt_issuer: str = "paymentledger-auth"
    jwt_audience: str = "paymentledger-api"
    cors_origins: str = "http://localhost:5173"  # comma-separated

    storage_dir: str = "./data/invoices"
    max_upload_bytes: int = 20 * 1024 * 1024

    # Invoice extraction. Credentials come from ANTHROPIC_API_KEY (read by the SDK).
    extraction_model: str = "claude-opus-5-5"

    # 3-way match tolerances.
    price_tolerance: Decimal = Decimal("0.02")  # 2% unit-price variance vs the PO
    amount_tolerance: Decimal = Decimal("0.01")  # rounding slack on totals
    wallet_change_hold_hours: int = 72

    # Shared secret the settlement-service uses to report onchain results.
    internal_api_token: str = "dev-internal-token"
    # Enables /demo endpoints (seed data, simulated settlement). Turn off for real customers.
    enable_demo: bool = True


settings = Settings()
