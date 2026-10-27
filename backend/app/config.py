from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from .credential_paths import default_token_file, provider_secret_file


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=Path(__file__).resolve().parents[2] / ".env", extra="ignore")

    app_env: str = "development"
    app_secret_key: str = "development-only-change-me"
    database_url: str = "sqlite:///" + (Path(__file__).resolve().parents[2] / "state" / "dispatcher.sqlite3").as_posix()
    token_file: Path = Field(default_factory=default_token_file)
    comfyui_url: str = "http://127.0.0.1:8188"
    comfyui_input_dir: Path = Path("X:/git/ComfyUI/input")
    model_directories: list[Path] = [Path('E:/ComfyUI/models'), Path('X:/git/ComfyUI/models')]
    cloud_output_dir: Path = Path(__file__).resolve().parents[2] / "state" / "cloud-outputs"
    vast_api_key: str | None = None
    vast_api_key_file: Path = Field(default_factory=lambda: provider_secret_file("vast"))
    runpod_api_key_file: Path = Field(default_factory=lambda: provider_secret_file("runpod"))
    huggingface_token_file: Path = Field(default_factory=lambda: provider_secret_file("huggingface"))

    max_active_gpus: int = Field(default=10, ge=1, le=64)
    max_gpu_price_per_hour: float = Field(default=1.70, gt=0)
    max_total_price_per_hour: float = Field(default=12.50, gt=0)
    max_instance_lifetime_hours: int = Field(default=6, ge=1, le=168)
    idle_destroy_minutes: int = Field(default=15, ge=1, le=1440)
    # Every new worker downloads its whole model profile. Vast hosts may bill that per GB at
    # anything from $0 to $0.04/GB, so a cheap-looking host can cost more in sync than in GPU time.
    max_download_cost_per_gb: float = Field(default=0.01, ge=0)
    # A ready worker with no heartbeat (or a Vast instance that is no longer running) is destroyed after this long.
    unresponsive_destroy_minutes: int = Field(default=3, ge=1, le=60)
    # Refuse a Vast launch unless the account credit covers the model sync plus this much GPU time.
    min_credit_gpu_hours: float = Field(default=0.5, ge=0)


@lru_cache
def get_settings() -> Settings:
    return Settings()
