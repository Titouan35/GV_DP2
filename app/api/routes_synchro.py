"""Synchronisation avec le dossier PROJETS du poste (mode web, « dossier lié »).

Pourquoi (20/11/2026). Le mode web rend l'hébergement gratuit possible parce
que rien n'y est conservé. Revers : un onglet fermé, une mise en veille de
l'hébergeur ou un redémarrage, et le travail en cours est perdu. Le bureau
d'études voulait l'outil en ligne SANS stockage côté serveur, avec les
dossiers sur les postes.

Le navigateur sait, avec l'accord de l'utilisateur, lire et écrire dans un
dossier du poste (Edge et Chrome). L'utilisateur y désigne son dossier
PROJETS, typiquement le dossier OneDrive partagé : c'est lui la RÉFÉRENCE.
Le serveur n'en garde qu'une copie de travail, dans l'espace éphémère de la
session :

  1. à l'ouverture d'un projet, le navigateur envoie ses fichiers, seulement
     ceux qui diffèrent de la copie serveur (comparés par taille et date) ;
  2. après chaque écriture du serveur, il relit l'état, rapatrie ce qui a
     changé et l'écrit dans le dossier du poste ;
  3. la copie serveur disparaît avec l'espace (12 h d'inactivité, veille,
     redémarrage). Le navigateur la renvoie si elle manque.

La liste des projets se calcule sans rien écrire sur le serveur : le
navigateur envoie les fiches JSON, le serveur rend les lignes de la liste.

Ces routes n'élargissent pas ce qu'un visiteur atteint : tout se passe dans
SON espace (config.espace()), et chaque chemin est confiné aux fichiers d'UN
projet (`<id>.json`, `<id>.lock`, `<id>.assets/...`). Elles n'existent qu'en
mode web : au poste, l'espace EST le vrai dossier PROJETS, et une suppression
« miroir » y effacerait des fichiers réels.
"""
from __future__ import annotations

import json
import os
import re
import secrets
from pathlib import Path

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse

from .. import config, session_web
from ..models import Projet
from .routes_projets import resume_projet, verrou_projet

router = APIRouter(prefix="/api/synchro", tags=["synchro"])

# Une pièce déposée est limitée à 40 Mo (routes_documents), mais le dossier
# contient aussi ce que l'outil produit : le PPTX assemblé d'un projet réel
# pèse 6 Mo, une FVE 28 Mo. La marge couvre les deux sans laisser un envoi
# remplir le disque de l'hébergeur.
TAILLE_MAX = 150 * 1024 * 1024

# Au-delà, une liste de projets n'est plus une liste mais un envoi massif.
NB_MAX_RESUMES = 1000

_ID = re.compile(r"[a-z0-9-]+")


def ignore(nom: str) -> bool:
    """Fichiers qui ne font pas partie d'un projet : jamais synchronisés.

    Écritures en cours (`.tmp`), verrous Office (`~$`), fichiers système de
    Windows et de macOS que l'Explorateur ou le Finder déposent d'eux-mêmes.
    Le navigateur applique la même règle (dossier_lie.js).
    """
    bas = nom.lower()
    return (bas.endswith(".tmp") or nom.startswith("~$")
            or bas in ("desktop.ini", "thumbs.db", ".ds_store"))


def _exiger_web() -> None:
    if not session_web.actif():
        raise HTTPException(status_code=404,
                            detail="Synchronisation disponible en mode web uniquement.")


def _verifier_id(projet_id: str) -> None:
    if not _ID.fullmatch(projet_id or ""):
        raise HTTPException(status_code=400, detail="Identifiant de projet invalide.")


