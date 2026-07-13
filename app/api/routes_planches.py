"""Génération et service des planches (DP1 cartes, DP3 coupe, DP4 façades).

Chaque planche est générée à la demande puis mise en cache dans le dossier
assets du projet ; ?regen=1 force la régénération (après modification des
paramètres, l'UI force toujours regen).
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from .. import config
from ..geo.client import GeoApiError
from ..planches import GENERATEURS
from .routes_projets import _charger

router = APIRouter(prefix="/api/projets", tags=["planches"])


def generer_planche(projet_id: str, code: str, regen: bool = False):
    """Génère (ou relit le cache de) la planche `code`. Renvoie le chemin PNG."""
    if code not in GENERATEURS:
        raise HTTPException(status_code=404, detail=f"Planche inconnue : {code}")
    chemin = config.assets_dir(projet_id) / f"{code}.png"
    if chemin.exists() and not regen:
        return chemin
    projet = _charger(projet_id).model_dump()
    try:
        image = GENERATEURS[code](projet)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except GeoApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    image.save(chemin, "PNG")
    return chemin


@router.get("/{projet_id}/planches")
def lister_planches(projet_id: str):
    assets = config.assets_dir(projet_id)
    return {
        "planches": [
            {"code": code, "generee": (assets / f"{code}.png").exists()}
            for code in GENERATEURS
        ]
    }


@router.get("/{projet_id}/planches/{code}.png")
def servir_planche(projet_id: str, code: str, regen: int = 0):
    chemin = generer_planche(projet_id, code, regen=bool(regen))
    return FileResponse(chemin, media_type="image/png")
