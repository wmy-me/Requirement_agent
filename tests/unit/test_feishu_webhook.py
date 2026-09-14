"""飞书回调的协议实现与 Webhook 端点测试。

关于可信度：解密与签名算法是按飞书官方文档实现的，但**没有官方测试向量**。
这里的密文/签名由测试按文档自行构造（AES-256-CBC、key=SHA256(encrypt_key)、
IV=前 16 字节、PKCS#7；签名=sha256(timestamp+nonce+encrypt_key+原始 body)），
属于自洽验证——能挡住实现走样，但不能替代接上真实飞书应用后的联调。
"""

import base64
import hashlib
import json
import os

import pytest
from cryptography.hazmat.primitives import padding as sym_padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from fastapi.testclient import TestClient

from requirement_agent.api.app import app
from requirement_agent.api.routes import channels as channels_module
from requirement_agent.infrastructure.channels.feishu_client import FeishuClient, FeishuPayloadError

ENCRYPT_KEY = "test-encrypt-key"
VERIFY_TOKEN = "test-verify-token"
WEBHOOK_PATH = "/api/v1/channels/feishu/webhook"


def encrypt_for_test(plaintext: str, encrypt_key: str = ENCRYPT_KEY) -> str:
    """按官方文档构造密文：AES-256-CBC，key=SHA256(encrypt_key)，IV 随机 16 字节。"""
    key = hashlib.sha256(encrypt_key.encode()).digest()
    iv = os.urandom(16)
    padder = sym_padding.PKCS7(128).padder()
    padded = padder.update(plaintext.encode()) + padder.finalize()
    encryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    return base64.b64encode(iv + encryptor.update(padded) + encryptor.finalize()).decode()


def sign_for_test(raw_body: bytes, timestamp: str, nonce: str, encrypt_key: str = ENCRYPT_KEY) -> str:
    """按官方文档构造签名：sha256(timestamp + nonce + encrypt_key + 原始 body)。不是 HMAC。"""
    joined = timestamp + nonce + encrypt_key + raw_body.decode()
    return hashlib.sha256(joined.encode()).hexdigest()


def signed_headers(raw_body: bytes, timestamp: str = "1700000000", nonce: str = "nonce-1") -> dict[str, str]:
    return {
        "X-Lark-Request-Timestamp": timestamp,
        "X-Lark-Request-Nonce": nonce,
        "X-Lark-Signature": sign_for_test(raw_body, timestamp, nonce),
        "Content-Type": "application/json",
    }


# ── 解密 ────────────────────────────────────────────────────────────────


def test_decrypt_recovers_documented_construction() -> None:
    plain = json.dumps({"hello": "世界"}, ensure_ascii=False)
    assert FeishuClient(encrypt_key=ENCRYPT_KEY).decrypt(encrypt_for_test(plain)) == plain


def test_decrypt_with_wrong_key_never_returns_plaintext() -> None:
    """密钥不对时必须报错；极小概率填充恰好合法时，也绝不能还原出原文。

    不能断言「必然抛错」——错密钥解出的末字节是随机的，约 1/256 概率构成合法填充。
    """
    plain = '{"a":1}'
    encrypted = encrypt_for_test(plain, encrypt_key="another-key")

    try:
        result = FeishuClient(encrypt_key=ENCRYPT_KEY).decrypt(encrypted)
    except FeishuPayloadError:
        return
    assert result != plain


def test_unpad_rejects_invalid_padding() -> None:
    """填充校验是确定性的，直接测它的边界值。"""
    with pytest.raises(FeishuPayloadError):
        FeishuClient._unpad(b"")  # 空
    with pytest.raises(FeishuPayloadError):
        FeishuClient._unpad(b"\x01\x02\x03\x00")  # 末字节 0 非法
    with pytest.raises(FeishuPayloadError):
        FeishuClient._unpad(b"\x01\x02\x03\x20")  # 末字节 32 > 分组大小
    with pytest.raises(FeishuPayloadError):
        FeishuClient._unpad(b"\x08")  # 末字节 8 比数据还长
    assert FeishuClient._unpad(b"abc\x01") == b"abc"  # 合法填充照常剥掉
    # 末字节等于长度是合法的整块填充（PKCS#7 允许），别把它当错误
    assert FeishuClient._unpad(b"\x04\x04\x04\x04") == b""


def test_decode_plaintext_envelope() -> None:
    raw = json.dumps({"challenge": "c-1", "type": "url_verification"}).encode()
    assert FeishuClient(encrypt_key=ENCRYPT_KEY).decode(raw)["challenge"] == "c-1"


def test_decode_encrypted_envelope() -> None:
    inner = json.dumps({"challenge": "c-2", "type": "url_verification"})
    raw = json.dumps({"encrypt": encrypt_for_test(inner)}).encode()
    assert FeishuClient(encrypt_key=ENCRYPT_KEY).decode(raw)["challenge"] == "c-2"


def test_decode_encrypted_without_key_raises() -> None:
    raw = json.dumps({"encrypt": encrypt_for_test("{}")}).encode()
    with pytest.raises(FeishuPayloadError):
        FeishuClient(verification_token=VERIFY_TOKEN).decode(raw)


def test_decode_rejects_non_json() -> None:
    with pytest.raises(FeishuPayloadError):
        FeishuClient(encrypt_key=ENCRYPT_KEY).decode(b"not json")


# ── 签名 ────────────────────────────────────────────────────────────────


def test_verify_accepts_valid_signature() -> None:
    raw = b'{"schema":"2.0"}'
    assert FeishuClient(encrypt_key=ENCRYPT_KEY).verify(signed_headers(raw), raw) is True


