"""React 工作台路由、旧前端下线路由与 CSP 兼容的 token 注入。"""

import asyncio

from requirement_agent.api.app import app


def test_only_react_workbench_routes_are_registered() -> None:
    def paths_for(routes):
        for route in routes:
            path = getattr(route, "path", None)
            if path:
                yield path
            nested = getattr(route, "routes", None)
            if nested:
                yield from paths_for(nested)

    paths = set(paths_for(app.routes))

    assert "/app-next" in paths
    assert "/app-next/{path:path}" in paths
    assert "/app" in paths
    assert "/app/{path:path}" in paths
    assert "/app/reviews/detail" not in paths


def test_react_workbench_token_is_injected_as_csp_safe_meta() -> None:
    route = next(route for route in app.routes if getattr(route, "path", None) == "/app-next")

    response = asyncio.run(route.endpoint())
    html = response.body.decode("utf-8")

    assert '<meta name="ra-ui-token" content="' in html
    assert "window.RA_UI_TOKEN" not in html


def test_root_redirects_to_the_only_react_frontend() -> None:
    route = next(route for route in app.routes if getattr(route, "path", None) == "/")
    response = asyncio.run(route.endpoint())

    assert response.status_code == 307
    assert response.headers["location"] == "/app-next"
