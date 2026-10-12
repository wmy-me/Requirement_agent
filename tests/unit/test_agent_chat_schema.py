from requirement_agent.api.schemas.agent import AgentChatRequest


def test_chat_request_defaults_to_automatic_web_policy() -> None:
    """Omitted policy must agree with the UI and only permit research intent."""
    request = AgentChatRequest(message="联网查询 Python 官方文档")

    assert request.web_policy == "auto"