def test_verify_rejects_tampered_body() -> None:
    """body 被改过则签名对不上——这正是签名要防的。"""
    headers = signed_headers(b'{"schema":"2.0"}')
    assert FeishuClient(encrypt_key=ENCRYPT_KEY).verify(headers, b'{"schema":"2.0","x":1}') is False


def test_verify_rejects_missing_or_unconfigured() -> None:
    raw = b"{}"
    assert FeishuClient(encrypt_key=ENCRYPT_KEY).verify({}, raw) is False
    assert FeishuClient(verification_token=VERIFY_TOKEN).verify(signed_headers(raw), raw) is False


def test_verify_is_case_insensitive_on_header_names() -> None:
    raw = b"{}"
    headers = {k.lower(): v for k, v in signed_headers(raw).items()}
    assert FeishuClient(encrypt_key=ENCRYPT_KEY).verify(headers, raw) is True


# ── Verification Token ──────────────────────────────────────────────────


def test_verify_token_matches_v2_and_v1_location() -> None:
    client = FeishuClient(verification_token=VERIFY_TOKEN)
    assert client.verify_token({"header": {"token": VERIFY_TOKEN}}) is True
    assert client.verify_token({"token": VERIFY_TOKEN}) is True
    assert client.verify_token({"header": {"token": "wrong"}}) is False
    assert client.verify_token({}) is False


def test_verify_token_skipped_when_unset() -> None:
    # 未配置 token 时由签名把关，此时不拦
    assert FeishuClient(encrypt_key=ENCRYPT_KEY).verify_token({}) is True


# ── Webhook 端点 ────────────────────────────────────────────────────────


class FakeIngestService:
    def __init__(self) -> None:
        self.calls: list[object] = []

    def ingest(self, inbound):
        self.calls.append(inbound)
        return {
            "accepted": True,
            "deduplicated": False,
            "queued": True,
            "source_id": 12345,
            "status": "received",
            "channel": inbound.channel,
        }


@pytest.fixture()
def webhook(monkeypatch: pytest.MonkeyPatch):
    client = FeishuClient(verification_token=VERIFY_TOKEN, encrypt_key=ENCRYPT_KEY)
    service = FakeIngestService()
    monkeypatch.setattr(channels_module, "feishu_client", client)
    monkeypatch.setattr(channels_module, "channel_ingest_service", service)
    return TestClient(app), service


def test_webhook_answers_challenge(webhook) -> None:
    client, service = webhook
    raw = json.dumps(
        {"challenge": "ch-42", "type": "url_verification", "token": VERIFY_TOKEN}
    ).encode()

    response = client.post(WEBHOOK_PATH, content=raw)

    assert response.status_code == 200
    assert response.json() == {"challenge": "ch-42"}
    assert service.calls == []  # 校验请求不入库


def test_webhook_answers_encrypted_challenge_without_signature(webhook) -> None:
    """官方文档：URL 校验不适用签名流程——加密的 challenge 不带签名头也要能应答。"""
    client, service = webhook
    inner = json.dumps({"challenge": "ch-enc", "type": "url_verification", "token": VERIFY_TOKEN})
    raw = json.dumps({"encrypt": encrypt_for_test(inner)}).encode()

    response = client.post(WEBHOOK_PATH, content=raw)

    assert response.status_code == 200
    assert response.json() == {"challenge": "ch-enc"}
    assert service.calls == []


def test_webhook_rejects_challenge_with_wrong_token(webhook) -> None:
    client, _service = webhook
    raw = json.dumps({"challenge": "ch", "type": "url_verification", "token": "bad"}).encode()

    assert client.post(WEBHOOK_PATH, content=raw).status_code == 401


def test_webhook_ingests_signed_event(webhook) -> None:
    client, service = webhook
    raw = json.dumps(
        {
            "schema": "2.0",
            "header": {"event_id": "evt-9", "token": VERIFY_TOKEN},
            "event": {"message": {"content": '{"text":"来自飞书的需求"}'}},
        }
    ).encode()

    response = client.post(WEBHOOK_PATH, content=raw, headers=signed_headers(raw))

    assert response.status_code == 200
    assert response.json()["source_id"] == 12345
    assert len(service.calls) == 1
    assert service.calls[0].event_id == "evt-9"
    assert service.calls[0].text == "来自飞书的需求"


def test_webhook_rejects_bad_signature_without_ingesting(webhook) -> None:
    client, service = webhook
    raw = json.dumps(
        {"schema": "2.0", "header": {"event_id": "evt-10", "token": VERIFY_TOKEN}}
    ).encode()
    headers = signed_headers(raw)
    headers["X-Lark-Signature"] = "0" * 64

    assert client.post(WEBHOOK_PATH, content=raw, headers=headers).status_code == 401
    assert service.calls == []


def test_webhook_rejects_event_with_wrong_token(webhook) -> None:
    client, service = webhook
    raw = json.dumps({"schema": "2.0", "header": {"event_id": "e", "token": "bad"}}).encode()

    assert client.post(WEBHOOK_PATH, content=raw, headers=signed_headers(raw)).status_code == 401
    assert service.calls == []


def test_webhook_returns_503_when_channel_unconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    """未配置的渠道端点不能接受任何输入。"""
    monkeypatch.setattr(channels_module, "feishu_client", FeishuClient())

    response = TestClient(app).post(WEBHOOK_PATH, content=b"{}")

    assert response.status_code == 503


def test_webhook_returns_400_on_undecryptable_body(webhook) -> None:
    client, _service = webhook
    raw = json.dumps({"encrypt": "!!!not-base64!!!"}).encode()

    assert client.post(WEBHOOK_PATH, content=raw).status_code == 400
