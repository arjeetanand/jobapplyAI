from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Ethical Job Assistant"
    database_url: str = "sqlite:///./seekapply.db"
    storage_root: Path = Path("../storage")
    cors_origins: list[str] = ["http://127.0.0.1:5173", "http://localhost:5173"]
    match_threshold: int = 60
    latex_template_path: Path | None = None

    oci_config_file: str = "~/.oci/config"
    oci_profile: str = "DEFAULT"
    oci_region: str | None = None
    oci_compartment_ocid: str | None = None
    oci_genai_model_id: str | None = None
    oci_genai_endpoint_id: str | None = None

    model_config = SettingsConfigDict(env_file=".env", env_prefix="", extra="ignore")

    @property
    def resolved_storage_root(self) -> Path:
        return self.storage_root.expanduser().resolve()


@lru_cache
def get_settings() -> Settings:
    return Settings()
