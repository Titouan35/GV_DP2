"""Verrou d'ouverture coopératif entre postes (mode partagé, 20/07/2026).

En mode partagé, chaque poste exécute son propre serveur sur un dossier
projets commun (OneDrive/SharePoint). Les verrous internes de l'application
sérialisent les écritures d'UN processus : ils ne voient pas les autres
postes. Sans garde-fou, deux personnes qui ouvrent le même dossier produisent
une « copie de conflit » OneDrive et du travail perdu.

On applique donc la même mécanique que Word ou Excel sur SharePoint : à
l'ouverture, un petit fichier de présence est déposé à côté du projet. Les
autres postes le voient et affichent « ouvert par ... », en gardant la
possibilité de prendre la main en connaissance de cause.

Limite assumée : le fichier de présence subit la latence de synchronisation
d'OneDrive (quelques secondes). Deux ouvertures simultanées à la seconde près
peuvent passer entre les mailles ; le verrou optimiste sur date_modification
reste le filet de sécurité en dessous.
"""
from __future__ import annotations

import json
import os
import socket
from datetime import datetime, timedelta
from pathlib import Path

from . import config

# au-delà, la présence est considérée comme abandonnée (poste éteint, onglet
# fermé brutalement). Le client rafraîchit toutes les 5 min.
DUREE_VIE = timedelta(minutes=15)


def _chemin(projet_id: str) -> Path:
    return config.PROJETS_DIR / f"{projet_id}.lock"


def _machine() -> str:
    try:
        return socket.gethostname()
    except OSError:
        return "?"


def lire(projet_id: str) -> dict | None:
    """Présence en cours sur ce projet, ou None (absente ou périmée)."""
    try:
        données = json.loads(_chemin(projet_id).read_text(encoding="utf-8"))
        vu = datetime.fromisoformat(données["date"])
    except (OSError, ValueError, KeyError):
        return None
    if datetime.now() - vu > DUREE_VIE:
        return None                      # abandonnée : le projet est libre
    return données


def poser(projet_id: str, utilisateur: str | None) -> dict:
    """Prend ou rafraîchit la présence, et renvoie l'état du verrou.

    {detenteur, machine, depuis, a_moi} — `a_moi` est faux quand un autre
    poste détient le projet : l'appelant affiche alors un avertissement.
    """
    moi = utilisateur or os.environ.get("USERNAME") or "?"
    existant = lire(projet_id)
    if existant and existant.get("utilisateur") != moi:
        return {"detenteur": existant.get("utilisateur"),
                "machine": existant.get("machine"),
                "depuis": existant.get("depuis", existant.get("date")),
                "a_moi": False}

    depuis = (existant or {}).get("depuis") if existant else None
    maintenant = datetime.now().isoformat(timespec="seconds")
    données = {"utilisateur": moi, "machine": _machine(),
               "date": maintenant, "depuis": depuis or maintenant}
    try:
        config.PROJETS_DIR.mkdir(parents=True, exist_ok=True)
        tmp = _chemin(projet_id).with_suffix(".lock.tmp")
        tmp.write_text(json.dumps(données), encoding="utf-8")
        os.replace(tmp, _chemin(projet_id))
    except OSError:
        pass        # dossier momentanément verrouillé par la synchro : sans gravité
    return {"detenteur": moi, "machine": données["machine"],
            "depuis": données["depuis"], "a_moi": True}


def liberer(projet_id: str, utilisateur: str | None) -> None:
    """Libère la présence si elle nous appartient (jamais celle d'un autre)."""
    existant = lire(projet_id)
    moi = utilisateur or os.environ.get("USERNAME") or "?"
    if existant and existant.get("utilisateur") != moi:
        return
    _chemin(projet_id).unlink(missing_ok=True)


def forcer(projet_id: str, utilisateur: str | None) -> dict:
    """Prend la main malgré une présence tierce (choix explicite de l'utilisateur)."""
    _chemin(projet_id).unlink(missing_ok=True)
    return poser(projet_id, utilisateur)
