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

from . import config, securite
from .api import (
    routes_connexion,
    routes_documents,
    routes_dossier,
    routes_geo,
    routes_planches,
    routes_projets,
)

# Contrôle de démarrage : l'application refuse de servir si elle est joignable
# au-delà de cette machine sans authentification. Voir app/securite.py — les
# dossiers contiennent des données personnelles de maîtres d'ouvrage.
MODE_AUTH = securite.verifier_configuration()

app = FastAPI(title=config.APP_TITLE, version=config.VERSION)
app.add_middleware(securite.Authentification)

app.include_router(routes_connexion.router)
app.include_router(routes_geo.router)
app.include_router(routes_projets.router)
app.include_router(routes_planches.router)
app.include_router(routes_documents.router)
app.include_router(routes_dossier.router)

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
        "authentification": MODE_AUTH,
    }


@app.get("/")
def index():
    """Page d'entrée, explicitement NON mise en cache.

    Les fichiers statiques portent un numéro de version dans leur URL
    (app.js?v=30) : c'est ce qui permet de forcer leur rechargement après une
    mise à jour. Mais si le navigateur garde en cache l'index qui PORTE ces
    numéros, il continue de demander l'ancienne version, et l'incrément ne sert
    à rien. Constaté le 01/09/2026 : après le retrait du module Insertion,
    l'interface affichait toujours l'étape supprimée.
    """
    return FileResponse(
        STATIC_DIR / "index.html",
        headers={"Cache-Control": "no-store, must-revalidate"},
    )
