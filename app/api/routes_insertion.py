"""Étape 4 — Insertion IA : génération directe via l'API Gemini.

Endpoints : upload/choix des photos du site, consignes, génération d'une
insertion, sélection des images incluses au dossier DP, téléchargement JPEG,
compteur de dépense local, et fiche de validation d'emprise (PPTX).
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Body, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from .. import config, insertion_ia
from ..fiche_emprise import generer_fiche
from ..insertion_ia import InsertionError
from .routes_projets import _charger, _sauver

router = APIRouter(prefix="/api/projets", tags=["insertion"])

EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
TAILLE_MAX = 40 * 1024 * 1024


# ------------------------------------------------------------------ statut

@router.get("/{projet_id}/insertion/statut")
def statut_projet(projet_id: str):
    projet = _charger(projet_id)
    return {
        **insertion_ia.apercu(),
        "etat": insertion_ia.etat(projet.model_dump()),
        "images_projet": projet.insertion.nb_images_generees,
    }


# ------------------------------------------------------------------ photo du site

@router.post("/{projet_id}/insertion/photos")
async def uploader_photos(projet_id: str, fichiers: list[UploadFile] = File(...)):
    """Dépose une ou plusieurs photos du site (base des insertions)."""
    projet = _charger(projet_id)
    dossier = config.assets_dir(projet_id) / "insertion"
    dossier.mkdir(parents=True, exist_ok=True)
    ajout = 0
    for fichier in fichiers:
        ext = Path(fichier.filename or "").suffix.lower()
        if ext not in EXTENSIONS:
            continue
        contenu = await fichier.read()
        if len(contenu) > TAILLE_MAX:
            continue
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]
        chemin = dossier / f"site_{stamp}{ext}"
        chemin.write_bytes(contenu)
        rel = str(chemin.relative_to(config.PROJETS_DIR)).replace("\\", "/")
        projet.insertion.photos.append(rel)
        ajout += 1
    if not ajout:
        raise HTTPException(status_code=400, detail="Aucune image valide (PNG/JPG/WEBP, 40 Mo max).")
    if not projet.insertion.photo:
        projet.insertion.photo = projet.insertion.photos[0]
    projet.date_modification = datetime.now().isoformat(timespec="seconds")
    _sauver(projet)
    return {"projet": projet}


@router.put("/{projet_id}/insertion/photo-active")
def photo_active(projet_id: str, corps: dict = Body(...)):
    """Choisit la photo du site sur laquelle générer l'insertion."""
    projet = _charger(projet_id)
    chemin = corps.get("chemin")
    if chemin not in projet.insertion.photos:
        raise HTTPException(status_code=400, detail="Photo inconnue.")
    projet.insertion.photo = chemin
    projet.date_modification = datetime.now().isoformat(timespec="seconds")
    _sauver(projet)
    return {"projet": projet}


@router.delete("/{projet_id}/insertion/photo")
def supprimer_photo(projet_id: str, chemin: str):
    projet = _charger(projet_id)
    projet.insertion.photos = [p for p in projet.insertion.photos if p != chemin]
    if projet.insertion.photo == chemin:
        projet.insertion.photo = projet.insertion.photos[0] if projet.insertion.photos else None
    cible = (config.PROJETS_DIR / chemin).resolve()
    base = config.assets_dir(projet_id).resolve()
    if base in cible.parents and cible.exists():
        cible.unlink()
    projet.date_modification = datetime.now().isoformat(timespec="seconds")
    _sauver(projet)
    return {"projet": projet}


@router.get("/{projet_id}/insertion/photos-disponibles")
def photos_disponibles(projet_id: str):
    """Photos du site déposées + photos réutilisables des Pièces BE (DP7/DP8/DP6)."""
    projet = _charger(projet_id)
    photos = [
        {"chemin": p,
         "url": f"/api/projets/{projet_id}/insertion/fichier?chemin={quote(p)}"}
        for p in projet.insertion.photos
    ]
    libelles = {"dp7": "DP7 · proche", "dp8": "DP8 · lointain", "dp6": "DP6"}
    reutil = []
    for code, libelle in libelles.items():
        doc = (projet.documents or {}).get(code)
        if doc and Path(doc.get("fichier", "")).suffix.lower() in EXTENSIONS:
            reutil.append({"code": code, "libelle": libelle,
                           "url": f"/api/projets/{projet_id}/documents/{code}/image"})
    return {"photos": photos, "reutilisables": reutil, "active": projet.insertion.photo}


