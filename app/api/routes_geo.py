"""Routes géo : géocodage, parcelles, urbanisme, risques.

Toutes les APIs open data sont appelées côté serveur (uniformité des
erreurs, pas de dépendance CORS côté navigateur). Une GeoApiError devient
une réponse 502 avec un message affichable dans l'UI.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from ..geo import cadastre, geocode, georisques, gpu
from ..geo.client import GeoApiError

router = APIRouter(prefix="/api", tags=["geo"])


def _proteger(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except GeoApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/geocode")
def api_geocode(q: str = Query(min_length=3), limit: int = 5):
    return {"resultats": _proteger(geocode.rechercher_adresse, q, limit)}


@router.get("/parcelles/suggest")
def api_parcelles_suggest(lon: float, lat: float):
    parcelles = _proteger(cadastre.parcelles_par_position, lon, lat)
    return {"parcelles": parcelles}


@router.get("/parcelles/lookup")
def api_parcelles_lookup(
    insee: str, section: str, numero: str, com_abs: str = "000"
):
    parcelles = _proteger(
        cadastre.parcelle_par_reference, insee, section, numero, com_abs
    )
    if not parcelles:
        raise HTTPException(
            status_code=404,
            detail=f"Parcelle {section} {numero} introuvable sur la commune {insee}.",
        )
    return {"parcelles": parcelles}


@router.get("/urbanisme")
def api_urbanisme(lon: float, lat: float, insee: str | None = None):
    """Zonage PLU + servitudes ABF (+ RNU si code INSEE fourni).

    Ces fonctions gèrent leurs erreurs en interne (flag `disponible`) :
    la couverture GPU est partielle, on n'échoue jamais en bloc.
    """
    reponse = {
        "zonage": gpu.zonage_plu(lon, lat),
        "servitudes": gpu.servitudes_abf(lon, lat),
    }
    if insee:
        reponse["rnu"] = gpu.commune_rnu(insee)
    return reponse


@router.get("/risques")
def api_risques(insee: str):
    return georisques.risques_commune(insee)
