from requirement_agent.api.app import app


def test_app_exists() -> None:
    assert app is not None
    assert hasattr(app, "routes")