def cible(projet_id: str, chemin: str) -> Path:
    """Chemin serveur d'un fichier du projet, ou 400.

    Seuls trois emplacements sont admis : la fiche `<id>.json`, la présence
    `<id>.lock` et tout ce qui vit sous `<id>.assets/`. Le chemin arrive du
    navigateur : il est contrôlé partie par partie PUIS une fois résolu, pour
    qu'aucune combinaison (`..`, lecteur Windows, lien) ne sorte de l'espace.
    """
    _verifier_id(projet_id)
    refus = HTTPException(status_code=400, detail="Chemin de fichier refusé.")
    if not isinstance(chemin, str) or not chemin or len(chemin) > 400:
        raise refus
    if any(c in chemin for c in ("\\", "\x00", ":")):
        raise refus
    parties = chemin.split("/")
    if any(p in ("", ".", "..") for p in parties) or ignore(parties[-1]):
        raise refus
    if len(parties) == 1:
        if parties[0] not in (f"{projet_id}.json", f"{projet_id}.lock"):
            raise refus
    elif parties[0] != f"{projet_id}.assets":
        raise refus

    base = config.espace()
    base.mkdir(parents=True, exist_ok=True)
    chemin_final = base.joinpath(*parties)
    try:
        racine = base.resolve()
        resolu = chemin_final.resolve()
    except (OSError, ValueError) as exc:
        raise refus from exc
    if racine not in resolu.parents:
        raise refus
    return chemin_final


