"""Client HTTP commun pour les APIs open data.

Les APIs (Géoplateforme, API Carto, Géorisques) sont sans SLA : chaque
appel a un timeout et remonte une erreur propre, jamais une exception brute.
"""
from __future__ import annotations

import httpx

from .. import config


class GeoApiError(Exception):
    """Erreur d'appel à une API open data (réseau, HTTP, format)."""

    def __init__(self, service: str, message: str, status: int | None = None):
        self.service = service
        self.status = status
        super().__init__(f"{service} : {message}")


def get_json(service: str, url: str, params: dict | None = None) -> dict:
    """GET JSON avec gestion d'erreurs uniforme."""
    try:
        with httpx.Client(timeout=config.HTTP_TIMEOUT, follow_redirects=True) as client:
            resp = client.get(url, params=params)
    except httpx.HTTPError as exc:
        raise GeoApiError(service, f"appel impossible ({exc.__class__.__name__})") from exc
    if resp.status_code != 200:
        raise GeoApiError(service, f"HTTP {resp.status_code}", status=resp.status_code)
    try:
        return resp.json()
    except ValueError as exc:
        raise GeoApiError(service, "réponse non JSON") from exc
