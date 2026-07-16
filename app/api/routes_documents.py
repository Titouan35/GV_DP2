"""Uploads des pièces du BE (étape 4) : un fichier par code de pièce.

Fichiers stockés dans PROJETS/<id>.assets/uploads/ (gitignoré avec le
reste de PROJETS/, données client).
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile

from .. import config, regles
from .routes_projets import _charger, _sauver

router = APIRouter(prefix="/api/projets", tags=["documents"])

CODES_UPLOAD = {"dp2", "dp3", "dp4", "dp6", "dp7", "dp8", "photo_site"}
EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg"}
TAILLE_MAX = 40 * 1024 * 1024  # 40 Mo


def _verifier_code(code: str):
    if code not in CODES_UPLOAD:
        raise HTTPException(status_code=404, detail=f"Pièce uploadable inconnue : {code}")


@router.post("/{projet_id}/documents/{code}")
async def uploader(projet_id: str, code: str, fichier: UploadFile):
    _verifier_code(code)
    ext = Path(fichier.filename or "").suffix.lower()
    if ext not in EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"Format non accepté ({ext}) : PDF, PNG ou JPG.")
    contenu = await fichier.read()
    if len(contenu) > TAILLE_MAX:
        raise HTTPException(status_code=400, detail="Fichier trop volumineux (40 Mo max).")

    projet = _charger(projet_id)
    dossier = config.assets_dir(projet_id) / "uploads"
    dossier.mkdir(parents=True, exist_ok=True)
    # un seul fichier par pièce : purger les anciennes versions
    for ancien in dossier.glob(f"{code}.*"):
        ancien.unlink()
    chemin = dossier / f"{code}{ext}"
    chemin.write_bytes(contenu)

    projet.documents[code] = {
        "nom_fichier": fichier.filename,
        "fichier": str(chemin.relative_to(config.PROJETS_DIR)).replace("\\", "/"),
        "date": datetime.now().isoformat(timespec="seconds"),
        "taille": len(contenu),
    }
    projet.date_modification = datetime.now().isoformat(timespec="seconds")
    _sauver(projet)
    return {"projet": projet, "evaluation": regles.evaluer(projet)}


@router.delete("/{projet_id}/documents/{code}")
def supprimer_document(projet_id: str, code: str):
    _verifier_code(code)
    projet = _charger(projet_id)
    doc = projet.documents.pop(code, None)
    if doc:
        chemin = config.PROJETS_DIR / doc["fichier"]
        if chemin.exists():
            chemin.unlink()
    projet.date_modification = datetime.now().isoformat(timespec="seconds")
    _sauver(projet)
    return {"projet": projet, "evaluation": regles.evaluer(projet)}


@router.get("/{projet_id}/documents/{code}")
def telecharger_document(projet_id: str, code: str):
    from fastapi.responses import FileResponse

    projet = _charger(projet_id)
    doc = projet.documents.get(code)
    if not doc:
        raise HTTPException(status_code=404, detail="Pièce non fournie.")
    chemin = config.PROJETS_DIR / doc["fichier"]
    if not chemin.exists():
        raise HTTPException(status_code=404, detail="Fichier manquant sur le disque.")
    return FileResponse(chemin, filename=doc["nom_fichier"])


@router.get("/{projet_id}/documents/{code}/image")
def apercu_document_image(projet_id: str, code: str):
    """Rend la pièce en PNG (1re page si PDF) pour l'outil de mesure du plan."""
    from fastapi.responses import FileResponse

    projet = _charger(projet_id)
    doc = projet.documents.get(code)
    if not doc:
        raise HTTPException(status_code=404, detail="Pièce non fournie.")
    chemin = config.PROJETS_DIR / doc["fichier"]
    if not chemin.exists():
        raise HTTPException(status_code=404, detail="Fichier manquant sur le disque.")
    if chemin.suffix.lower() != ".pdf":
        return FileResponse(chemin)
    import pypdfium2 as pdfium
    sortie = config.assets_dir(projet_id) / f"apercu_{code}.png"
    pdf = pdfium.PdfDocument(str(chemin))
    try:
        pdf[0].render(scale=200 / 72).to_pil().save(sortie)
    finally:
        pdf.close()
    return FileResponse(sortie, media_type="image/png")