def _entree(base: Path, fichier: Path) -> dict:
    st = fichier.stat()
    return {"chemin": fichier.relative_to(base).as_posix(),
            "taille": st.st_size,
            "mtime": st.st_mtime_ns // 1_000_000}


def etat(projet_id: str) -> list[dict]:
    """Fichiers du projet présents dans l'espace, avec taille et date (ms)."""
    _verifier_id(projet_id)
    base = config.espace()
    fichiers = []
    for nom in (f"{projet_id}.json", f"{projet_id}.lock"):
        chemin = base / nom
        if chemin.is_file():
            fichiers.append(_entree(base, chemin))
    assets = base / f"{projet_id}.assets"
    if assets.is_dir():
        for chemin in sorted(assets.rglob("*")):
            if chemin.is_file() and not ignore(chemin.name):
                fichiers.append(_entree(base, chemin))
    return fichiers


def _horodater(chemin: Path, mtime_ms: int | None) -> None:
    if mtime_ms is None:
        return
    ns = int(mtime_ms) * 1_000_000
    os.utime(chemin, ns=(ns, ns))


def _installer(projet_id: str, tmp: Path, destination: Path, mtime_ms: int | None) -> None:
    """Met le fichier reçu en place, sous le verrou du projet.

    Une fiche JSON est validée avant d'entrer : un fichier qui ne serait pas
    CE projet (identifiant différent, copie de conflit renommée, contenu
    illisible) ferait échouer toutes les routes qui le chargent ensuite.
    """
    if destination.name == f"{projet_id}.json":
        try:
            data = json.loads(tmp.read_text(encoding="utf-8"))
            Projet.model_validate(data)
        except (ValueError, OSError) as exc:
            raise HTTPException(
                status_code=422,
                detail=f"Fiche projet illisible ({exc.__class__.__name__}).") from exc
        if data.get("id") != projet_id:
            raise HTTPException(
                status_code=422,
                detail="La fiche ne correspond pas à ce projet (identifiant différent).")
    with verrou_projet(projet_id):
        os.replace(tmp, destination)
        _horodater(destination, mtime_ms)


# ------------------------------------------------------------------ routes

@router.post("/resumes")
def resumes(corps: dict = Body(...)):
    """Lignes de la liste des projets, calculées sans rien écrire.

    Le corps porte le contenu des fiches JSON lues dans le dossier du poste ;
    la réponse rend, dans le même ordre, la ligne de chacune (ou None si le
    contenu n'est pas un projet lisible).
    """
    _exiger_web()
    projets = corps.get("projets")
    if not isinstance(projets, list) or len(projets) > NB_MAX_RESUMES:
        raise HTTPException(status_code=400, detail="Liste de projets invalide.")
    return {"resumes": [resume_projet(p) for p in projets]}


@router.get("/{projet_id}")
def lire_etat(projet_id: str):
    _exiger_web()
    return {"fichiers": etat(projet_id)}


@router.put("/{projet_id}/fichier")
async def recevoir(projet_id: str, request: Request, chemin: str, mtime: int | None = None):
    """Reçoit UN fichier du dossier du poste et le place dans l'espace.

    Le corps est le contenu brut du fichier, lu par morceaux : un dépassement
    est refusé dès qu'il se produit, sans tout garder en mémoire (512 Mo sur
    l'offre gratuite). `mtime` est la date du fichier sur le poste : la copie
    serveur la reprend, ce qui permet à l'ouverture suivante de ne renvoyer
    que ce qui a changé.
    """
    _exiger_web()
    destination = cible(projet_id, chemin)
    destination.parent.mkdir(parents=True, exist_ok=True)
    tmp = destination.with_name(f"{destination.name}.{secrets.token_hex(4)}.tmp")
    total = 0
    try:
        with open(tmp, "wb") as f:
            async for bloc in request.stream():
                total += len(bloc)
                if total > TAILLE_MAX:
                    raise HTTPException(
                        status_code=413,
                        detail=f"Fichier trop volumineux ({TAILLE_MAX // 1_048_576} Mo max) : {chemin}")
                f.write(bloc)
        await run_in_threadpool(_installer, projet_id, tmp, destination, mtime)
    finally:
        tmp.unlink(missing_ok=True)
    return _entree(config.espace(), destination)


@router.get("/{projet_id}/fichier")
def envoyer(projet_id: str, chemin: str):
    _exiger_web()
    source = cible(projet_id, chemin)
    if not source.is_file():
        raise HTTPException(status_code=404, detail="Fichier absent de l'espace.")
    return FileResponse(source, headers={"Cache-Control": "no-store"})


@router.delete("/{projet_id}/fichier")
def oublier(projet_id: str, chemin: str):
    """Retire un fichier de la COPIE serveur (absent du dossier du poste)."""
    _exiger_web()
    chemin_serveur = cible(projet_id, chemin)
    with verrou_projet(projet_id):
        chemin_serveur.unlink(missing_ok=True)
    return {"ok": True}


@router.post("/{projet_id}/horodatage")
def horodater(projet_id: str, corps: dict = Body(...)):
    """Aligne la date des copies serveur sur celle des fichiers du poste.

    Après avoir écrit sur le poste un fichier produit par le serveur, le
    navigateur connaît sa date locale ; la reporter ici évite de le renvoyer
    pour rien à la prochaine ouverture.

    Chaque entrée porte la version que le navigateur a LUE (`taille`,
    `mtime`) et la date à reporter (`vers`). La date n'est reportée que si la
    copie serveur est encore cette version : si le serveur l'a réécrite
    entre-temps, lui donner l'ancienne date masquerait la nouvelle version,
    qui ne serait jamais rapatriée sur le poste.
    """
    _exiger_web()
    dates = corps.get("dates")
    if not isinstance(dates, dict):
        raise HTTPException(status_code=400, detail="Dates invalides.")
    base = config.espace()
    sortie = []
    with verrou_projet(projet_id):
        for chemin, entree in dates.items():
            chemin_serveur = cible(projet_id, chemin)
            if not isinstance(entree, dict) or not chemin_serveur.is_file():
                continue
            vers = entree.get("vers")
            if not isinstance(vers, int) or isinstance(vers, bool):
                continue
            actuel = _entree(base, chemin_serveur)
            if (actuel["taille"], actuel["mtime"]) != (entree.get("taille"), entree.get("mtime")):
                continue          # réécrite depuis la lecture : on n'y touche pas
            _horodater(chemin_serveur, vers)
            sortie.append(_entree(base, chemin_serveur))
    return {"fichiers": sortie}
