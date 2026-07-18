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


@router.get("/{projet_id}/insertion/apercu-prompt")
def apercu_prompt(projet_id: str):
    """Prompt auto qui serait envoyé, pour l'aperçu éditable de l'UI."""
    projet = _charger(projet_id)
    return {"prompt": insertion_ia.apercu_prompt(projet.model_dump())}


TITRES_PAYLOAD = {
    "photo": "Photo repérée (bords avant + fuite)",
    "coupe": "Coupe technique (profil exact)",
    "reference": "Ombrière de référence (réalisme)",
}


@router.get("/{projet_id}/insertion/apercu-payload")
def apercu_payload(projet_id: str):
    """Le lot d'images EXACT qui partira à Gemini (miniatures UI) + le prompt."""
    projet = _charger(projet_id)
    try:
        req = insertion_ia._preparer_requete_pose(projet.model_dump())
    except (InsertionError, OSError):
        # OSError = fichier cache (photo repérée) momentanément verrouillé par
        # OneDrive : l'aperçu ne doit jamais planter, il se recharge au clic.
        return {"images": [], "prompt": ""}
    from ..catalogue import libelle_coupe

    # quand plusieurs coupes ou références partent (un type distinct chacune),
    # on nomme le type dans la vignette : sans ça, deux miniatures portent le
    # même libellé et se lisent comme un doublon.
    familles = insertion_ia._familles_tracees(projet.model_dump())
    compte = {"coupe": 0, "reference": 0}
    total = {r: req["roles"].count(r) for r in ("coupe", "reference")}

    images = []
    for i, (role, chemin) in enumerate(zip(req["roles"], req["chemins"]), 1):
        rang = compte.get(role, 0)
        if role == "reference":
            # les références vivent hors de PROJETS_DIR (app/gabarits) : route dédiée
            url = f"/api/projets/{projet_id}/insertion/reference?i={rang}"
        else:
            rel = str(Path(chemin).resolve().relative_to(config.PROJETS_DIR.resolve())).replace("\\", "/")
            url = f"/api/projets/{projet_id}/insertion/fichier?chemin={quote(rel)}"
        titre = TITRES_PAYLOAD.get(role, role)
        if role in compte:
            if total[role] > 1 and rang < len(familles):
                titre = f"{titre.split(' (')[0]} · {libelle_coupe(familles[rang])}"
            compte[role] = rang + 1
        images.append({"role": role, "titre": f"{i} · {titre}", "url": url})
    return {"images": images, "prompt": req["prompt"]}


@router.get("/{projet_id}/insertion/reference")
def servir_reference(projet_id: str, i: int = 0):
    """Photo de référence d'ombrière (bundlée) — une par type distinct tracé."""
    projet = _charger(projet_id)
    refs = insertion_ia._reference_photos(projet.model_dump())
    if not refs:
        raise HTTPException(status_code=404, detail="Référence introuvable.")
    return FileResponse(refs[min(max(0, i), len(refs) - 1)])


# ------------------------------------------------------------------ vue aérienne

@router.get("/{projet_id}/insertion/aerienne")
def lire_aerienne(projet_id: str):
    """Fond aérien (crop du plan) + emprises (auto ou ajustées) + flèche."""
    projet = _charger(projet_id)
    d = insertion_ia.aerienne_donnees(projet.model_dump())
    if not d:
        return {"disponible": False}
    rel = str(d["fond"].resolve().relative_to(config.PROJETS_DIR.resolve())).replace("\\", "/")
    return {
        "disponible": True,
        "fond": f"/api/projets/{projet_id}/insertion/fichier?chemin={quote(rel)}",
        "emprises": d["emprises"],
        "fleche": d["fleche"],
        "auto": d["auto"],
    }


