from __future__ import annotations

import json

from sqlalchemy.engine import URL
from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from requirement_agent.domain.similarity_scale import (
    SimilarityCalibration,
    SimilarityGates,
    relevance,
)


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

    # —— 鉴权（B1）——
    # `API_AUTH_TOKENS` 是**主配置**：JSON `{"token": "role"}`，角色取值见 `api/auth.py`。
    # 每个调用方拿自己的 token，**角色由服务端解析、不可伪造**（这是选它而不是
    # 「单 token + 角色请求头」的原因：后者拿到 token 就能自称 admin）。
    api_auth_tokens: dict[str, str] = Field(default_factory=dict, alias="API_AUTH_TOKENS")
    # `API_AUTH_TOKEN` 保留为**兼容入口**：配置了它就等同于一个 `admin` token。
    # 这样既有的部署（和全部既有测试）不用改就能继续工作。
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

    # —— 相似度判定标尺（B4 批 2）——
    # ⚠️ **这几个数不是拍的**，出自 `scripts/calibrate_similarity.py` 的实跑报告
    # （`docs/baseline/similarity_calibration_<model>_<date>.json`）。
    # 它们只对 `SIMILARITY_CALIBRATION_MODEL` 那个 embedding 模型有效 ——
    # 换模型后必须重跑校准，`similarity_calibration_is_stale()` 会告诉你该不该重测。
    #
    # 判定是**两把锁**：relevance（全局距离，由 baseline 换算）**且**
    # contrast（本次查询内的突出度）。为什么必须是两把，见 `domain/similarity_scale.py`。
    similarity_baseline: float = Field(default=0.7273, ge=0.0, lt=1.0, alias="SIMILARITY_BASELINE")
    similarity_noise_ceiling: float = Field(
        default=0.8120, ge=0.0, le=1.0, alias="SIMILARITY_NOISE_CEILING"
    )
    similarity_contrast_duplicate: float = Field(
        default=0.1385, ge=0.0, alias="SIMILARITY_CONTRAST_DUPLICATE"
    )
    similarity_contrast_related: float = Field(
        default=0.0747, ge=0.0, alias="SIMILARITY_CONTRAST_RELATED"
    )
    similarity_calibration_model: str = Field(
        default="Doubao-embedding", alias="SIMILARITY_CALIBRATION_MODEL"
    )
    similarity_calibration_source: str = Field(
        default="docs/baseline/similarity_calibration_Doubao-embedding_20260917.json",
        alias="SIMILARITY_CALIBRATION_SOURCE",
    )
    # 分析图里检索的候选条数。
    # **不是「越大越好」**：它决定 contrast 统计的样本量，而 contrast 是中位数 ——
    # 样本太少（原值 5）时中位数只用 3~4 个数，噪声很大，实测同一条真重复两次走查
    # 会分别落到 related 与 duplicate。10 是 `RequirementQueryService.MAX_SEARCH_LIMIT`
    # 允许的上限，再大就要动那个上限与向量召回的返回条数。
    similarity_recall_limit: int = Field(
        default=10, ge=1, le=20, alias="SIMILARITY_RECALL_LIMIT"
    )
    # 分析图检索时要不要按渠道过滤。三档：
    #   off  —— 完全不传 filters。与接线前**逐字节一致**，用于对照与回滚。
    #   soft —— **不删候选**，只给每条候选附「与本次来源同渠道吗」（默认）。
    #   hard —— 真过滤。⚠️ 只对显式调用方有意义，分析路径基本不该用，理由见下。
    #
    # ⚠️ **为什么默认 soft 而不是 hard。** 实测四个可过滤维度没有一个能安全用于硬过滤
    # （2026-09-17，库里 12 条来源 / 4 条需求）：
    #   · department / sensitivity_level —— 12/12 全是 NULL，硬过滤返回 **0 条候选**，
    #     于是 analyze 判「独立」→ 一个真重复被静默入库。这是项目最怕的场景。
    #   · source_type —— 12/12 全是 'web'，今天过滤是空操作；等飞书流量进来，
    #     按渠道过滤会**藏掉跨渠道的重复**（同一条需求从两个渠道提，本就该判重复）。
    #   · business_domain —— 有值，但**库里唯一那条真实关系两端 domain 不同**
    #     （REQ-000015 门店巡检 workflow ↔ REQ-000002 报表导出 report）。
    #     硬过滤会把它直接丢掉。
    # soft 只标注、不删候选，所以零漏召回风险；标注本身也让审核人看得到「这条来自别的渠道」。
    similarity_filter_mode: str = Field(default="soft", alias="SIMILARITY_FILTER_MODE")

    # 后台 outbox 消费循环：由 API 进程持续认领 embedding 同步 / 文档分片事件
    outbox_consumer_enabled: bool = Field(default=True, alias="OUTBOX_CONSUMER_ENABLED")
    outbox_poll_interval: float = Field(default=5.0, alias="OUTBOX_POLL_INTERVAL_SECONDS")
    outbox_poll_batch: int = Field(default=50, alias="OUTBOX_POLL_BATCH")
    outbox_stale_timeout_seconds: int = Field(default=300, alias="OUTBOX_STALE_TIMEOUT_SECONDS")
    # —— 失败重试的退避（B5）——
    # 此前失败是**立刻**回 pending，于是 3 次重试全挤在 ~15 秒内（默认轮询 5s × 3），
    # 对「远端 LLM 超时」这类瞬时故障几乎没有恢复窗口。
    # 现在按 `base * 2^(retry_count-1)` 退避，上限 `max`。
    outbox_retry_backoff_seconds: float = Field(
        default=30.0, ge=0.0, alias="OUTBOX_RETRY_BACKOFF_SECONDS"
    )
    outbox_retry_max_backoff_seconds: float = Field(
        default=600.0, ge=0.0, alias="OUTBOX_RETRY_MAX_BACKOFF_SECONDS"
    )

    # 对话运行被判为「僵尸」的静默阈值（秒）：超过它的 running run 会被判为 failed。
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

    @field_validator("api_auth_tokens", mode="before")
    @classmethod
    def _parse_tokens(cls, value: object) -> object:
        """把 `API_AUTH_TOKENS` 从 JSON 字符串解析成 dict。

        写成 JSON 而不是 `token:role,token:role` 这种自定义分隔符，是因为
        token 里可能出现任何字符 —— 自定义格式迟早要被某个含分隔符的 token 咬到。
        """
        if not isinstance(value, str):
            return value
        raw = value.strip()
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"API_AUTH_TOKENS 不是合法 JSON：{exc}") from exc
        if not isinstance(parsed, dict):
            raise ValueError("API_AUTH_TOKENS 必须是 JSON 对象：{\"token\": \"role\"}")
        return {str(k): str(v) for k, v in parsed.items()}

    def require_api_auth(self) -> None:
        if not self.api_auth_token.get_secret_value().strip():
            raise RuntimeError("API_AUTH_TOKEN must be configured for protected API operations")

    # ── 相似度标尺（B4 批 2）────────────────────────────────────────────
    # 适配器放在 settings 而不是领域层：领域层要能脱离配置单测（见
    # `domain/similarity_scale.py` 的模块 docstring），所以由这里做「配置 → 标尺」的换算。

    def similarity_calibration(self) -> SimilarityCalibration:
        """运行期标尺。判定只看 `baseline` 与 `noise_ceiling`，其余是溯源信息。"""
        return SimilarityCalibration(
            baseline=self.similarity_baseline,
            noise_ceiling=self.similarity_noise_ceiling,
            model=self.similarity_calibration_model,
            measured_at="",
            corpus_sha256="",
            separability=None,
        )

    def similarity_gates(self) -> SimilarityGates:
        """基准闸门（strict）。

        三个 relevance 闸门**都由 `noise_ceiling` 换算**、取同一个值 ——
        实测 relevance 分不开级别（真相关的余弦可以低于无关的），给它三个数只是假装。
        级别区分全部由 contrast 承担。按模式缩放走 `SimilarityGates.with_relevance_scaled()`。
        """
        floor = relevance(
            self.similarity_noise_ceiling, self.similarity_calibration()
        )
        return SimilarityGates(
            duplicate_relevance=floor,
            duplicate_contrast=self.similarity_contrast_duplicate,
            related_relevance=floor,
            related_contrast=self.similarity_contrast_related,
            candidate_relevance=floor,
        )

    @field_validator("similarity_filter_mode")
    @classmethod
    def _validate_filter_mode(cls, value: str) -> str:
        """拼错的模式名会被静默当成默认值，那等于配置没生效却没人知道。"""
        normalized = str(value).strip().lower()
        if normalized not in ("off", "soft", "hard"):
            raise ValueError(
                f"SIMILARITY_FILTER_MODE 只能是 off / soft / hard，收到 {value!r}"
            )
        return normalized

    def similarity_calibration_is_stale(self) -> bool:
        """当前 embedding 模型与「阈值是在哪个模型上量的」是否对不上。

        换模型后最容易踩、也最容易被忽略的一脚：阈值看起来还在，判定却已经失准，
        而且**不会报任何错**。调用方据此打醒目告警并在结果里标记。
        """
        current = self.embedding_model.strip().lower()
        measured = self.similarity_calibration_model.strip().lower()
        return bool(measured) and current != measured


settings = Settings()

__all__ = ["Settings", "settings"]
