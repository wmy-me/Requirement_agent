from __future__ import annotations

from sqlalchemy.engine import URL
from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment settings for the requirement management service."""

    app_env: str = Field(default="dev", alias="APP_ENV")
    app_port: int = Field(default=8888, alias="APP_PORT")
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
    # 注：`TOOL_ACTOR_ID`（tool_actor_id）随 `src/requirement_agent/tools/` 一并删除（2026-09-16）——
    # 它是那批内部方法的唯一消费者。若将来重新对外暴露工具面，再按需恢复。

    llm_provider: str = Field(default="deepseek", alias="LLM_PROVIDER")
    openai_api_key: SecretStr = Field(default=SecretStr(""), alias="OPENAI_API_KEY")
    openai_base_url: str = Field(default="https://api.openai.com/v1", alias="OPENAI_BASE_URL")
    openai_model: str = Field(default="gpt-4o-mini", alias="OPENAI_MODEL")
    deepseek_api_key: SecretStr = Field(default=SecretStr(""), alias="DEEPSEEK_API_KEY")
    deepseek_base_url: str = Field(default="https://api.deepseek.com", alias="DEEPSEEK_BASE_URL")
    deepseek_model: str = Field(default="deepseek-chat", alias="DEEPSEEK_MODEL")
    embedding_model: str = Field(default="text-embedding-3-small", alias="EMBEDDING_MODEL")

    # —— LLM 请求调参 ——
    # temperature 默认 None 表示「请求体不带该字段」，与历史行为逐字节一致；
    # 需要确定性输出时显式设 LLM_TEMPERATURE=0。
    llm_temperature: float | None = Field(default=None, alias="LLM_TEMPERATURE")
    # 非流式与 embedding 请求的总超时（秒）。
    # 60 而非 30：实测长文档抽取耗时约 28s，30s 只差不到 2 秒，稍有抖动就超时降级到启发式，
    # 而失败只留一条 skill_fallback 告警，很难发现。
    llm_timeout_seconds: float = Field(default=60.0, gt=0, alias="LLM_TIMEOUT_SECONDS")
    # 流式请求的 read 超时（秒）——首字节后每段增量的等待上限。
    llm_stream_read_timeout_seconds: float = Field(
        default=120.0, gt=0, alias="LLM_STREAM_READ_TIMEOUT_SECONDS"
    )
    # 可恢复错误（网络层异常 / 408,409,425,429,5xx）的最大重试次数，0 表示不重试。
    llm_max_retries: int = Field(default=2, ge=0, alias="LLM_MAX_RETRIES")

    # —— 飞书渠道（事件订阅）——
    # verification_token / encrypt_key 至少配一个，否则 Webhook 端点会拒绝所有请求
    # （未配置的渠道端点不能接受任意输入）。两者都配时签名校验与 token 校验都会执行。
    feishu_app_id: str = Field(default="", alias="FEISHU_APP_ID")
    feishu_app_secret: SecretStr = Field(default=SecretStr(""), alias="FEISHU_APP_SECRET")
    feishu_verification_token: SecretStr = Field(
        default=SecretStr(""), alias="FEISHU_VERIFICATION_TOKEN"
    )
    # 配置后飞书回调为密文，需 AES-256-CBC 解密；同时强制要求签名校验
    feishu_encrypt_key: SecretStr = Field(default=SecretStr(""), alias="FEISHU_ENCRYPT_KEY")
    # 重试退避基数（秒）：第 n 次重试等待 base * 2**n 并叠加抖动。
    llm_retry_backoff_seconds: float = Field(
        default=0.5, ge=0.0, alias="LLM_RETRY_BACKOFF_SECONDS"
    )

    # 雪花 id：多实例部署时每实例设不同 SNOWFLAKE_WORKER_ID（0-1023），保证全局不撞号
    snowflake_worker_id: int = Field(default=0, alias="SNOWFLAKE_WORKER_ID", ge=0, le=1023)
    embedding_base_url: str = Field(default="", alias="EMBEDDING_BASE_URL")
    embedding_api_key: SecretStr = Field(default=SecretStr(""), alias="EMBEDDING_API_KEY")
    # 必须与向量列维度一致：三张表经 006 迁移后均为 vector(4096)。
    embedding_dimension: int = Field(default=4096, alias="EMBEDDING_DIMENSION")

    # 后台 outbox 消费循环：由 API 进程持续认领 embedding 同步 / 文档分片事件
    outbox_consumer_enabled: bool = Field(default=True, alias="OUTBOX_CONSUMER_ENABLED")
    outbox_poll_interval: float = Field(default=5.0, alias="OUTBOX_POLL_INTERVAL_SECONDS")
    outbox_poll_batch: int = Field(default=50, alias="OUTBOX_POLL_BATCH")
    outbox_stale_timeout_seconds: int = Field(default=300, alias="OUTBOX_STALE_TIMEOUT_SECONDS")

    # 对话运行被判为「僵尸」的静默阈值（秒）：超过它的 running / paused run 会被判为 failed。
    # 同一对话只允许一个活跃运行（migrations/012），而进程中断留下的 run 不会自己收尾 ——
    # 没有这个阈值，一次崩溃就把该对话永久堵死。取 30 分钟，远大于任何正常分析耗时
    # （4 个 LLM 步骤 × 60s 超时 × 重试次数），避免误杀正在跑的分析。
    chat_run_stale_timeout_seconds: int = Field(
        default=1800, gt=0, alias="CHAT_RUN_STALE_TIMEOUT_SECONDS"
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @field_validator("llm_temperature", mode="before")
    @classmethod
    def _blank_temperature_means_unset(cls, value: object) -> object:
        """`.env` 里写成 `LLM_TEMPERATURE=` 视为「未配置」而非非法数字。

        .env 是人手编辑的文件，留空是常见写法；不拦的话 pydantic 会直接抛
        ValidationError，表现为服务起不来。
        """
        if isinstance(value, str) and not value.strip():
            return None
        return value

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


settings = Settings()

__all__ = ["Settings", "settings"]
