from src.graph.requirement_graph import RequirementGraph


def test_requirement_graph_commits() -> None:
    graph = RequirementGraph()
    state = graph.run({
        "source_text": "用户登录需要支持手机号登录、短信验证码和权限校验。",
        "source_type": "web",
        "requester_name": "alice",
    })
    assert state.status == "committed"
    assert state.requirement_key
