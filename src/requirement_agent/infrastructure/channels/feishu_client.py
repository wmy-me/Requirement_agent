"""飞书渠道适配器。

实现飞书事件订阅的 HTTP 回调协议（依据官方文档「Step 3: Receive events」）：

- 配置 Encrypt Key 后回调体是 `{"encrypt": "..."}` 密文，走 AES-256-CBC 解密：
  key = `SHA256(encrypt_key)`，IV 取 base64 解码后的**前 16 字节**，PKCS#7 填充。
- 签名校验为 `sha256(timestamp + nonce + encrypt_key + 原始 body)`，
  与请求头 `X-Lark-Signature` 比对。**注意不是 HMAC**，encrypt_key 是拼进哈希输入的。
- Verification Token 从（解密后的）请求体里取，与配置值比对。

安全默认：`verification_token` 与 `encrypt_key` 都没配置时**拒绝一切请求** ——
未配置的渠道端点不应该接受任意输入。`verify` 未配置 encrypt_key 时同样返回 False。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from typing import Any, Mapping

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from requirement_agent.infrastructure.channels.base import ChannelAdapter, InboundRequirement

_BLOCK_SIZE = 16


class FeishuPayloadError(ValueError):
    """飞书回调载荷无法解析或解密。"""


class FeishuClient(ChannelAdapter):
    """飞书事件订阅适配器。"""

    channel = "feishu"

    def __init__(
        self,
        app_id: str = "",
        app_secret: str = "",
        verification_token: str = "",
        encrypt_key: str = "",
    ) -> None:
        self.app_id = app_id
        self.app_secret = app_secret
        self.verification_token = verification_token
        self.encrypt_key = encrypt_key

    @property
    def is_configured(self) -> bool:
        """至少配置了 verification token 或 encrypt key，才认为渠道可用。"""
        return bool(self.verification_token or self.encrypt_key)

    # ── 解密 ────────────────────────────────────────────────────────────

    def decode(self, raw_body: bytes) -> dict[str, Any]:
        """还原请求体：带 `encrypt` 字段时先解密，否则按 JSON 直接解析。"""
        envelope = self._loads(raw_body)
        if not isinstance(envelope, dict) or "encrypt" not in envelope:
            return envelope
        if not self.encrypt_key:
            raise FeishuPayloadError("收到加密回调，但未配置 FEISHU_ENCRYPT_KEY")
        return self._loads(self.decrypt(str(envelope["encrypt"])).encode("utf-8"))

    @staticmethod
    def _loads(raw: bytes) -> Any:
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise FeishuPayloadError(f"请求体不是合法 JSON：{exc}") from exc

    def decrypt(self, encrypted: str) -> str:
        """AES-256-CBC 解密：key = SHA256(encrypt_key)，IV = 密文前 16 字节。"""
        key = hashlib.sha256(self.encrypt_key.encode("utf-8")).digest()
        try:
            data = base64.b64decode(encrypted)
        except (ValueError, TypeError) as exc:
            raise FeishuPayloadError(f"encrypt 字段不是合法 base64：{exc}") from exc
        if len(data) <= _BLOCK_SIZE:
            raise FeishuPayloadError("密文长度不足一个分组")
        iv, ciphertext = data[:_BLOCK_SIZE], data[_BLOCK_SIZE:]
        decryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
        try:
            plain = decryptor.update(ciphertext) + decryptor.finalize()
        except ValueError as exc:
            raise FeishuPayloadError(f"解密失败：{exc}") from exc
        try:
            return self._unpad(plain).decode("utf-8")
        except UnicodeDecodeError as exc:
            # 密钥不对时，末字节有约 1/256 的概率恰好构成合法填充，于是走到这里。
            # 必须归一成 FeishuPayloadError：路由只捕获它，漏出去就是 500 而非 400。
            raise FeishuPayloadError(f"解密结果不是合法 UTF-8：{exc}") from exc

    @staticmethod
    def _unpad(data: bytes) -> bytes:
        """PKCS#7 去填充。填充非法通常意味着密钥不对，必须报错而非返回半截明文。"""
        if not data:
            raise FeishuPayloadError("解密结果为空")
        pad = data[-1]
        if not 1 <= pad <= _BLOCK_SIZE or pad > len(data):
            raise FeishuPayloadError("填充非法，通常意味着 Encrypt Key 不匹配")
        return data[:-pad]

    # ── 校验 ────────────────────────────────────────────────────────────

    def verify(self, headers: Mapping[str, str], raw_body: bytes) -> bool:
        """签名校验：`sha256(timestamp + nonce + encrypt_key + 原始 body)`。

        `body` 必须是**未反序列化的原始字节**（官方文档明确要求不可反序列化后再算）。
        未配置 encrypt_key 时无从验签，一律拒绝。
        """
        if not self.encrypt_key:
            return False
        signature = self._header(headers, "x-lark-signature")
        timestamp = self._header(headers, "x-lark-request-timestamp")
        nonce = self._header(headers, "x-lark-request-nonce")
        if not (signature and timestamp and nonce):
            return False
        raw = timestamp + nonce + self.encrypt_key + raw_body.decode("utf-8", errors="replace")
        expected = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        return hmac.compare_digest(expected, signature)

    def verify_token(self, payload: dict[str, Any]) -> bool:
        """Verification Token 校验；未配置 token 时视为通过（此时由签名把关）。

        v2 事件把 token 放在 `header.token`，v1 放在顶层 `token`，两者都取。
        """
        if not self.verification_token:
            return True
        token = (payload.get("header") or {}).get("token") or payload.get("token")
        return bool(token) and hmac.compare_digest(str(token), self.verification_token)

    @staticmethod
    def is_challenge(payload: dict[str, Any]) -> bool:
        """是否为「请求地址校验」回调。

        官方文档明确 URL 校验**不适用**签名校验流程（excluding request URL verification），
        所以这条分支必须在验签之前处理；但仍要求 Verification Token 匹配，否则任何人都能
        借它探测/触发响应。
        """
        return payload.get("type") == "url_verification"

    @staticmethod
    def _header(headers: Mapping[str, str], name: str) -> str:
        """大小写不敏感地取请求头（Starlette Headers 与普通 dict 都能用）。"""
        for key, value in headers.items():
            if key.lower() == name:
                return str(value)
        return ""

    # ── 归一化 ──────────────────────────────────────────────────────────

    def parse(self, payload: dict[str, Any]) -> InboundRequirement:
        """把飞书事件载荷归一化为 `InboundRequirement`。

        兼容两种结构：
        - v2.0 事件：`{"schema": "2.0", "header": {...}, "event": {"message": {...}, "sender": {...}}}`
        - 扁平结构：直接给 `text` / `content` 的简化载荷（内部联调与单测用）

        注意：飞书文本消息的 `message.content` 是一段 JSON 字符串（形如 `{"text":"..."}`），
        这里按 JSON 解一层；解不出来就当纯文本。@ 提及的清洗留待需要时再补。
        """
        header = payload.get("header") or {}
        event = payload.get("event") or {}
        message = event.get("message") or {}
        sender = event.get("sender") or {}
        sender_id = sender.get("sender_id") or {}

        raw_text = (
            message.get("content")
            or event.get("text")
            or payload.get("text")
            or payload.get("content")
            or ""
        )
        text = raw_text
        if isinstance(raw_text, dict):
            text = raw_text.get("text") or ""
        elif isinstance(raw_text, str) and raw_text.strip().startswith("{"):
            try:
                decoded = json.loads(raw_text)
            except json.JSONDecodeError:
                decoded = None
            if isinstance(decoded, dict) and decoded.get("text"):
                text = str(decoded["text"])

        return InboundRequirement(
            channel=self.channel,
            text=str(text or ""),
            # v2 的 header.event_id 是幂等键来源（v1 事件放在 event.event_id）
            event_id=str(header.get("event_id") or event.get("event_id") or "") or None,
            requester_id=str(sender_id.get("open_id") or payload.get("user_id") or "") or None,
            requester_name=str(payload.get("user_name") or "") or None,
            payload=payload,
            metadata={
                "schema": payload.get("schema"),
                "event_type": header.get("event_type") or event.get("type"),
                "message_type": message.get("message_type"),
            },
        )
