import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / 'tools'))

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from .config import TEMPLATES as TEMPLATES_DIR, TEXTURE_CATALOG, ensure_runtime_dirs
from .database import init_db
from .i18n import inject_i18n
from .routers import scripts, import_, texts, build, tools, settings, textures

app = FastAPI(
    title="Strawberry Panic Translation Manager",
    version="0.1.0",
)


class I18nMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request.state.i18n = inject_i18n(request)
        response = await call_next(request)
        return response


app.add_middleware(I18nMiddleware)

ensure_runtime_dirs()
app.mount("/catalog", StaticFiles(directory=str(TEXTURE_CATALOG), html=True), name="catalog_static")

app.include_router(scripts.router)
app.include_router(import_.router)
app.include_router(texts.router)
app.include_router(build.router)
app.include_router(tools.router)
app.include_router(settings.router)
app.include_router(textures.router)


@app.on_event("startup")
def on_startup():
    ensure_runtime_dirs()
    init_db()
    Path(TEMPLATES_DIR).parent.mkdir(parents=True, exist_ok=True)


@app.get("/")
def root():
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/scripts")
