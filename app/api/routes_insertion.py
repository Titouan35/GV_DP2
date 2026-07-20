"""Étape 4 — Insertion IA : génération directe via l'API Gemini.

Endpoints : upload/choix des photos du site, consignes, génération d'une
insertion, sélection des images incluses au dossier DP, téléchargement JPEG,
compteur de dépense local, et fiche de validation d'emprise (PPTX).
"""
from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Body, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from .. import config, insertion_ia
from ..fiche_emprise import generer_fiche
from ..insertion_ia import InsertionError
from .routes_documents import lire_upload, normaliser_exif, signature_valide
from .routes_projets import _charger, _ecrire, _sauver, verrou_projet

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


# (routes « vue aérienne » du flux v5 retirées le 19/07/2026 : plus appelées
#  par l'UI, code archivé dans app/_archive/flux_v5_scaffold.py)


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
        try:
            contenu = await lire_upload(fichier, TAILLE_MAX)
        except HTTPException:
            continue  # fichier trop gros : on passe au suivant
        if not signature_valide(ext, contenu):
            continue
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]
        chemin = dossier / f"site_{stamp}{ext}"
        chemin.write_bytes(contenu)
        normaliser_exif(chemin)  # photo redressée une fois pour toutes
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
    projet.date_modification = datetime.now().isoformat(timespec="seconds")
    _sauver(projet)
    return {"projet": projet}


# ------------------------------------------------------------------ génération (Gemini)

# Génération en TÂCHE DE FOND (19/07/2026) : l'appel Gemini dure 10-30 s.
# En synchrone, l'onglet restait suspendu et fermer la page perdait une image
# déjà payée. Un job par projet, verrou serveur : deux clics/onglets ne
# déclenchent plus deux dépenses.
_GENERATIONS: dict[str, dict] = {}
_GENERATIONS_LOCK = threading.Lock()


def _tache_generation(projet_id: str, affinage: str, prompt_override: str) -> None:
    try:
        projet = _charger(projet_id)
        image = insertion_ia.generer_image(projet.model_dump(), affinage=affinage,
                                           prompt_override=prompt_override)
        # recharger l'état le plus frais avant d'écrire (l'utilisateur a pu
        # modifier le projet pendant la génération) — TOUT sous le verrou du
        # projet, sinon un autosave glissé entre le _charger et l'écriture
        # serait écrasé par le thread
        with verrou_projet(projet_id):
            projet = _charger(projet_id)
            if affinage:
                projet.insertion.affinage = affinage[:2000]
            projet.insertion.images = [image, *projet.insertion.images]
            if not projet.insertion.retenue:
                projet.insertion.retenue = image["fichier"]
            projet.insertion.prompt = image.get("prompt")
            projet.insertion.nb_images_generees += int(image.get("essais", 1))
            projet.date_modification = datetime.now().isoformat(timespec="seconds")
            _ecrire(projet)
        with _GENERATIONS_LOCK:
            _GENERATIONS[projet_id] = {"etat": "prete", "image": image}
    except Exception as exc:  # InsertionError, HTTPException, imprévu : tout doit sortir du job
        detail = getattr(exc, "detail", None) or str(exc)
        with _GENERATIONS_LOCK:
            _GENERATIONS[projet_id] = {"etat": "erreur", "erreur": str(detail)}


@router.post("/{projet_id}/insertion/generer")
def generer_insertion(projet_id: str, corps: dict = Body(default={})):
    """Lance UNE génération Gemini en tâche de fond. 409 si déjà en cours."""
    _charger(projet_id)  # valide l'existence avant de démarrer quoi que ce soit
    if not insertion_ia.api_configuree():
        raise HTTPException(status_code=400,
                            detail="Mode API non configuré : clé GEMINI_API_KEY absente.")
    with _GENERATIONS_LOCK:
        job = _GENERATIONS.get(projet_id)
        if job and job.get("etat") == "en_cours":
            raise HTTPException(status_code=409,
                                detail="Une génération est déjà en cours pour ce projet.")
        _GENERATIONS[projet_id] = {"etat": "en_cours",
                                   "demarre": datetime.now().isoformat(timespec="seconds")}
    affinage = str(corps.get("affinage", "") or "")[:2000]
    prompt_override = str(corps.get("prompt", "") or "")[:8000]
    threading.Thread(target=_tache_generation, daemon=True,
                     args=(projet_id, affinage, prompt_override)).start()
    return {"etat": "en_cours"}