@router.put("/{projet_id}/insertion/aerienne")
def sauver_aerienne(projet_id: str, corps: dict = Body(...)):
    """Emprises ajustées sur la vue aérienne (coordonnées 0-1 du crop)."""
    projet = _charger(projet_id)

    def _point(p):
        try:
            x, y = float(p[0]), float(p[1])
        except (TypeError, ValueError, IndexError):
            raise HTTPException(status_code=400, detail="Point invalide.")
        return [min(1.0, max(0.0, x)), min(1.0, max(0.0, y))]

    emprises = [[_point(p) for p in e] for e in (corps.get("emprises") or []) if len(e) >= 3]
    if not emprises:
        raise HTTPException(status_code=400, detail="Au moins une emprise attendue.")
    projet.insertion.aerienne = {"emprises": emprises, "auto": False}
    projet.date_modification = datetime.now().isoformat(timespec="seconds")
    _sauver(projet)
    return {"projet": projet}


@router.delete("/{projet_id}/insertion/aerienne")
def reinitialiser_aerienne(projet_id: str):
    """Revient à l'emprise automatique extraite du plan."""
    projet = _charger(projet_id)
    projet.insertion.aerienne = {}
    projet.date_modification = datetime.now().isoformat(timespec="seconds")
    _sauver(projet)
    return {"projet": projet}


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
    prompt_override = str(corps.get("prompt", "") or "")[:8000]
    try:
        image = insertion_ia.generer_image(projet.model_dump(), affinage=affinage,
                                           prompt_override=prompt_override)
    except InsertionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    projet.insertion.images = [image, *projet.insertion.images]
    if not projet.insertion.retenue:
        projet.insertion.retenue = image["fichier"]
    projet.insertion.prompt = image.get("prompt")
    essais = int(image.get("essais", 1))   # coût réel (relance auto incluse)
    projet.insertion.nb_images_generees += essais
    images_global = insertion_ia.incrementer_compteur_global(essais)
    projet.date_modification = datetime.now().isoformat(timespec="seconds")
    _sauver(projet)
    return {"projet": projet, "image": image,
            "images_projet": projet.insertion.nb_images_generees,
            "images_global": images_global}


# ------------------------------------------------------------------ type d'ombrière

@router.put("/{projet_id}/insertion/type")
def choisir_type(projet_id: str, corps: dict = Body(...)):
    """Type d'ombrière pour l'insertion (Mono Bas / Mono Haut / Double).

    Met à jour ombriere.famille et, si les hauteurs ne sont pas déjà saisies,
    les initialise depuis le catalogue (modifiables à l'étape Caractéristiques).
    """
    from ..catalogue import CATALOGUE

    projet = _charger(projet_id)
    famille = corps.get("famille")
    if famille not in CATALOGUE:
        raise HTTPException(status_code=400, detail="Type inconnu.")
    projet.ombriere.famille = famille
    entree = CATALOGUE[famille]
    if projet.ombriere.garde_au_sol_m is None:
        projet.ombriere.garde_au_sol_m = entree["h_bas_m"]
    if projet.ombriere.hauteur_hors_tout_m is None:
        projet.ombriere.hauteur_hors_tout_m = entree["h_haut_m"]
    projet.date_modification = datetime.now().isoformat(timespec="seconds")
    _sauver(projet)
    return {"projet": projet}


# ------------------------------------------------------------------ pose (un geste)

