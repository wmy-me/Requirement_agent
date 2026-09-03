from src.agents.extract_agent import ExtractAgent


def test_extract_agent_basic() -> None:
    agent = ExtractAgent()
    extracted = agent.extract(
        "用户登录需要支持短信验证码和权限校验",
        source_type="web",
        requester_name="alice",
    )
    assert extracted.requirement_title
    assert "登录" in extracted.summary or "验证码" in extracted.summary
    assert extracted.source_type == "web"
    assert extracted.requester_name == "alice"
