import os

os.environ.setdefault("API_AUTH_TOKEN", "test-api-token")

from fastapi.testclient import TestClient

from apps.api.main import app


client = TestClient(app)


API_HEADERS = {"Authorization": "Bearer test-api-token"}


def test_submit_requirement_api(monkeypatch) -> None:
    from src.config.settings import settings

    monkeypatch.setattr(settings.api_auth_token, "_secret_value", "test-api-token")
    response = client.post(
        "/api/v1/requirements/submit",
        headers=API_HEADERS,
        json={
            "source_type": "web",
            "requester_id": "u-001",
            "requester_name": "alice",
            "original_text": "用户登录支持短信验证码",
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "pending_review"
    assert payload["source_type"] == "web"
