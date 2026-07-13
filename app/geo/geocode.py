"""Géocodage BAN via la Géoplateforme : adresse → coordonnées + code INSEE.

API : https://data.geopf.fr/geocodage/search (ordre GeoJSON = [lon, lat]).
Piège connu : sur un grand parking, le point géocodé de l'adresse ne tombe
pas forcément sur la parcelle du projet → la sélection des parcelles reste
une suggestion à valider manuellement (cf. PLAN_OUTIL_DP.md §10).
"""
from __future__ import annotations

from .. import config
from .client import get_json


def rechercher_adresse(q: str, limit: int = 5) -> list[dict]:
    """Recherche d'adresse, résultats triés par score décroissant."""
    data = get_json(
        "Géocodage BAN",
        config.GEOCODAGE_URL,
        {"q": q, "limit": limit, "index": "address"},
    )
    resultats = []
    for feat in data.get("features", []):
        props = feat.get("properties", {})
        coords = (feat.get("geometry") or {}).get("coordinates") or [None, None]
        resultats.append(
            {
                "label": props.get("label"),
                "score": props.get("score"),
                "lon": coords[0],
                "lat": coords[1],
                "code_insee": props.get("citycode"),
                "commune": props.get("city"),
                "code_postal": props.get("postcode"),
                "contexte": props.get("context"),
                "type": props.get("type"),
            }
        )
    return resultats
