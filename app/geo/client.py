"""Client HTTP commun pour les APIs open data.

Les APIs (Géoplateforme, API Carto, Géorisques) sont sans SLA : chaque
appel a un timeout et remonte une erreur propre, jamais une exception brute.
"""
from __future__ import annotations

import time

import httpx

from .. import config

# erreurs transitoires typiques des APIs sans SLA : on retente avant d'échouer
_STATUTS_TRANSITOIRES = {429, 502, 503, 504}


class GeoApiError(Exception):
    """Erreur d'appel à une API open data (réseau, HTTP, format)."""

    def __init__(self, service: str, message: str, status: int | None = None):
        self.service = service
        self.status = status
        super().__init__(f"{service} : {message}")


def get_json(service: str, url: str, params: dict | None = None,
             essais: int = 3) -> dict:
    """GET JSON avec gestion d'erreurs uniforme et retries sur transitoire.

    Les APIs open data (Géoplateforme, API Carto, Géorisques) rendent
    régulièrement des 5xx/timeouts ponctuels : 1-2 nouvelles tentatives avec
    un court backoff évitent de remonter une 502 à l'utilisateur pour rien.
    Les erreurs franches (404, 400, réponse non JSON) échouent immédiatement.
    """
    derniere: GeoApiError | None = None
    for tentative in range(max(1, essais)):
        try:
            with httpx.Client(timeout=config.HTTP_TIMEOUT, follow_redirects=True) as client:
                resp = client.get(url, params=params)
        except httpx.HTTPError as exc:
            derniere = GeoApiError(service, f"appel impossible ({exc.__class__.__name__})")
            derniere.__cause__ = exc
        else:
            if resp.status_code == 200:
                try:
                    return resp.json()
                except ValueError as exc:
                    raise GeoApiError(service, "réponse non JSON") from exc
            if resp.status_code not in _STATUTS_TRANSITOIRES:
                raise GeoApiError(service, f"HTTP {resp.status_code}", status=resp.status_code)
            derniere = GeoApiError(service, f"HTTP {resp.status_code}", status=resp.status_code)
        if tentative < essais - 1:
            time.sleep(0.6 * (tentative + 1))
    raise derniere
