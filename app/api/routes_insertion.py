"""Étape 5 — Insertion IA, générateur de prompt SANS API (PLAN §6 bis).

Endpoints : upload photo du site, sauvegarde des consignes, construction du
prompt + kit d'images à joindre, service des images du kit (photo / plan de
masse / coupe), ré-import de l'image générée dans ChatGPT (zone de dépôt),
choix de l'image retenue, et export de la fiche de validation d'emprise.

Aucun appel réseau, aucune clé : tout est local et déterministe.
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
    return {**insertion_ia.apercu(), "etat": insertion_ia.etat(projet.model_dump())}


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


# ------------------------------------------------------------------ génération API (Gemini)

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
    projet.date_modification = datetime.now().isoformat(timespec="seconds")
    _sauver(projet)
    return {"projet": projet, "image": image}


# ------------------------------------------------------------------ prompt + kit

@router.post("/{projet_id}/insertion/prompt")
def generer_prompt(projet_id: str, corps: dict = Body(default={})):
    """Construit le prompt (6 blocs) + le kit d'images. Persiste le prompt."""
    projet = _charger(projet_id)
    affinage = str(corps.get("affinage", "") or "")
    if affinage:
        projet.insertion.affinage = affinage[:2000]
    data = projet.model_dump()
    prompt = insertion_ia.construire_prompt(data, affinage=affinage)
    projet.insertion.prompt = prompt
    projet.date_modification = datetime.now().isoformat(timespec="seconds")
    _sauver(projet)
    return {"projet": projet, "prompt": prompt, "kit": insertion_ia.kit(data)}


@router.get("/{projet_id}/insertion/kit/{role}")
def servir_kit(projet_id: str, role: str, t: int = 0):
    """Sert une image du kit (photo / plan / coupe), générée à la demande."""
    if role not in ("photo", "plan", "coupe"):
        raise HTTPException(status_code=404, detail="Rôle de kit inconnu.")
    projet = _charger(projet_id).model_dump()
    chemin = insertion_ia.image_kit(projet, role)
    if not chemin or not Path(chemin).exists():
        raise HTTPException(status_code=404, detail="Image du kit indisponible (input manquant).")
    return FileResponse(chemin)


@router.get("/{projet_id}/insertion/kit.zip")
def telecharger_kit(projet_id: str):
    """Télécharge d'un coup les images disponibles du kit (à joindre dans ChatGPT)."""
    import io
    import zipfile

    from fastapi.responses import Response

    projet = _charger(projet_id).model_dump()
    noms = {"photo": "1_photo_site", "plan": "2_plan_de_masse", "coupe": "3_coupe"}
    buf = io.BytesIO()
    n = 0
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for role, base in noms.items():
            chemin = insertion_ia.image_kit(projet, role)
            if chemin and Path(chemin).exists():
                z.write(chemin, f"{base}{Path(chemin).suffix}")
                n += 1
    if not n:
        raise HTTPException(status_code=400, detail="Aucune image de kit disponible (ajoutez au moins la photo du site).")
    return Response(
        buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="kit_insertion_{projet_id}.zip"'},
    )


# ------------------------------------------------------------------ ré-import (drop zone)

@router.post("/{projet_id}/insertion/import")
async def importer_image(projet_id: str, fichier: UploadFile):
    """Ré-importe l'image générée par ChatGPT (zone de dépôt)."""
    ext = Path(fichier.filename or "").suffix.lower()
    if ext not in EXTENSIONS:
        raise HTTPException(status_code=400, detail="Image PNG, JPG ou WEBP attendue.")
    contenu = await fichier.read()
    if len(contenu) > TAILLE_MAX:
        raise HTTPException(status_code=400, detail="Image trop volumineuse (40 Mo max).")

    projet = _charger(projet_id)
    dossier = config.assets_dir(projet_id) / "insertion"
    dossier.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]
    nom = f"insertion_{stamp}{ext}"
    (dossier / nom).write_bytes(contenu)
    rel = str((dossier / nom).relative_to(config.PROJETS_DIR)).replace("\\", "/")

    image = {
        "fichier": rel,
        "date": datetime.now().isoformat(timespec="seconds"),
        "etiquette": "visuel IA — usage commercial",
    }
    projet.insertion.images = [image, *projet.insertion.images]
    if not projet.insertion.retenue:
        projet.insertion.retenue = rel
    projet.date_modification = datetime.now().isoformat(timespec="seconds")
    _sauver(projet)
    return {"projet": projet, "image": image}


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
