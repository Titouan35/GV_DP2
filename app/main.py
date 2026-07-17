"""GV_DP — Générateur de Déclaration Préalable (ombrières PV de parking).

Application FastAPI locale (port 8420), interface HTML servie en statique.
Lancement : `Lancer GV_DP.bat` ou
    .venv/Scripts/python -m uvicorn app.main:app --port 8420
(jamais --reload : le watcher scanne tout le dossier OneDrive.)
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import config
from .api import (
    routes_documents,
    routes_dossier,
    routes_geo,
    routes_insertion,
    routes_planches,
    routes_projets,
)

app = FastAPI(title=config.APP_TITLE, version=config.VERSION)

app.include_router(routes_geo.router)
app.include_router(routes_projets.router)
app.include_router(routes_planches.router)
app.include_router(routes_documents.router)
app.include_router(routes_dossier.router)
app.include_router(routes_insertion.router)

STATIC_DIR = config.REPO_ROOT / "app" / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/api/sante")
def sante():
    return {
        "app": config.APP_NAME,
        "version": config.VERSION,
        "projets_dir": str(config.PROJETS_DIR),
        "coupes_dir_existe": config.COUPES_DIR.exists(),
        "env_charge": str(config.ENV_FILE_CHARGE) if config.ENV_FILE_CHARGE else None,
        "insertion": "api_gemini",
    }


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")