@router.put("/{projet_id}/insertion/photo-piece")
def photo_depuis_piece(projet_id: str, corps: dict = Body(...)):
    """Reprend une photo des Pièces BE (DP7/DP8/DP6) dans les photos du site."""
    code = corps.get("code")
    projet = _charger(projet_id)
    doc = (projet.documents or {}).get(code)
    if not doc or Path(doc.get("fichier", "")).suffix.lower() not in EXTENSIONS:
        raise HTTPException(status_code=400, detail="Pièce image introuvable.")
    if doc["fichier"] not in projet.insertion.photos:
        projet.insertion.photos.append(doc["fichier"])
    projet.insertion.photo = doc["fichier"]
    projet.date_modification = datetime.now().isoformat(timespec="seconds")
    _sauver(projet)
    return {"projet": projet}


# ------------------------------------------------------------------ consignes


@router.put("/{projet_id}/insertion/consignes")
def enregistrer_consignes(projet_id: str, corps: dict = Body(...)):
    projet = _charger(projet_id)
    if "consignes" in corps:
        projet.insertion.consignes = str(corps["consignes"])[:2000]
    if "affinage" in corps:
        projet.insertion.affinage = str(corps["affinage"])[:2000]
    if "echelle_desc" in corps:
        projet.insertion.echelle_desc = str(corps["echelle_desc"])[:200]
    if "echelle_distance_m" in corps:
        try:
            projet.insertion.echelle_distance_m = float(corps["echelle_distance_m"]) or None
        except (TypeError, ValueError):
            projet.insertion.echelle_distance_m = None
    projet.date_modification = datetime.now().isoformat(timespec="seconds")
    _sauver(projet)
    return {"projet": projet}


# ------------------------------------------------------------------ génération (Gemini)

@router.post("/{projet_id}/insertion/generer")
def generer_insertion(projet_id: str, corps: dict = Body(default={})):
    """Génère UNE insertion via l'API Gemini (Nano Banana). ~10-30 s, synchrone."""
    projet = _charger(projet_id)
    affinage = str(corps.get("affinage", "") or "")
    if affinage:
        projet.insertion.affinage = affinage[:2000]
    try:
        image = insertion_ia.generer_image(projet.model_dump(), affinage=affinage)
    except InsertionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    projet.insertion.images = [image, *projet.insertion.images]
    if not projet.insertion.retenue:
        projet.insertion.retenue = image["fichier"]
    projet.insertion.prompt = image.get("prompt")
    projet.insertion.nb_images_generees += 1
    images_global = insertion_ia.incrementer_compteur_global()
    projet.date_modification = datetime.now().isoformat(timespec="seconds")
    _sauver(projet)
    return {"projet": projet, "image": image,
            "images_projet": projet.insertion.nb_images_generees,
            "images_global": images_global}


# ------------------------------------------------------------------ guides photo

@router.put("/{projet_id}/insertion/guides")
def sauver_guides(projet_id: str, corps: dict = Body(...)):
    """Guides tracés sur une photo : emprises (quadrilatères) + calibrage.

    Coordonnées normalisées 0-1. Corps : {photo, emprises, calibrage|null}.
    """
    projet = _charger(projet_id)
    photo = corps.get("photo")
    if photo not in (projet.insertion.photos or []):
        raise HTTPException(status_code=400, detail="Photo inconnue.")

    def _point(p):
        try:
            x, y = float(p[0]), float(p[1])
        except (TypeError, ValueError, IndexError):
            raise HTTPException(status_code=400, detail="Point de guide invalide.")
        return [min(1.0, max(0.0, x)), min(1.0, max(0.0, y))]

    emprises = [[_point(p) for p in e] for e in (corps.get("emprises") or []) if len(e) >= 3]
    calibrage = corps.get("calibrage") or None
    if calibrage:
        try:
            calibrage = {
                "a": _point(calibrage["a"]),
                "b": _point(calibrage["b"]),
                "distance_m": float(calibrage["distance_m"]),
                "libelle": str(calibrage.get("libelle") or "")[:120],
            }
        except (KeyError, TypeError, ValueError):
            raise HTTPException(status_code=400, detail="Calibrage invalide (2 points + distance).")

    if emprises or calibrage:
        projet.insertion.guides[photo] = {"emprises": emprises, "calibrage": calibrage}
    else:
        projet.insertion.guides.pop(photo, None)
    projet.date_modification = datetime.now().isoformat(timespec="seconds")
    _sauver(projet)
    return {"projet": projet}


