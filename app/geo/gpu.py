"""API Carto — GPU (Géoportail de l'urbanisme) : zonage PLU et secteur ABF.

Couverture GPU partielle : chaque fonction renvoie un dict avec un flag
`disponible` pour que l'UI affiche « donnée indisponible » au lieu d'échouer
(cf. PLAN_OUTIL_DP.md §10, fallback prévu).
"""
from __future__ import annotations

import json

from .. import config
from .client import GeoApiError, get_json

# Servitudes qui déclenchent l'avis ABF (bascule DP → PC, plan §2)
ABF_CATEGORIES = {
    "AC1": "Abords des monuments historiques",
    "AC2": "Sites classés ou inscrits",
    "AC4": "Site patrimonial remarquable (SPR)",
}


def _point_geom(lon: float, lat: float) -> str:
    return json.dumps({"type": "Point", "coordinates": [lon, lat]})


def zonage_plu(lon: float, lat: float) -> dict:
    """Zone(s) d'urbanisme au droit du point (libellé, type, document)."""
    try:
        data = get_json(
            "API Carto GPU",
            config.APICARTO_GPU_ZONE_URL,
            {"geom": _point_geom(lon, lat)},
        )
    except GeoApiError as exc:
        return {"disponible": False, "erreur": str(exc), "zones": []}
    zones = []
    for feat in data.get("features", []):
        props = feat.get("properties", {})
        zones.append(
            {
                "libelle": props.get("libelle"),
                "libelong": props.get("libelong"),
                "typezone": props.get("typezone"),
                "idurba": props.get("idurba"),
                "datappro": props.get("datappro"),
            }
        )
    return {"disponible": True, "couvert": bool(zones), "zones": zones}


def servitudes_abf(lon: float, lat: float) -> dict:
    """Servitudes AC1/AC2/AC4 au droit du point → secteur ABF probable.

    Simple pré-alerte : la case « secteur ABF » reste confirmable à la main
    dans l'UI (la donnée GPU peut être incomplète).
    """
    try:
        data = get_json(
            "API Carto GPU",
            config.APICARTO_GPU_SUP_S_URL,
            {"geom": _point_geom(lon, lat)},
        )
    except GeoApiError as exc:
        return {"disponible": False, "erreur": str(exc), "servitudes": [], "abf": None}
    servitudes = []
    for feat in data.get("features", []):
        props = feat.get("properties", {})
        categorie = (props.get("suptype") or props.get("categorie") or "").upper()
        if categorie in ABF_CATEGORIES:
            servitudes.append(
                {
                    "categorie": categorie,
                    "libelle": props.get("libelle") or ABF_CATEGORIES[categorie],
                }
            )
    return {"disponible": True, "servitudes": servitudes, "abf": bool(servitudes)}


def commune_rnu(code_insee: str) -> dict:
    """La commune est-elle au RNU (pas de document d'urbanisme) ?"""
    try:
        data = get_json(
            "API Carto GPU",
            config.APICARTO_GPU_MUNICIPALITY_URL,
            {"insee": code_insee},
        )
    except GeoApiError as exc:
        return {"disponible": False, "erreur": str(exc), "rnu": None}
    feats = data.get("features", [])
    if not feats:
        return {"disponible": True, "rnu": None}
    props = feats[0].get("properties", {})
    return {"disponible": True, "rnu": props.get("is_rnu")}
