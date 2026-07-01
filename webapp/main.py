#!/usr/bin/env python3
"""FastAPI main application for Strawberry Panic Translation Manager."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / 'tools'))

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .config import TEMPLATES as TEMPLATES_DIR
from .database import init_db
from .routers import scripts, import_, texts, build, tools

app = FastAPI(
    title="Strawberry Panic Translation Manager",
    version="0.1.0",
)

app.include_router(scripts.router)
app.include_router(import_.router)
app.include_router(texts.router)
app.include_router(build.router)
app.include_router(tools.router)


@app.on_event("startup")
def on_startup():
    """Inicializa la DB al arrancar."""
    init_db()
    Path(TEMPLATES_DIR).parent.mkdir(parents=True, exist_ok=True)


@app.get("/")
def root():
    """Redirige al dashboard."""
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/scripts")
