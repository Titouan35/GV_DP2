"""Uploads des pièces du BE (étape 4) : un fichier par code de pièce.

Fichiers stockés dans PROJETS/<id>.assets/uploads/ (gitignoré avec le
reste de PROJETS/, données client).
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile
from PIL import Image, ImageOps

from .. import config, lecture_plan, regles
from .routes_projets import _charger, _sauver

router = APIRouter(prefix="/api/projets", tags=["documents"])

CODES_UPLOAD = {"dp2", "dp3", "dp6", "dp7", "dp8", "photo_site"}
EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg"}
TAILLE_MAX = 40 * 1024 * 1024  # 40 Mo

# en-têtes (magic bytes) attendus par extension : un fichier renommé .pdf qui
# n'en est pas un plantait plus loin dans pypdfium avec une erreur obscure
SIGNATURES = {".pdf": b"%PDF", ".png": b"\x89PNG", ".jpg": b"\xff\xd8",
              ".jpeg": b"\xff\xd8", ".webp": b"RIFF"}


def signature_valide(ext: str, contenu: bytes) -> bool:
    """Vrai si le contenu du fichier correspond bien à son extension."""
    attendu = SIGNATURES.get(ext)
    if attendu is None:
        return True
    if not contenu.startswith(attendu):
        return False
    return ext != ".webp" or contenu[8:12] == b"WEBP"


async def lire_upload(fichier: UploadFile, taille_max: int) -> bytes:
    """Lit un upload par morceaux, en coupant court dès le dépassement.

    L'ancien `await fichier.read()` bufferisait tout le fichier avant de
    vérifier la taille : un envoi de plusieurs centaines de Mo passait
    intégralement en mémoire avant d'être rejeté.
    """
    morceaux: list[bytes] = []
    total = 0
    while True:
        bloc = await fichier.read(1024 * 1024)
        if not bloc:
            break
        total += len(bloc)
        if total > taille_max:
            raise HTTPException(status_code=400, detail="Fichier trop volumineux (40 Mo max).")
        morceaux.append(bloc)
    return b"".join(morceaux)


def normaliser_exif(chemin: Path) -> None:
    """Réécrit une image redressée si elle porte un tag EXIF Orientation.

    Le navigateur affiche les photos redressées, PIL (planches, insertion,
    fiche d'emprise) les lisait brutes : normaliser une fois à l'upload rend
    tout le pipeline cohérent avec ce que l'utilisateur voit.
    """
    if chemin.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
        return
    try:
        with Image.open(chemin) as im:
            if im.getexif().get(274, 1) == 1:  # tag Orientation absent/neutre
                return
            redressee = ImageOps.exif_transpose(im)
        params = {"quality": 95} if chemin.suffix.lower() in {".jpg", ".jpeg"} else {}
        redressee.save(chemin, **params)
    except OSError:
        pass  # image illisible : on laisse le fichier tel quel


def _verifier_code(code: str):
    if code not in CODES_UPLOAD:
        raise HTTPException(status_code=404, detail=f"Pièce uploadable inconnue : {code}")


def _chemin_confine(relatif: str) -> Path:
    """Chemin résolu, confiné à PROJETS/ (même garde que routes_insertion)."""
    cible = (config.PROJETS_DIR / relatif).resolve()
    if config.PROJETS_DIR.resolve() not in cible.parents:
        raise HTTPException(status_code=404, detail="Fichier introuvable.")
    return cible


@router.post("/{projet_id}/documents/{code}")
async def uploader(projet_id: str, code: str, fichier: UploadFile):
    _verifier_code(code)
    ext = Path(fichier.filename or "").suffix.lower()
    if ext not in EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"Format non accepté ({ext}) : PDF, PNG ou JPG.")
    contenu = await lire_upload(fichier, TAILLE_MAX)
    if not signature_valide(ext, contenu):
        raise HTTPException(status_code=400,
                            detail=f"Le contenu du fichier ne correspond pas à son format ({ext}).")

    projet = _charger(projet_id)
    dossier = config.assets_dir(projet_id) / "uploads"
    dossier.mkdir(parents=True, exist_ok=True)
    # un seul fichier par pièce : purger les anciennes versions
    for ancien in dossier.glob(f"{code}.*"):
        ancien.unlink()
    chemin = dossier / f"{code}{ext}"
    chemin.write_bytes(contenu)
    normaliser_exif(chemin)

    projet.documents[code] = {
        "nom_fichier": fichier.filename,
        "fichier": str(chemin.relative_to(config.PROJETS_DIR)).replace("\\", "/"),
        "date": datetime.now().isoformat(timespec="seconds"),
        "taille": len(contenu),
    }

    # plan de masse : lecture du cartouche -> pré-remplissage des champs vides
    champs_proposes: list[str] = []
    if code == "dp2":
        lecture = lecture_plan.lire_plan_masse(chemin)
        if lecture:
            champs_proposes = lecture_plan.appliquer_lecture(projet, lecture)

    projet.date_modification = datetime.now().isoformat(timespec="seconds")
    _sauver(projet)
    return {"projet": projet, "evaluation": regles.evaluer(projet),
            "plan_champs_proposes": champs_proposes}


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
    chemin = _chemin_confine(doc["fichier"])
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
    chemin = _chemin_confine(doc["fichier"])
    if not chemin.exists():
        raise HTTPException(status_code=404, detail="Fichier manquant sur le disque.")
    if chemin.suffix.lower() != ".pdf":
        return FileResponse(chemin)
    import pypdfium2 as pdfium
    sortie = config.assets_dir(projet_id) / f"apercu_{code}.png"
    with config.PDFIUM_LOCK:
        pdf = pdfium.PdfDocument(str(chemin))
        try:
            pdf[0].render(scale=200 / 72).to_pil().save(sortie)
        finally:
            pdf.close()
    return FileResponse(sortie, media_type="image/png")
