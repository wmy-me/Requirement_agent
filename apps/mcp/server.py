from starlette.responses import JSONResponse
from starlette.routing import Route

from src.interfaces.mcp.tools import mcp


async def healthcheck(request):
    return JSONResponse({"status": "ok"})


app = mcp.streamable_http_app()
app.router.routes.append(Route("/health", healthcheck, methods=["GET"]))
