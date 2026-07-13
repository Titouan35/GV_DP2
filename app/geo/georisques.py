"""Géorisques : synthèse des risques de la commune, pour la notice DP11.

Endpoint principal : /resultats_rapport_risque (synthèse par commune).
Fallback : /gaspar/risques (liste GASPAR brute). Les deux sont parsés
défensivement : structure susceptible d'évoluer, API sans SLA.
"""
from __future__ import annotations

from .. import config
from .client import GeoApiError, get_json


def _depuis_rapport(data: dict) -> list[dict]:
    """Aplati risquesNaturels / risquesTechnologiques en liste de risques présents."""
    risques = []
    for famille_cle, famille_lbl in (
        ("risquesNaturels", "naturel"),
        ("risquesTechnologiques", "technologique"),
    ):
        bloc = data.get(famille_cle)
        if not isinstance(bloc, dict):
            continue
        for cle, val in bloc.items():
            if isinstance(val, dict) and val.get("present"):
                risques.append(
                    {
                        "famille": famille_lbl,
                        "code": cle,
                        "libelle": val.get("libelle") or cle,
                    }
                )
    return risques


def _depuis_gaspar(data: dict) -> list[dict]:
    risques, vus = [], set()
    for item in data.get("data", []):
        lib = item.get("libelle_risque_long") or item.get("libelle_risque")
        if lib and lib not in vus:
            vus.add(lib)
            risques.append({"famille": "gaspar", "code": None, "libelle": lib})
    return risques


def risques_commune(code_insee: str) -> dict:
    """Risques recensés sur la commune (présents uniquement)."""
    try:
        data = get_json(
            "Géorisques",
            f"{config.GEORISQUES_URL}/resultats_rapport_risque",
            {"code_insee": code_insee},
        )
        risques = _depuis_rapport(data)
        return {"disponible": True, "source": "rapport_risque", "risques": risques}
    except GeoApiError:
        pass  # fallback GASPAR
    try:
        data = get_json(
            "Géorisques",
            f"{config.GEORISQUES_URL}/gaspar/risques",
            {"code_insee": code_insee, "page": 1, "page_size": 100},
        )
        return {"disponible": True, "source": "gaspar", "risques": _depuis_gaspar(data)}
    except GeoApiError as exc:
        return {"disponible": False, "erreur": str(exc), "risques": []}
