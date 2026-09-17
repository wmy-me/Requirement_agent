"""任务级模型路由（B3.1）。

**为什么要有这个模块。** 在它之前，「用哪个模型」由**一个全局开关**决定：
`settings.llm_provider`（deepseek / openai）经三个 if-else 属性
（`active_llm_api_key` / `active_llm_base_url` / `active_llm_model`）作用到**所有**调用。
抽取、关系分析、风险评估、叙述生成、embedding —— 全用同一个模型。

这有几个说不通的地方：

- `extract` 要的是**结构化输出稳定 + 快**，`analyze` 要的是**长上下文 + 关系判断稳**，
  `risk` 偏向**推理与解释**，`narrative` 只要**中文流畅**。它们的取舍本来就不同；
- 想给某一个任务换模型，只能改全局 env —— 于是所有任务一起换；
- 某家网关抖动时，**没有任何备用**，整条分析链路一起挂。

这个模块把「任务 → 模型」变成一张可配置的表，并给出备用链。

## 三级回退（这是本模块最容易搞错的地方）

```
1. MODEL_ROUTES 里这个 task_type 的配置        ← 显式指定
2. 全局 LLM_PROVIDER + *_MODEL                 ← 没配就沿用现状（向后兼容）
3. 空（未配置）                                 ← 调用方降级到启发式规则
```

**第 2 级是关键**：`MODEL_ROUTES` 默认是空的，此时所有任务都解析到全局配置 ——
行为与 B3.1 之前**逐字节一致**。这样这个批次可以独立提交、独立回滚，
不会因为「引入了路由」而改变任何一次实际调用用的模型。

## 支持的 provider 是**有限枚举**，不是任意字符串

`SUPPORTED_PROVIDERS` 之外的名字一律解析成「未配置」，而不是拼一个 URL 去请求。
理由：拼错的 provider 名如果被当成新 provider，会得到一个 base_url 为空的
`LLMProvider` —— 它 `is_configured()` 返回 False，调用方降级到启发式，
**看起来像「没配模型」，而不像「配错了」**。这里让它在解析阶段就归零，
并且 `ModelRegistry` 会把无法识别的 name 记下来供诊断。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Final, Literal

__all__ = [
    "SUPPORTED_PROVIDERS",
    "TASK_TYPES",
    "ModelRegistry",
    "ModelSpec",
    "TaskRoute",
    "TaskType",
]

#: 任务类型。与追加文档 §4.2 的表一一对应。
TASK_TYPES: Final = ("extract", "analyze", "risk", "narrative", "embedding", "vision")
TaskType = Literal["extract", "analyze", "risk", "narrative", "embedding", "vision"]

SUPPORTED_PROVIDERS: Final = ("deepseek", "openai")


@dataclass(frozen=True, slots=True)
class ModelSpec:
    """一个有名字的模型：走哪家网关、用哪个模型名。

    ⚠️ **`provider` 与 `model` 是两个维度**：同一家网关可以有多个模型
    （deepseek-chat / deepseek-reasoner），而同一个模型名也可能挂在两家的兼容网关上。
    记录调用时必须两个都记，否则事后分不清「换了模型」还是「换了网关」。
    """

    provider: str
    model: str

    @property
    def supported(self) -> bool:
        return self.provider in SUPPORTED_PROVIDERS and bool(self.model)


@dataclass(frozen=True, slots=True)
class TaskRoute:
    """一个任务的模型链：`primary` 打头，`fallbacks` 按顺序兜底。"""

    primary: ModelSpec
    fallbacks: tuple[ModelSpec, ...] = field(default_factory=tuple)

    def chain(self) -> tuple[ModelSpec, ...]:
        """主模型在前、备用在后。**去重**：同一对 (provider, model) 出现两次没有意义，
        而重复会让「第几级降级」这个数虚高。"""
        seen: set[tuple[str, str]] = set()
        ordered: list[ModelSpec] = []
        for spec in (self.primary, *self.fallbacks):
            key = (spec.provider, spec.model)
            if key in seen:
                continue
            seen.add(key)
            ordered.append(spec)
        return tuple(ordered)


class ModelRegistry:
    """按 `task_type` 解析要用的模型（及其备用链）。

    不做缓存：`settings` 是进程内单例，解析是一次字典查找 + 几个不可变对象的构造，
    比缓存失效的复杂度便宜。
    """

    def __init__(self, settings_obj: Any | None = None) -> None:
        if settings_obj is None:
            from requirement_agent.config.settings import settings as _settings

            settings_obj = _settings
        self._settings = settings_obj

    # ── 解析 ──────────────────────────────────────────────────────────────

    def resolve(self, task_type: str) -> TaskRoute:
        """任务 → 模型链。**永远返回一个 `TaskRoute`**，不会抛。

        配置缺失/格式错误/名字不认识时，退到全局配置；全局也没配时返回一个
        **未配置的占位**（`model == ""`），由调用方按「未配置」处理
        —— 而不是在这里抛错。理由：模型没配是**降级到启发式的正常路径**
        （见各 Agent 的 `is_configured()` 分支），不是异常。

        ⚠️ `embedding` / `vision` **不走这条路**，它们各有自己的全局兜底
        （见 `get_embedding_model` / `get_vision_model`）—— chat 模型名拿去当
        embedding 模型名会直接 404。
        """
        configured = self._settings.model_routes.get(task_type)
        if configured:
            route = self._parse_route(configured, task_type)
            if route is not None:
                return route
        if task_type == "embedding":
            return TaskRoute(primary=self.get_embedding_model())
        if task_type == "vision":
            return TaskRoute(primary=ModelSpec(provider="", model=""))
        return TaskRoute(primary=self._global_default())

    def get_chat_model(self, task_type: str) -> ModelSpec:
        """该任务的**主**模型（不含备用）。"""
        return self.resolve(task_type).primary

    def get_fallback_chain(self, task_type: str) -> tuple[ModelSpec, ...]:
        """该任务的备用链（**不含主模型**）。主模型失败后按序尝试。"""
        return self.resolve(task_type).chain()[1:]

    def get_embedding_model(self) -> ModelSpec:
        """embedding 用哪个模型。

        ⚠️ **它的全局兜底与 chat 不同**：没配 `MODEL_ROUTES["embedding"]` 时，
        取的是 `EMBEDDING_MODEL`（4096 维的那个），**不是** `active_llm_model` ——
        chat 模型名拿去当 embedding 模型名会直接 404。
        这与 `EmbeddingService` 一直以来的行为一致。

        另注：embedding 有自己的 base_url/key（`EMBEDDING_BASE_URL` /
        `EMBEDDING_API_KEY`），不受 `provider` 字段影响；这里的 `provider`
        只是个标签，真正的凭据解析在 `LLMProvider` 里。
        """
        configured = self._settings.model_routes.get("embedding")
        if configured:
            route = self._parse_route(configured, "embedding")
            if route is not None:
                return route.primary
        return ModelSpec(
            provider=str(self._settings.llm_provider or "").strip().lower(),
            model=str(self._settings.embedding_model or "").strip(),
        )

    def get_vision_model(self) -> ModelSpec:
        """vision 用哪个模型。

        ⚠️ **本系统目前没有任何图像调用**（飞书截图进来先走 OCR 文本化），
        而配置里也**没有任何视觉模型字段**。所以没配 `MODEL_ROUTES["vision"]` 时
        返回的是一个 **`supported == False` 的占位**，而不是像其它任务那样
        退到全局 chat 模型 —— 假装「chat 模型就是视觉模型」会让将来接图片理解的人
        以为它已经配好了。

        保留这个接口是因为视觉模型在追加实施文档 §4.2 的能力表里，
        将来接图片理解时要有统一的取法。
        """
        configured = self._settings.model_routes.get("vision")
        if configured:
            route = self._parse_route(configured, "vision")
            if route is not None:
                return route.primary
        return ModelSpec(provider="", model="")

    # ── 解析细节 ──────────────────────────────────────────────────────────

    def _global_default(self) -> ModelSpec:
        """第 2 级回退：全局 `LLM_PROVIDER` + 对应模型名。

        `active_llm_model` 在 provider 不认识时返回空串 —— 那种情况下这个 `ModelSpec`
        的 `supported` 是 False，调用方会走「未配置」分支。这与 B3.1 之前
        `LLMProvider.is_configured()` 为 False 的行为一致。
        """
        return ModelSpec(
            provider=str(self._settings.llm_provider or "").strip().lower(),
            model=str(self._settings.active_llm_model or "").strip(),
        )

    @staticmethod
    def _parse_route(raw: Any, task_type: str) -> TaskRoute | None:
        """解析一条路由配置。**格式不对就返回 None**（退到全局），不抛。

        `MODEL_ROUTES` 是**手写 JSON**，写错一个键名是常事。抛错会让服务起不来，
        而「这个任务的模型没配上，走全局」是一个完全可接受的降级 —— 前提是
        **它要被记为未识别**，而不是静默。未识别的条目由 `unrecognized()` 报出来。
        """
        if not isinstance(raw, dict):
            return None
        primary = _parse_spec(raw)
        if primary is None:
            return None
        fallbacks: list[ModelSpec] = []
        for item in raw.get("fallbacks") or []:
            spec = _parse_spec(item)
            if spec is not None:
                fallbacks.append(spec)
        return TaskRoute(primary=primary, fallbacks=tuple(fallbacks))

    def unrecognized(self) -> dict[str, str]:
        """诊断用：配置了但**没被采用**的任务 → 原因。

        「我明明配了 analyze 的模型，怎么没生效」—— 没有这个的话只能靠读代码猜。
        """
        problems: dict[str, str] = {}
        for task_type, raw in (self._settings.model_routes or {}).items():
            if task_type not in TASK_TYPES:
                problems[task_type] = f"未知的 task_type（合法值：{'/'.join(TASK_TYPES)}）"
                continue
            if not isinstance(raw, dict):
                problems[task_type] = "配置必须是对象，形如 {\"provider\":…,\"model\":…}"
                continue
            if _parse_spec(raw) is None:
                problems[task_type] = (
                    "缺 provider 或 model，或 provider 不在 "
                    f"{'/'.join(SUPPORTED_PROVIDERS)} 里"
                )
        return problems


def _parse_spec(raw: Any) -> ModelSpec | None:
    """从一段配置里取一个 `ModelSpec`；取不到返回 None。"""
    if not isinstance(raw, dict):
        return None
    provider = str(raw.get("provider") or "").strip().lower()
    model = str(raw.get("model") or "").strip()
    if provider not in SUPPORTED_PROVIDERS or not model:
        return None
    return ModelSpec(provider=provider, model=model)