@router.put("/{projet_id}/insertion/pose")
def sauver_pose(projet_id: str, corps: dict = Body(...)):
    """Ombrières tracées sur une photo (un cliqué-glissé = un bord avant).

    Corps : {photo, ombrieres: [{bord_avant: [[x,y],[x,y]], famille?,
    longueur_m?, profondeur_m?}]} — coordonnées 0-1, gauche->droite.
    Les cotes absentes sont pré-remplies depuis le plan de masse (par ordre des
    rangées lues), le type absent retombe sur celui du projet. Liste vide =
    efface les tracés de cette photo.
    """
    from ..catalogue import CATALOGUE

    projet = _charger(projet_id)
    photo = corps.get("photo")
    if photo not in (projet.insertion.photos or []):
        raise HTTPException(status_code=400, detail="Photo inconnue.")

    def _point(p):
        try:
            x, y = float(p[0]), float(p[1])
        except (TypeError, ValueError, IndexError):
            raise HTTPException(status_code=400, detail="Point de pose invalide.")
        return [min(1.0, max(0.0, x)), min(1.0, max(0.0, y))]

    def _cote(v):
        try:
            return round(float(v), 1) if v not in (None, "") else None
        except (TypeError, ValueError):
            return None

    dims = insertion_ia.plan_dims(projet.model_dump())   # cotes lues sur le plan
    defaut = projet.ombriere.famille or "START PLAINE Bas"
    ombrieres = []
    for i, o in enumerate(corps.get("ombrieres") or []):
        ba = o.get("bord_avant")
        if not (ba and len(ba) == 2):
            continue
        famille = o.get("famille") if o.get("famille") in CATALOGUE else defaut
        L = _cote(o.get("longueur_m"))
        prof = _cote(o.get("profondeur_m"))
        if L is None and i < len(dims):
            L = round(dims[i][0], 1)
        if prof is None and i < len(dims):
            prof = round(dims[i][1], 1)
        if prof is None:
            prof = CATALOGUE[famille]["profondeur_m"]
        ombrieres.append({
            "bord_avant": [_point(ba[0]), _point(ba[1])],
            "famille": famille, "longueur_m": L, "profondeur_m": prof,
        })

    if ombrieres:
        projet.insertion.poses[photo] = {"ombrieres": ombrieres}
    else:
        projet.insertion.poses.pop(photo, None)
    projet.date_modification = datetime.now().isoformat(timespec="seconds")
    _sauver(projet)
    return {"projet": projet}


# ------------------------------------------------------------------ guides photo (legacy)

@router.get("/{projet_id}/insertion/plan-dims")
def plan_dims(projet_id: str):
    """Cotes (longueur, largeur) des rangées lues sur le plan, pour préremplir."""
    projet = _charger(projet_id)
    dims = insertion_ia.plan_dims(projet.model_dump())
    return {"dims_m": [{"longueur_m": L, "largeur_m": l} for L, l in dims]}


@router.put("/{projet_id}/insertion/guides")
def sauver_guides(projet_id: str, corps: dict = Body(...)):
    """Repères tracés sur une photo : une ombrière = 2 traits (longueur+largeur).

    Corps : {photo, ombrieres:[{longueur:[A,B], largeur:[C,D],
    longueur_m?, largeur_m?}]}. Coordonnées 0-1. Les cotes manquantes sont
    préremplies depuis le plan de masse (par ordre des rangées), modifiables.
    """
    projet = _charger(projet_id)
    photo = corps.get("photo")
    if photo not in (projet.insertion.photos or []):
        raise HTTPException(status_code=400, detail="Photo inconnue.")

    def _point(p):
        try:
            x, y = float(p[0]), float(p[1])
        except (TypeError, ValueError, IndexError):
            raise HTTPException(status_code=400, detail="Point de repère invalide.")
        return [min(1.0, max(0.0, x)), min(1.0, max(0.0, y))]

    def _cote(v):
        try:
            return round(float(v), 1) if v not in (None, "") else None
        except (TypeError, ValueError):
            return None

    dims = insertion_ia.plan_dims(projet.model_dump())
    ombrieres = []
    for i, o in enumerate(corps.get("ombrieres") or []):
        lo, la = o.get("longueur"), o.get("largeur")
        if not (lo and la and len(lo) == 2 and len(la) == 2):
            continue
        L_m = _cote(o.get("longueur_m"))
        l_m = _cote(o.get("largeur_m"))
        if L_m is None and i < len(dims):
            L_m = round(dims[i][0], 1)
        if l_m is None and i < len(dims):
            l_m = round(dims[i][1], 1)
        ombrieres.append({
            "longueur": [_point(lo[0]), _point(lo[1])],
            "largeur": [_point(la[0]), _point(la[1])],
            "longueur_m": L_m, "largeur_m": l_m,
        })

    if ombrieres:
        projet.insertion.guides[photo] = {"ombrieres": ombrieres}
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
