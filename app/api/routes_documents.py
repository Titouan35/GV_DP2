"""Uploads des pièces du BE (étape 4) : un fichier par code de pièce.

Fichiers stockés dans PROJETS/<id>.assets/uploads/ (gitignoré avec le
reste de PROJETS/, données client).
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from PIL import Image, ImageOps

from .. import config, coupes_dp3, lecture_fve, lecture_plan, regles
from .routes_projets import _charger, _utilisateur, exiger_projet_id, projet_a_modifier

# Toutes les routes portent {projet_id} : validé avant la route (exiger_projet_id)
router = APIRouter(prefix="/api/projets", tags=["documents"],
                   dependencies=[Depends(exiger_projet_id)])

# "photo_site" est parti avec le module Insertion (01/09/2026) : la photo
# du parking actuel est désormais la pièce DP7, déjà demandée au BE.
CODES_UPLOAD = {"dp2", "dp3", "dp6", "dp7", "dp8", "fve"}
EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".pptx"}

# La FVE (Fiche de Validation d'Emprises) n'est PAS une pièce du dossier : c'est
# un document source du BE, dont on tire des valeurs et le photomontage. Elle
# n'entre donc pas dans regles.PIECES_DP et ne compte pas dans la complétude.
CODE_FVE = "fve"
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


def _proposer_images_fve(projet, projet_id: str, lecture: dict) -> list[str]:
    """Dépose le photomontage de la FVE en DP6, et la photo du site en DP7.

    Ne remplace JAMAIS une pièce déjà fournie : le bureau d'études reste
    l'auteur du dossier, la FVE ne fait que proposer ce qui manque. Renvoie
    les codes réellement déposés, pour que l'interface le dise.
    """
    correspondances = [("insertion", "dp6"), ("source", "dp7")]
    deposees: list[str] = []
    for role, code_piece in correspondances:
        blob = (lecture.get("images") or {}).get(role)
        if not blob or code_piece in (projet.documents or {}):
            continue
        # signature_valide compare l'en-tête du fichier à son extension :
        # on s'en sert ici pour DEDUIRE l'extension du contenu.
        ext = ".png" if signature_valide(".png", blob) else ".jpg"
        chemin = config.assets_dir(projet_id) / "uploads" / f"{code_piece}{ext}"
        chemin.parent.mkdir(parents=True, exist_ok=True)
        chemin.write_bytes(blob)
        projet.documents[code_piece] = {
            "nom_fichier": f"FVE — {role}{ext}",
            "fichier": str(chemin.relative_to(config.espace())).replace("\\", "/"),
            "date": datetime.now().isoformat(timespec="seconds"),
            "taille": len(blob),
            "origine": "fve",      # traçabilité : valeur non fournie par le BE
        }
        deposees.append(code_piece)
    return deposees


def _proposer_coupe_fve(projet, projet_id: str) -> tuple[str | None, str | None]:
    """Dépose la coupe type du catalogue en DP3. Renvoie (libellé, refus).

    Le choix se fait sur le nombre de modules du rampant ET le point bas, tous
    deux lus sur la FVE (app/coupes_dp3.py). Ne remplace jamais une DP3 déjà
    fournie : le bureau d'études reste l'auteur de sa coupe.

    La pièce est marquée `origine: "catalogue"`, pas `"fve"` : elle ne sort pas
    de la fiche, elle est CHOISIE d'après elle. L'assemblage s'en sert pour
    l'étiqueter provisoire, parce qu'elle porte le cartouche du dossier dont
    elle est issue et non celui du projet en cours.
    """
    omb = projet.ombriere
    coupe, refus = coupes_dp3.choisir(omb.nb_modules_rampant, omb.garde_au_sol_m)
    if coupe is None:
        return None, refus
    if "dp3" in (projet.documents or {}):
        return None, None          # le BE a déjà fourni sa coupe : on n'y touche pas

    chemin = config.assets_dir(projet_id) / "uploads" / "dp3.pdf"
    chemin.parent.mkdir(parents=True, exist_ok=True)
    chemin.write_bytes(coupe["chemin"].read_bytes())
    projet.documents["dp3"] = {
        "nom_fichier": coupe["libelle"] + ".pdf",
        "fichier": str(chemin.relative_to(config.espace())).replace("\\", "/"),
        "date": datetime.now().isoformat(timespec="seconds"),
        "taille": chemin.stat().st_size,
        "origine": "catalogue",
        "provisoire": True,
    }
    return coupe["libelle"], None


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
    """Chemin confiné à l'espace de travail (garde contre la traversée de dossier)."""
    cible = config.chemin_dans_espace(relatif)
    if cible is None:
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

    # Seule la lecture de l'envoi a besoin de la boucle asynchrone. La suite
    # est bloquante (lecture du PPTX de la FVE, rendu PDF du plan de masse) et
    # tient le verrou du projet : exécutée ici, elle figeait TOUT le serveur
    # pendant plusieurs secondes, et un verrou tenu par une autre requête
    # aurait bloqué la boucle elle-même (20/11/2026).
    return await run_in_threadpool(
        _enregistrer_depot, projet_id, code, ext, fichier.filename, contenu)