# ------------------------------------------------------------------ sélection & exports

@router.put("/{projet_id}/insertion/dossier")
def inclure_au_dossier(projet_id: str, corps: dict = Body(...)):
    """Inclut / retire une insertion du dossier DP exporté."""
    projet = _charger(projet_id)
    fichier = corps.get("fichier")
    if fichier not in [im.get("fichier") for im in projet.insertion.images]:
        raise HTTPException(status_code=400, detail="Image inconnue.")
    selection = [f for f in projet.insertion.dans_dossier if f != fichier]
    if corps.get("inclure"):
        selection.append(fichier)
    projet.insertion.dans_dossier = selection
    projet.date_modification = datetime.now().isoformat(timespec="seconds")
    _sauver(projet)
    return {"projet": projet}


@router.get("/{projet_id}/insertion/image.jpg")
def telecharger_jpeg(projet_id: str, chemin: str):
    """Sert une insertion convertie en JPEG (usage hors outil)."""
    from fastapi.responses import Response
    import io

    from PIL import Image

    cible = (config.PROJETS_DIR / chemin).resolve()
    base = config.assets_dir(projet_id).resolve()
    if base not in cible.parents or not cible.exists():
        raise HTTPException(status_code=404, detail="Fichier introuvable.")
    buf = io.BytesIO()
    with Image.open(cible) as im:
        im.convert("RGB").save(buf, "JPEG", quality=92)
    nom = Path(chemin).stem + ".jpg"
    return Response(buf.getvalue(), media_type="image/jpeg",
                    headers={"Content-Disposition": f'attachment; filename="{nom}"'})


@router.put("/{projet_id}/insertion/retenue")
def choisir_retenue(projet_id: str, corps: dict = Body(...)):
    projet = _charger(projet_id)
    projet.insertion.retenue = corps.get("fichier")
    projet.date_modification = datetime.now().isoformat(timespec="seconds")
    _sauver(projet)
    return {"projet": projet}


@router.delete("/{projet_id}/insertion/image")
def retirer_image(projet_id: str, fichier: str):
    projet = _charger(projet_id)
    projet.insertion.images = [im for im in projet.insertion.images if im.get("fichier") != fichier]
    projet.insertion.dans_dossier = [f for f in projet.insertion.dans_dossier if f != fichier]
    if projet.insertion.retenue == fichier:
        projet.insertion.retenue = (projet.insertion.images[0]["fichier"]
                                    if projet.insertion.images else None)
    cible = (config.PROJETS_DIR / fichier).resolve()
    base = config.assets_dir(projet_id).resolve()
    if base in cible.parents and cible.exists():
        cible.unlink()
    projet.date_modification = datetime.now().isoformat(timespec="seconds")
    _sauver(projet)
    return {"projet": projet}


@router.get("/{projet_id}/insertion/fichier")
def servir_fichier(projet_id: str, chemin: str):
    """Sert une image importée (chemins confinés au dossier assets du projet)."""
    cible = (config.PROJETS_DIR / chemin).resolve()
    base = config.assets_dir(projet_id).resolve()
    if base not in cible.parents or not cible.exists():
        raise HTTPException(status_code=404, detail="Fichier introuvable.")
    return FileResponse(cible)


# ------------------------------------------------------------------ fiche d'emprise

@router.post("/{projet_id}/insertion/fiche")
def exporter_fiche(projet_id: str):
    projet = _charger(projet_id)
    try:
        chemin = generer_fiche(projet.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "fichier": chemin.name,
        "telechargement": f"/api/projets/{projet_id}/insertion/fiche.pptx",
    }


@router.get("/{projet_id}/insertion/fiche.pptx")
def telecharger_fiche(projet_id: str):
    chemin = config.assets_dir(projet_id) / f"Fiche_emprise_{projet_id}.pptx"
    if not chemin.exists():
        raise HTTPException(status_code=404, detail="Fiche non générée.")
    return FileResponse(chemin, filename=chemin.name)
