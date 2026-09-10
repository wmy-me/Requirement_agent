from __future__ import annotations

from sqlalchemy.engine import URL
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment settings for the requirement management service."""

    app_env: str = Field(default="dev", alias="APP_ENV")
    app_port: int = Field(default=8888, alias="APP_PORT")
    mcp_port: int = Field(default=8000, alias="MCP_PORT")
    # 业务展示时区（如 Asia/Shanghai）：数据库统一以 UTC 存储，前端展示时转成本地时区
    display_timezone: str = Field(default="Asia/Shanghai", alias="DISPLAY_TIMEZONE")

    postgres_host: str = Field(default="localhost", alias="POSTGRES_HOST")
    postgres_port: int = Field(default=5432, alias="POSTGRES_PORT")
    postgres_db: str = Field(default="requirement_agent", alias="POSTGRES_DB")
    postgres_user: str = Field(default="postgres", alias="POSTGRES_USER")
    postgres_password: SecretStr = Field(default=SecretStr(""), alias="POSTGRES_PASSWORD")

    minio_endpoint: str = Field(default="localhost:9000", alias="MINIO_ENDPOINT")
    minio_access_key: str = Field(default="", alias="MINIO_ACCESS_KEY")
    minio_secret_key: SecretStr = Field(default=SecretStr(""), alias="MINIO_SECRET_KEY")
    minio_bucket: str = Field(default="requirement-attachments", alias="MINIO_BUCKET")

    api_auth_token: SecretStr = Field(default=SecretStr(""), alias="API_AUTH_TOKEN")
    api_actor_id: str = Field(default="api-user", alias="API_ACTOR_ID")
    mcp_auth_token: SecretStr = Field(default=SecretStr(""), alias="MCP_AUTH_TOKEN")
    mcp_actor_id: str = Field(default="mcp-client", alias="MCP_ACTOR_ID")
    mcp_issuer_url: str = Field(default="http://localhost:8000", alias="MCP_ISSUER_URL")
    mcp_resource_url: str = Field(default="http://localhost:8000/mcp", alias="MCP_RESOURCE_URL")

    llm_provider: str = Field(default="deepseek", alias="LLM_PROVIDER")
    openai_api_key: SecretStr = Field(default=SecretStr(""), alias="OPENAI_API_KEY")
    openai_base_url: str = Field(default="https://api.openai.com/v1", alias="OPENAI_BASE_URL")
    openai_model: str = Field(default="gpt-5.5", alias="OPENAI_MODEL")
    deepseek_api_key: SecretStr = Field(default=SecretStr(""), alias="DEEPSEEK_API_KEY")
    deepseek_base_url: str = Field(default="https://api.deepseek.com", alias="DEEPSEEK_BASE_URL")
    deepseek_model: str = Field(default="deepseek-chat", alias="DEEPSEEK_MODEL")
    embedding_model: str = Field(default="text-embedding-3-small", alias="EMBEDDING_MODEL")
    embedding_base_url: str = Field(default="", alias="EMBEDDING_BASE_URL")
    embedding_api_key: SecretStr = Field(default=SecretStr(""), alias="EMBEDDING_API_KEY")
    embedding_dimension: int = Field(default=1536, alias="EMBEDDING_DIMENSION")

    # 后台 outbox 消费循环：由 API 进程持续认领 embedding 同步 / 文档分片事件
    outbox_consumer_enabled: bool = Field(default=True, alias="OUTBOX_CONSUMER_ENABLED")
    outbox_poll_interval: float = Field(default=5.0, alias="OUTBOX_POLL_INTERVAL_SECONDS")
    outbox_poll_batch: int = Field(default=50, alias="OUTBOX_POLL_BATCH")
    outbox_stale_timeout_seconds: int = Field(default=300, alias="OUTBOX_STALE_TIMEOUT_SECONDS")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @property
    def database_url(self) -> str:
        return URL.create(
            drivername="postgresql+psycopg",
            username=self.postgres_user,
            password=self.postgres_password.get_secret_value(),
            host=self.postgres_host,
            port=self.postgres_port,
            database=self.postgres_db,
        ).render_as_string(hide_password=False)

    @property
    def psycopg_dsn(self) -> str:
        return URL.create(
            drivername="postgresql",
            username=self.postgres_user,
            password=self.postgres_password.get_secret_value(),
            host=self.postgres_host,
            port=self.postgres_port,
            database=self.postgres_db,
        ).render_as_string(hide_password=False)

    @property
    def active_llm_api_key(self) -> str:
        if self.llm_provider.lower() == "deepseek":
            return self.deepseek_api_key.get_secret_value()
        if self.llm_provider.lower() == "openai":
            return self.openai_api_key.get_secret_value()
        return ""

    @property
    def active_llm_base_url(self) -> str:
        if self.llm_provider.lower() == "deepseek":
            return self.deepseek_base_url
        if self.llm_provider.lower() == "openai":
            return self.openai_base_url
        return ""

    @property
    def active_llm_model(self) -> str:
        if self.llm_provider.lower() == "deepseek":
            return self.deepseek_model
        if self.llm_provider.lower() == "openai":
            return self.openai_model
        return ""

    def require_database_credentials(self) -> None:
        missing = [
            name
            for name, value in (
                ("POSTGRES_USER", self.postgres_user),
                ("POSTGRES_PASSWORD", self.postgres_password.get_secret_value()),
                ("POSTGRES_DB", self.postgres_db),
            )
            if not value.strip()
        ]
        if missing:
            raise RuntimeError(f"Missing database configuration: {', '.join(missing)}")

    def require_api_auth(self) -> None:
        if not self.api_auth_token.get_secret_value().strip():
            raise RuntimeError("API_AUTH_TOKEN must be configured for protected API operations")

    def require_mcp_auth(self) -> None:
        if not self.mcp_auth_token.get_secret_value().strip():
            raise RuntimeError("MCP_AUTH_TOKEN must be configured for MCP operations")


settings = Settings()

__all__ = ["Settings", "settings"]
