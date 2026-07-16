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


def geocoder_inverse(lon: float, lat: float) -> dict | None:
    """Point (lon, lat) → adresse, commune et code INSEE les plus proches.

    Sert quand l'utilisateur déplace le marqueur, clique sur la carte ou saisit
    des coordonnées : on récupère la commune/INSEE pour le zonage et les risques.
    """
    url = config.GEOCODAGE_URL.rsplit("/", 1)[0] + "/reverse"
    data = get_json(
        "Géocodage inverse",
        url,
        {"lon": lon, "lat": lat, "index": "address", "limit": 1},
    )
    feats = data.get("features") or []
    if not feats:
        return None
    props = feats[0].get("properties", {})
    coords = (feats[0].get("geometry") or {}).get("coordinates") or [lon, lat]
    return {
        "label": props.get("label"),
        "code_insee": props.get("citycode"),
        "commune": props.get("city"),
        "code_postal": props.get("postcode"),
        "lon": coords[0],
        "lat": coords[1],
    }
