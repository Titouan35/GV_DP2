"""API Carto — Cadastre : parcelles par position (suggestion) ou référence.

Géométries en WGS84, ordre [lon, lat]. La contenance (m²) est la surface
cadastrale officielle : c'est elle qu'on affiche, jamais une aire calculée
en degrés.
"""
from __future__ import annotations

import json

from .. import config
from .client import get_json


def _feature_vers_parcelle(feat: dict) -> dict:
    props = feat.get("properties", {})
    code_dep = props.get("code_dep") or ""
    code_com = props.get("code_com") or ""
    return {
        "idu": props.get("idu"),
        "section": props.get("section"),
        "numero": props.get("numero"),
        "com_abs": props.get("com_abs") or "000",
        "code_insee": code_dep + code_com,
        "commune": props.get("nom_com"),
        "contenance_m2": props.get("contenance"),
        "geometry": feat.get("geometry"),
    }


def parcelles_par_position(lon: float, lat: float) -> list[dict]:
    """Parcelle(s) intersectant un point : suggestion depuis l'adresse géocodée."""
    geom = json.dumps({"type": "Point", "coordinates": [lon, lat]})
    data = get_json(
        "API Carto cadastre", config.APICARTO_CADASTRE_URL, {"geom": geom}
    )
    return [_feature_vers_parcelle(f) for f in data.get("features", [])]


def parcelle_par_reference(
    code_insee: str, section: str, numero: str, com_abs: str = "000"
) -> list[dict]:
    """Lookup d'une parcelle saisie à la main (section + numéro)."""
    params = {
        "code_insee": code_insee.strip(),
        "section": section.strip().upper().zfill(2),
        "numero": numero.strip().zfill(4),
        "com_abs": (com_abs or "000").strip().zfill(3),
    }
    data = get_json("API Carto cadastre", config.APICARTO_CADASTRE_URL, params)
    return [_feature_vers_parcelle(f) for f in data.get("features", [])]