def _enregistrer_depot(projet_id: str, code: str, ext: str,
                       nom_fichier: str | None, contenu: bytes) -> dict:
    """Écrit la pièce et met le projet à jour, sous le verrou du projet."""
    with projet_a_modifier(projet_id) as projet:
        dossier = config.assets_dir(projet_id) / "uploads"
        dossier.mkdir(parents=True, exist_ok=True)
        # un seul fichier par pièce : purger les anciennes versions
        for ancien in dossier.glob(f"{code}.*"):
            ancien.unlink()
        chemin = dossier / f"{code}{ext}"
        chemin.write_bytes(contenu)
        normaliser_exif(chemin)

        projet.documents[code] = {
            "nom_fichier": nom_fichier,
            "fichier": str(chemin.relative_to(config.espace())).replace("\\", "/"),
            "date": datetime.now().isoformat(timespec="seconds"),
            "taille": len(contenu),
        }

        # plan de masse : lecture du cartouche -> pré-remplissage des champs vides
        champs_proposes: list[str] = []
        fve_images: list[str] = []
        if code == "dp2":
            lecture = lecture_plan.lire_plan_masse(chemin)
            if lecture:
                champs_proposes = lecture_plan.appliquer_lecture(projet, lecture)

        # FVE du BE : mêmes règles que le plan de masse (on PROPOSE, on n'impose
        # pas), plus le photomontage d'insertion offert comme pièce DP6.
        if code == CODE_FVE:
            lecture = lecture_fve.lire_fve(chemin)
            champs_proposes = lecture_fve.appliquer_lecture(projet, lecture)
            fve_images = _proposer_images_fve(projet, projet_id, lecture)
            # la coupe type vient APRÈS appliquer_lecture : elle se choisit sur des
            # valeurs que cette lecture vient d'écrire (modules du rampant, point bas)
            # la coupe n'entre PAS dans `fve_images` : elle est annoncée à part,
            # sinon le bilan disait deux fois la même chose (« DP3 déposée · coupe
            # type en DP3 »)
            coupe, refus_coupe = _proposer_coupe_fve(projet, projet_id)
            # de quel fichier, et quand : l'étape 3 l'affiche pour que le rédacteur
            # sache quelle version de la FVE a alimenté les cases qu'il relit.
            projet.meta["fve"].update({
                "fichier": nom_fichier,
                "date": projet.documents[code]["date"],
                "images": fve_images,
                "coupe": coupe,
                "coupe_refus": refus_coupe,
            })

        projet.date_modification = datetime.now().isoformat(timespec="seconds")
    return {"projet": projet, "evaluation": regles.evaluer(projet),
            "plan_champs_proposes": champs_proposes,
            "fve_images": fve_images}


@router.put("/{projet_id}/documents/{code}/valider")
def valider_document(projet_id: str, code: str, valide: int = 1, request: Request = None):
    """Le BE prend la responsabilité d'une pièce déposée automatiquement.

    Une coupe type du catalogue est provisoire tant que personne ne l'a
    regardée : elle est cotée pour SA configuration et porte le cartouche du
    dossier dont elle est issue. Valider, c'est dire « je l'ai vérifiée, elle
    convient à ce projet » — l'anomalie bloquante tombe alors, exactement
    comme la notice cesse d'être un brouillon quand elle est relue
    (`notice.valide_humain`).

    La validation ne survit pas à un remplacement : téléverser une nouvelle
    pièce réécrit l'entrée entière, donc la marque disparaît avec elle.
    `valide=0` revient en arrière.
    """
    _verifier_code(code)
    with projet_a_modifier(projet_id) as projet:
        doc = projet.documents.get(code)
        if not doc:
            raise HTTPException(status_code=404, detail="Pièce non fournie.")
        if valide:
            doc["valide_humain"] = True
            doc["valide_le"] = datetime.now().isoformat(timespec="seconds")
            doc["valide_par"] = _utilisateur(request)
        else:
            for cle in ("valide_humain", "valide_le", "valide_par"):
                doc.pop(cle, None)
        projet.date_modification = datetime.now().isoformat(timespec="seconds")
    return {"projet": projet, "evaluation": regles.evaluer(projet)}


@router.delete("/{projet_id}/documents/{code}")
def supprimer_document(projet_id: str, code: str):
    _verifier_code(code)
    with projet_a_modifier(projet_id) as projet:
        doc = projet.documents.pop(code, None)
        if doc:
            # confiné : un `fichier` forgé côté navigateur ne doit jamais désigner
            # un fichier hors de l'espace (voir config.chemin_dans_espace). L'entrée
            # est retirée du projet dans tous les cas.
            chemin = config.chemin_dans_espace(doc.get("fichier"))
            if chemin is not None and chemin.is_file():
                chemin.unlink()
        if code == CODE_FVE:
            # Retirer la FVE, c'est dire « oublie ce document ». Les valeurs déjà
            # reportées restent — elles appartiennent au projet — mais plus rien ne
            # les annonce comme reprises d'une fiche qui n'est plus là.
            projet.meta.pop("fve", None)
        projet.date_modification = datetime.now().isoformat(timespec="seconds")
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
