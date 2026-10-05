from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "KaisQr_server"
    environment: str = "development"
    host: str = "0.0.0.0"
    port: int = 8000
    api_prefix: str = "/api/v1"

    api_key: str = "development-key"
    cors_origins: str = "http://localhost:5171,http://127.0.0.1:5171,http://localhost:5173,http://127.0.0.1:5173,http://local.kaisweb.net"

    session_ttl_seconds: int = Field(default=600, ge=60, le=3600)
    max_documents_per_session: int = Field(default=20, ge=1, le=100)
    max_file_size_bytes: int = Field(default=15 * 1024 * 1024, ge=1024)
    temporary_storage_path: Path = Path("storage/temp")

    public_websocket_url: str = "ws://localhost:8000/api/v1/ws/v1"

    kaisvm_forward_enabled: bool = False
    kaisvm_base_url: str = "http://localhost:3500/api"
    kaisvm_service_token: str = ""

    transporte_forward_enabled: bool = False
    transporte_base_url: str = "http://localhost:3006/api"
    transporte_service_key: str = ""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
