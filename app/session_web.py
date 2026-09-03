"""Mode web : un espace de travail éphémère par visiteur.

Pourquoi ce mode existe (02/09/2026). Florent voulait une version utilisable en
ligne. Le blocage n'était ni Python, ni LibreOffice, ni l'authentification :
c'était le **disque persistant**. Les dossiers de l'outil sont des fichiers, et
aucune offre d'hébergement gratuite n'offre de volume qui survive à un
redéploiement.

Le mode web retourne le problème. Plutôt que de chercher un disque qui dure, on
supprime le besoin d'en avoir un : rien n'est conservé côté serveur.

  - chaque visiteur reçoit un espace de travail temporaire, isolé, désigné par
    un identifiant signé déposé dans un cookie ;
  - il y crée ses dossiers, dépose ses pièces, produit son PPTX et son Cerfa ;
  - il télécharge ce qu'il a produit, et l'espace est purgé après un délai.

Conséquences, à connaître :

  - un redéploiement efface les sessions en cours. Ce n'est pas une perte de
    données d'archive, c'est une perte de travail en cours, comme fermer un
    onglet. L'interface le dit clairement.
  - il n'y a AUCUN dossier client au repos sur le serveur. C'est ce qui rend
    l'hébergement acceptable là où le mode « suivi de dossier » ne l'était pas.
  - deux personnes ne partagent rien : chacune ne voit que sa session.

Le mode est activé par GVDP_MODE=web. Sans cela, rien ne change au poste.
"""
from __future__ import annotations

import logging
import os
import secrets
import shutil
import tempfile
import time
from pathlib import Path

from starlette.middleware.base import BaseHTTPMiddleware

from . import config, securite

logger = logging.getLogger(__name__)

COOKIE_ESPACE = "gvdp_espace"

# Durée de vie d'un espace inactif. Assez long pour monter un dossier en
# plusieurs fois dans la journée, assez court pour ne rien garder qui traîne.
DUREE_VIE_H = 12

# Purge au plus une fois par intervalle, pour ne pas parcourir le disque à
# chaque requête.
INTERVALLE_PURGE_S = 600
_derniere_purge = 0.0


def actif() -> bool:
    return (os.environ.get("GVDP_MODE") or "").strip().lower() == "web"


def racine() -> Path:
    """Dossier contenant les espaces de session.

    GVDP_WEB_DIR permet de le placer ailleurs (un volume, si l'hébergeur en a
    un). Par défaut, le dossier temporaire du système : parfaitement adapté,
    puisque rien n'est censé survivre.
    """
    valeur = (os.environ.get("GVDP_WEB_DIR") or "").strip()
    base = Path(valeur) if valeur else Path(tempfile.gettempdir()) / "gvdp_sessions"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _identifiant_valide(jeton: str | None) -> str | None:
    """Identifiant porté par un cookie signé, ou None.

    La signature empêche de désigner l'espace d'un autre visiteur en modifiant
    le cookie : sans elle, il suffirait d'essayer des identifiants au hasard.
    """
    if not jeton or "." not in jeton:
        return None
    identifiant, _, _ = jeton.partition(".")
    if not identifiant.isalnum() or not 8 <= len(identifiant) <= 64:
        return None
    return identifiant if securite.verifier_session_brute(jeton) else None


def creer_jeton() -> tuple[str, str]:
    """(identifiant, jeton signé) d'un nouvel espace."""
    identifiant = secrets.token_hex(16)
    return identifiant, securite.signer_brut(identifiant)


def espace_de(identifiant: str) -> Path:
    chemin = racine() / identifiant
    chemin.mkdir(parents=True, exist_ok=True)
    return chemin


def toucher(chemin: Path) -> None:
    """Marque l'espace comme vivant : la purge se fie à cette date."""
    try:
        (chemin / ".vivant").write_text(str(int(time.time())), encoding="utf-8")
    except OSError:
        pass


def _age_heures(chemin: Path) -> float:
    marqueur = chemin / ".vivant"
    try:
        vu = int(marqueur.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        try:
            vu = int(chemin.stat().st_mtime)
        except OSError:
            return 0.0
    return (time.time() - vu) / 3600.0


def purger(force: bool = False) -> int:
    """Supprime les espaces inactifs. Renvoie le nombre d'espaces supprimés."""
    global _derniere_purge
    maintenant = time.time()
    if not force and maintenant - _derniere_purge < INTERVALLE_PURGE_S:
        return 0
    _derniere_purge = maintenant

    supprimes = 0
    try:
        dossiers = list(racine().iterdir())
    except OSError:
        return 0
    for dossier in dossiers:
        if not dossier.is_dir():
            continue
        if _age_heures(dossier) > DUREE_VIE_H:
            shutil.rmtree(dossier, ignore_errors=True)
            supprimes += 1
    if supprimes:
        logger.info("Purge des espaces web : %s supprimé(s)", supprimes)
    return supprimes


class EspaceEphemere(BaseHTTPMiddleware):
    """Attache à chaque requête l'espace de travail de son visiteur."""

    async def dispatch(self, request, call_next):
        if not actif():
            return await call_next(request)

        purger()
        identifiant = _identifiant_valide(request.cookies.get(COOKIE_ESPACE))
        jeton_neuf = None
        if identifiant is None:
            identifiant, jeton_neuf = creer_jeton()

        chemin = espace_de(identifiant)
        toucher(chemin)
        remise = config.definir_espace(chemin)
        try:
            reponse = await call_next(request)
        finally:
            config._ESPACE.reset(remise)

        if jeton_neuf:
            reponse.set_cookie(
                COOKIE_ESPACE, jeton_neuf,
                max_age=DUREE_VIE_H * 3600,
                httponly=True, samesite="lax",
                secure=securite.cookie_securise(request), path="/",
            )
        return reponse
