import os
import uuid

os.environ.setdefault("API_AUTH_TOKEN", "test-api-token")

from fastapi.testclient import TestClient

from apps.api.main import app


client = TestClient(app)


API_HEADERS = {"Authorization": "Bearer test-api-token"}


def test_submit_requirement_api(monkeypatch) -> None:
    from requirement_agent.config.settings import settings

    monkeypatch.setattr(settings.api_auth_token, "_secret_value", "test-api-token")
    # 唯一 key，避免命中历史幂等记录（持久 DB 可能导致旧源已 committed）
    unique = uuid.uuid4().hex
    response = client.post(
        "/api/v1/requirements/submit",
        headers=API_HEADERS,
        json={
            "source_type": "web",
            "requester_id": f"u-{unique}",
            "requester_name": "alice",
            "original_text": f"接口冒烟-{unique}：希望报表按部门筛选导出",
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "pending_review"
    assert payload["source_type"] == "web"