@router.get("/{projet_id}/insertion/generer/statut")
def statut_generation(projet_id: str):
    """État du job de génération : aucune / en_cours / prete / erreur."""
    with _GENERATIONS_LOCK:
        job = dict(_GENERATIONS.get(projet_id) or {"etat": "aucune"})
    if job["etat"] in ("prete", "erreur"):
        job["images_global"] = insertion_ia.compteur_global()
    return job


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
    longueur_m?, profondeur_m?, pente_vers?}]} — coordonnées 0-1,
    gauche->droite. `pente_vers` : "fond" (la toiture monte en s'éloignant,
    défaut) ou "avant" (elle descend en s'éloignant).
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
        pente = o.get("pente_vers")
        ombrieres.append({
            "bord_avant": [_point(ba[0]), _point(ba[1])],
            "famille": famille, "longueur_m": L, "profondeur_m": prof,
            # côté du point haut du rampant, vu du photographe
            "pente_vers": pente if pente in ("fond", "avant") else "fond",
        })

    # ligne d'horizon ajustée (poignée UI) : persistée par photo, elle cale
    # la perspective de l'emprise projetée
    horizon = None
    try:
        h = float(corps.get("horizon"))
        if 0.02 <= h <= 0.95:
            horizon = round(h, 4)
    except (TypeError, ValueError):
        horizon = None
    # hauteur de prise de vue déclarée : c'est ELLE qui pilote la perspective
    # (l'horizon en est déduit), sauf horizon forcé à la poignée
    hauteur_vue = None
    try:
        hv = float(corps.get("hauteur_vue"))
        if 0.3 <= hv <= 120.0:
            hauteur_vue = round(hv, 2)
    except (TypeError, ValueError):
        hauteur_vue = None

    if ombrieres:
        entree = {"ombrieres": ombrieres}
        if horizon is not None:
            entree["horizon"] = horizon
        if hauteur_vue is not None:
            entree["hauteur_vue"] = hauteur_vue
        projet.insertion.poses[photo] = entree
    else:
        projet.insertion.poses.pop(photo, None)
    projet.date_modification = datetime.now().isoformat(timespec="seconds")
    _sauver(projet)
    return {"projet": projet, "volumes": _volumes_reponse(projet)}


def _volumes_reponse(projet) -> dict:
    """Volumes projetés de la photo active, en coordonnées 0-1 (pour le
    filaire du canvas : la MÊME géométrie que celle envoyée à Gemini)."""
    data = projet.model_dump() if hasattr(projet, "model_dump") else projet
    photo = insertion_ia.image_kit(data, "photo")
    if not photo:
        return {"disponible": False}
    try:
        W, H = insertion_ia._ouvrir_image(photo).size
        volumes = insertion_ia.volumes_poses(data, W, H, photo)
        cam = insertion_ia._camera_photo(data, W, H, photo)
        diag = insertion_ia.diagnostic_pose(data, W, H, photo)
    except OSError:
        return {"disponible": False}

    def norm(pts):
        return [[round(x / W, 4), round(y / H, 4)] for x, y in pts]

    return {
        "disponible": any(volumes),
        # horizon EFFECTIF (déduit de la hauteur de prise de vue, ou forcé à
        # la poignée) : c'est lui que le canvas doit afficher
        "horizon": round(cam.y_h / H, 4) if cam else insertion_ia.horizon_actif(data),
        "horizon_ajuste": insertion_ia.horizon_actif(data) is not None,
        "hauteur_vue": insertion_ia.hauteur_prise_vue(data),
        "diagnostic": diag,
        "volumes": [({"sol": norm(v["sol"]), "toit": norm(v["toit"])} if v else None)
                    for v in volumes],
    }


@router.get("/{projet_id}/insertion/volumes")
def lire_volumes(projet_id: str):
    """Filaire de pose : emprise + toiture projetées de chaque ombrière."""
    projet = _charger(projet_id)
    return _volumes_reponse(projet)


# ------------------------------------------------------------------ guides photo (legacy)

@router.get("/{projet_id}/insertion/plan-dims")
def plan_dims(projet_id: str):
    """Cotes (longueur, largeur) des rangées lues sur le plan, pour préremplir."""
    projet = _charger(projet_id)
    dims = insertion_ia.plan_dims(projet.model_dump())
    return {"dims_m": [{"longueur_m": L, "largeur_m": l} for L, l in dims]}


# (route « guides » du flux v5 retirée le 19/07/2026 : remplacée par /pose)


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
