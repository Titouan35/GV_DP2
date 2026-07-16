"""Module Insertion IA : photo du site, zone, génération, retouche, galerie.

Génération synchrone (10-30 s par image) : le front affiche un état
d'attente. La clé API reste côté serveur (jamais transmise au navigateur).
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Body, HTTPException, UploadFile
from fastapi.responses import FileResponse

from .. import config, insertion_ia
from ..insertion_ia import InsertionError
from .routes_projets import _charger, _sauver

router = APIRouter(prefix="/api/projets", tags=["insertion"])

EXTENSIONS = {".png", ".jpg", ".jpeg"}
TAILLE_MAX = 40 * 1024 * 1024


@router.get("/{projet_id}/insertion/statut")
def statut_projet(projet_id: str):
    return insertion_ia.statut()


@router.post("/{projet_id}/insertion/photo")
async def uploader_photo(projet_id: str, fichier: UploadFile):
    ext = Path(fichier.filename or "").suffix.lower()
    if ext not in EXTENSIONS:
        raise HTTPException(status_code=400, detail="Photo au format PNG ou JPG attendue.")
    contenu = await fichier.read()
    if len(contenu) > TAILLE_MAX:
        raise HTTPException(status_code=400, detail="Photo trop volumineuse (40 Mo max).")

    projet = _charger(projet_id)
    dossier = config.assets_dir(projet_id) / "insertion"
    dossier.mkdir(parents=True, exist_ok=True)
    for ancien in dossier.glob("photo_site.*"):
        ancien.unlink()
    chemin = dossier / f"photo_site{ext}"
    chemin.write_bytes(contenu)

    projet.insertion.photo = str(chemin.relative_to(config.PROJETS_DIR)).replace("\\", "/")
    projet.insertion.zone = None  # la zone se retrace sur la nouvelle photo
    projet.date_modification = datetime.now().isoformat(timespec="seconds")
    _sauver(projet)
    return {"projet": projet}


@router.put("/{projet_id}/insertion/reglages")
def enregistrer_reglages(projet_id: str, corps: dict = Body(...)):
    """Zone tracée (0-1), repère d'échelle et consignes libres."""
    projet = _charger(projet_id)
    ins = projet.insertion
    if "zone" in corps:
        zone = corps["zone"]
        if zone is not None and (len(zone) != 4 or not all(0 <= float(v) <= 1 for v in zone)):
            raise HTTPException(status_code=400, detail="Zone invalide (4 valeurs entre 0 et 1).")
        ins.zone = [float(v) for v in zone] if zone else None
    if "repere_distance_m" in corps:
        ins.repere_distance_m = float(corps["repere_distance_m"])
    if "repere_desc" in corps:
        ins.repere_desc = str(corps["repere_desc"])[:200]
    if "consignes" in corps:
        ins.consignes = str(corps["consignes"])[:2000]
    projet.date_modification = datetime.now().isoformat(timespec="seconds")
    _sauver(projet)
    return {"projet": projet}


@router.post("/{projet_id}/insertion/generer")
def generer(projet_id: str, corps: dict = Body(default={})):
    projet = _charger(projet_id)
    nb = int(corps.get("nb_variantes", 2))
    try:
        variantes = insertion_ia.generer(projet.model_dump(), nb_variantes=nb)
    except InsertionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    projet.insertion.variantes = variantes + projet.insertion.variantes
    projet.date_modification = datetime.now().isoformat(timespec="seconds")
    _sauver(projet)
    return {"projet": projet, "variantes": variantes}


@router.post("/{projet_id}/insertion/retoucher")
def retoucher(projet_id: str, corps: dict = Body(...)):
    projet = _charger(projet_id)
    fichier = corps.get("fichier")
    instruction = (corps.get("instruction") or "").strip()
    if not fichier or not instruction:
        raise HTTPException(status_code=400, detail="Variante et instruction requises.")
    try:
        variante = insertion_ia.retoucher(projet.model_dump(), fichier, instruction)
    except InsertionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    projet.insertion.variantes = [variante, *projet.insertion.variantes]
    projet.date_modification = datetime.now().isoformat(timespec="seconds")
    _sauver(projet)
    return {"projet": projet, "variante": variante}


@router.put("/{projet_id}/insertion/retenue")
def choisir_retenue(projet_id: str, corps: dict = Body(...)):
    projet = _charger(projet_id)
    projet.insertion.retenue = corps.get("fichier")
    projet.date_modification = datetime.now().isoformat(timespec="seconds")
    _sauver(projet)
    return {"projet": projet}


@router.get("/{projet_id}/insertion/fichier")
def servir_fichier(projet_id: str, chemin: str):
    """Sert une photo/variante du projet (chemins confinés au projet)."""
    cible = (config.PROJETS_DIR / chemin).resolve()
    base = config.assets_dir(projet_id).resolve()
    if base not in cible.parents or not cible.exists():
        raise HTTPException(status_code=404, detail="Fichier introuvable.")
    return FileResponse(cible)
