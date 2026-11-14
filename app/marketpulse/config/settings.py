import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    app_name: str = "MarketPulse India"
    app_version: str = "0.1.0"
    environment: str = field(
        default_factory=lambda: os.environ.get("MARKETPULSE_ENV", "development")
    )
    kotak_consumer_key: str | None = field(
        default_factory=lambda: os.environ.get("KOTAK_CONSUMER_KEY")
    )
    kotak_environment: str = field(
        default_factory=lambda: os.environ.get("KOTAK_ENVIRONMENT", "prod")
    )
    project_root: Path = field(
        default_factory=lambda: Path(__file__).resolve().parents[3]
    )

    @property
    def data_dir(self) -> Path:
        return self.project_root / "data"

    @property
    def instrument_dir(self) -> Path:
        return self.data_dir / "instruments"


settings = Settings()
