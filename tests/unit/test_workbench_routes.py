"""工作台路由与 CSP 兼容的前端 token 注入。"""

import asyncio

from requirement_agent.api.app import app


def test_workbench_detail_routes_are_explicitly_registered() -> None:
    def paths_for(routes):
        for route in routes:
            path = getattr(route, "path", None)
            if path:
                yield path
            nested = getattr(route, "routes", None)
            if nested:
                yield from paths_for(nested)

    paths = set(paths_for(app.routes))

    assert "/app/reviews/detail" in paths
    assert "/app/requirements/{requirement_key}" in paths
    assert "/app/analysis/runs/{run_id}" in paths


def test_workbench_token_is_injected_as_csp_safe_meta() -> None:
    route = next(route for route in app.routes if getattr(route, "path", None) == "/app")

    response = asyncio.run(route.endpoint())
    html = response.body.decode("utf-8")

    assert '<meta name="ra-ui-token" content="' in html
    assert "window.RA_UI_TOKEN" not in html
