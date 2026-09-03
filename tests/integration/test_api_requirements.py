from fastapi.testclient import TestClient

from apps.api.main import app


client = TestClient(app)


def test_submit_requirement_api() -> None:
    response = client.post(
        "/api/v1/requirements/submit",
        json={
            "source_type": "web",
            "requester_id": "u-001",
            "requester_name": "alice",
            "original_text": "用户登录支持短信验证码",
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "accepted"
    assert payload["source_type"] == "web"
