"""CRUD projets : persistance JSON dans PROJETS/ (1 fichier par projet).

Le frontend envoie l'état complet du projet à chaque sauvegarde ; la
réponse renvoie l'évaluation réglementaire (régime + complétude) pour
alimenter le panneau de droite.
"""
from __future__ import annotations

import json
import re
import unicodedata
import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException

from .. import config, regles
from ..models import Projet

router = APIRouter(prefix="/api/projets", tags=["projets"])


def _slug(nom: str) -> str:
    s = unicodedata.normalize("NFKD", nom).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()
    return s[:40] or "projet"


def _chemin(projet_id: str):
    if not re.fullmatch(r"[a-z0-9-]+", projet_id):
        raise HTTPException(status_code=400, detail="Identifiant de projet invalide.")
    return config.PROJETS_DIR / f"{projet_id}.json"


def _sauver(projet: Projet) -> None:
    config.PROJETS_DIR.mkdir(parents=True, exist_ok=True)
    chemin = _chemin(projet.id)
    chemin.write_text(
        projet.model_dump_json(indent=2), encoding="utf-8"
    )


def _charger(projet_id: str) -> Projet:
    chemin = _chemin(projet_id)
    if not chemin.exists():
        raise HTTPException(status_code=404, detail="Projet introuvable.")
    return Projet.model_validate(json.loads(chemin.read_text(encoding="utf-8")))


@router.get("")
def lister_projets():
    config.PROJETS_DIR.mkdir(parents=True, exist_ok=True)
    projets = []
    for f in sorted(config.PROJETS_DIR.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue
        projets.append(
            {
                "id": data.get("id"),
                "nom": data.get("nom"),
                "statut": data.get("statut"),
                "regime": data.get("regime"),
                "commune": (data.get("localisation") or {}).get("commune"),
                "date_modification": data.get("date_modification"),
            }
        )
    projets.sort(key=lambda p: p.get("date_modification") or "", reverse=True)
    return {"projets": projets}


@router.post("")
def creer_projet(projet: Projet):
    projet.id = f"{_slug(projet.nom)}-{uuid.uuid4().hex[:6]}"
    projet.date_creation = datetime.now().isoformat(timespec="seconds")
    projet.date_modification = projet.date_creation
    evaluation = regles.evaluer(projet)
    projet.regime = evaluation["regime"]["regime"]
    _sauver(projet)
    return {"projet": projet, "evaluation": evaluation}


@router.get("/{projet_id}")
def lire_projet(projet_id: str):
    projet = _charger(projet_id)
    return {"projet": projet, "evaluation": regles.evaluer(projet)}


@router.put("/{projet_id}")
def sauvegarder_projet(projet_id: str, projet: Projet):
    if not _chemin(projet_id).exists():
        raise HTTPException(status_code=404, detail="Projet introuvable.")
    projet.id = projet_id
    projet.date_modification = datetime.now().isoformat(timespec="seconds")
    evaluation = regles.evaluer(projet)
    projet.regime = evaluation["regime"]["regime"]
    _sauver(projet)
    return {"projet": projet, "evaluation": evaluation}


@router.delete("/{projet_id}")
def supprimer_projet(projet_id: str):
    chemin = _chemin(projet_id)
    if not chemin.exists():
        raise HTTPException(status_code=404, detail="Projet introuvable.")
    chemin.unlink()
    return {"ok": True}
