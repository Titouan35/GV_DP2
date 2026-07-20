"""Module Insertion IA (phase 5) — génération directe via l'API Gemini.

Flux 17/07/2026 : au clic « Générer l'insertion », l'outil assemble un prompt
ultra-détaillé (6 blocs, déterministe) + les images d'entrée (photo du site,
plan de masse DP2, coupe du type d'ombrière) et appelle Gemini image
(« Nano Banana »). L'image revient directement dans la galerie.

Les insertions sélectionnées entrent au dossier DP en planches « visuel
d'illustration » ; la pièce DP6 officielle du BE garde la priorité.
L'API Gemini n'exposant aucun solde de crédits, l'outil tient un compteur
de dépense local (nb d'images x coût unitaire).
"""
from __future__ import annotations

import base64
import io
import json
import math
import os
import threading
from datetime import datetime
from pathlib import Path

import httpx
import numpy as np
import pypdfium2 as pdfium
from PIL import Image, ImageOps

from . import config
from .catalogue import CATALOGUE, libelle_coupe, parametres_effectifs

GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"
GEMINI_MODELE_DEFAUT = "gemini-3-pro-image"  # Nano Banana Pro (placement + rendu)
COUT_IMAGE_EUR_DEFAUT = 0.13


class InsertionError(Exception):
    """Erreur du mode API (clé, réseau, quota/facturation, sécurité)."""


def api_configuree() -> bool:
    """Vrai si une clé Gemini est présente."""
    return bool(os.environ.get("GEMINI_API_KEY"))


def _modele() -> str:
    return os.environ.get("GVDP_GEMINI_MODEL", GEMINI_MODELE_DEFAUT)


def cout_image_eur() -> float:
    try:
        return float(os.environ.get("GVDP_COUT_IMAGE_EUR", COUT_IMAGE_EUR_DEFAUT))
    except ValueError:
        return COUT_IMAGE_EUR_DEFAUT


# --- compteur de dépense global (tous projets), fichier local gitignoré ---

def _compteur_chemin() -> Path:
    return config.PROJETS_DIR / "_compteur_ia.json"


def compteur_global() -> int:
    try:
        return int(json.loads(_compteur_chemin().read_text(encoding="utf-8"))["images"])
    except (OSError, ValueError, KeyError):
        return 0


# le read-modify-write du compteur n'est pas atomique : deux générations
# simultanées perdraient un incrément sans ce verrou. Écriture via fichier
# temporaire + os.replace pour ne jamais laisser un JSON tronqué (OneDrive).
_COMPTEUR_LOCK = threading.Lock()


def incrementer_compteur_global(n: int = 1) -> int:
    with _COMPTEUR_LOCK:
        total = compteur_global() + max(1, int(n))
        config.PROJETS_DIR.mkdir(parents=True, exist_ok=True)
        tmp = _compteur_chemin().with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"images": total}), encoding="utf-8")
        os.replace(tmp, _compteur_chemin())
    return total


# --- journal des générations (analyse a posteriori : quel prompt marche,
#     quel projet a coûté quoi). Une ligne JSON par génération, sous verrou. ---
_JOURNAL_LOCK = threading.Lock()


def _journal_chemin() -> Path:
    return config.PROJETS_DIR / "_journal_ia.jsonl"


def journaliser_generation(entree: dict) -> None:
    """Append d'une ligne au journal. Ne doit jamais faire échouer la génération."""
    try:
        with _JOURNAL_LOCK:
            config.PROJETS_DIR.mkdir(parents=True, exist_ok=True)
            with open(_journal_chemin(), "a", encoding="utf-8") as f:
                f.write(json.dumps(entree, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _ouvrir_image(chemin: Path) -> Image.Image:
    """Ouvre une image REDRESSÉE selon son tag EXIF Orientation.

    Les photos de smartphone portent presque toujours une orientation EXIF :
    le navigateur les affiche redressées (les tracés 0-1 de l'utilisateur sont
    donc faits sur l'image droite) alors que PIL les lit brutes. Sans cette
    correction, les repères tombaient à côté et Gemini recevait une photo
    couchée — cause n°1 de générations ratées.
    """
    with Image.open(chemin) as brut:
        return ImageOps.exif_transpose(brut).copy()


def apercu() -> dict:
    """Descriptif générique du module (indépendant d'un projet)."""
    return {
        "api_configuree": api_configuree(),
        "api_modele": _modele(),
        "cout_image_eur": cout_image_eur(),
        "images_global": compteur_global(),
    }


# ------------------------------------------------------------------ prompt

def _fmt(v, suffixe="", defaut="—"):
    """Formatte un nombre à la française (virgule décimale), sinon tel quel."""
    if v is None:
        return defaut
    if isinstance(v, (int, float)):
        return f"{v:g}".replace(".", ",") + suffixe
    return f"{v}{suffixe}"


# ------------------------------------------------------------------ kit d'images

def _document_image(projet: dict, code: str) -> Path | None:
    """Chemin du document uploadé `code` s'il existe (image ou PDF)."""
    doc = (projet.get("documents") or {}).get(code)
    if not doc or not doc.get("fichier"):
        return None
    chemin = config.PROJETS_DIR / doc["fichier"]
    return chemin if chemin.exists() else None


def _coupe_source(projet: dict) -> Path | None:
    """Chemin du PDF de coupe type (catalogue) pour la famille choisie."""
    famille = (projet.get("ombriere") or {}).get("famille")
    entree = CATALOGUE.get(famille)
    if not entree:
        return None
    chemin = config.COUPES_DIR / entree["coupe_pdf"]
    return chemin if chemin.exists() else None


def _pdf_premiere_page_png(chemin_pdf: Path, sortie: Path, dpi: int = 200) -> Path:
    """Rend la 1re page d'un PDF en PNG (cache) pour l'inclure au kit."""
    with config.PDFIUM_LOCK:
        doc = pdfium.PdfDocument(str(chemin_pdf))
        try:
            image = doc[0].render(scale=dpi / 72).to_pil()
            sortie.parent.mkdir(parents=True, exist_ok=True)
            image.save(sortie)
        finally:
            doc.close()
    return sortie


def _coupe_nettoyee(chemin_pdf: Path, sortie: Path, dpi: int = 200) -> Path:
    """Coupe du catalogue rendue POUR GEMINI : sans cartouche ni bandes bleues.

    Le gabarit Solstyce porte un cadre et un bandeau cartouche en bas de page
    (logo, adresse, échelle) : recadré. Les bandes bleues de signalisation du
    poteau contredisent la consigne « sans marquage de couleur » : recolorées
    dans le gris de l'acier. Cache par date du PDF.
    """
    if sortie.exists() and sortie.stat().st_mtime >= chemin_pdf.stat().st_mtime:
        return sortie
    with config.PDFIUM_LOCK:
        doc = pdfium.PdfDocument(str(chemin_pdf))
        try:
            image = doc[0].render(scale=dpi / 72).to_pil().convert("RGB")
        finally:
            doc.close()
    l, h = image.size
    image = image.crop((round(l * 0.015), round(h * 0.015),
                        round(l * 0.985), round(h * 0.93)))
    arr = np.array(image)
    r = arr[..., 0].astype(np.int16)
    g = arr[..., 1].astype(np.int16)
    b = arr[..., 2].astype(np.int16)
    bandes = (b > 150) & (b > r + 60) & (b > g + 60)  # bleu vif de signalisation
    arr[bandes] = (176, 183, 191)                     # gris acier galvanisé
    sortie.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(arr).save(sortie)
    return sortie


def _coupe_be_nettoyee(chemin_pdf: Path, sortie: Path, dpi: int = 200) -> Path:
    """Coupe DP3 du BE rendue POUR GEMINI : sans l'habillage du gabarit GVN.

    Le modèle IMITE la mise en page des documents joints (constaté 17/07 :
    cartouche et bloc caractéristiques recopiés autour du montage). Gabarit
    GVN constant (confirmé Florent) : cadre fin, bandeau cartouche en bas,
    bloc « Caractéristiques techniques » en bas à droite -> crop + blanchiment.
    Cache par date du PDF.
    """
    if sortie.exists() and sortie.stat().st_mtime >= chemin_pdf.stat().st_mtime:
        return sortie
    with config.PDFIUM_LOCK:
        doc = pdfium.PdfDocument(str(chemin_pdf))
        try:
            image = doc[0].render(scale=dpi / 72).to_pil().convert("RGB")
        finally:
            doc.close()
    l, h = image.size
    image = image.crop((round(l * 0.02), round(h * 0.02),
                        round(l * 0.98), round(h * 0.87)))
    from PIL import ImageDraw
    dr = ImageDraw.Draw(image)
    lc, hc = image.size
    dr.rectangle([round(lc * 0.66), round(hc * 0.72), lc, hc], fill=(255, 255, 255))
    sortie.parent.mkdir(parents=True, exist_ok=True)
    image.save(sortie)
    return sortie


def image_kit(projet: dict, role: str) -> Path | None:
    """Résout le fichier image d'un rôle du kit (photo / plan / coupe).

    Les PDF (plan de masse, coupe) sont convertis en PNG et mis en cache dans
    le dossier assets du projet. Renvoie None si l'input n'est pas disponible.
    """
    projet_id = projet.get("id")
    assets = config.assets_dir(projet_id)

    if role == "photo":
        photo = (projet.get("insertion") or {}).get("photo")
        if not photo:
            return None
        chemin = config.PROJETS_DIR / photo
        return chemin if chemin.exists() else None

    if role == "plan":
        source = _document_image(projet, "dp2")
        if not source:
            return None
        if source.suffix.lower() == ".pdf":
            return _pdf_premiere_page_png(source, assets / "kit_plan_masse.png")
        return source

    if role == "coupe_be":
        # coupe DP3 importée par le bureau d'études : elle PRIME sur le catalogue
        source = _document_image(projet, "dp3")
        if not source:
            return None
        if source.suffix.lower() == ".pdf":
            return _coupe_be_nettoyee(source, assets / "kit_coupe_be.png")
        return source

    if role == "coupe":
        source = _coupe_source(projet)
        if not source:
            return None
        return _coupe_nettoyee(source, assets / "kit_coupe.png")

    return None


MAGENTA = (255, 0, 200)   # repère du bord avant tracé sur la photo


# ------------------------------------------------------------------ vue aérienne

def _analyse_plan(projet: dict):
    """Analyse (implantation) du plan de masse PDF, ou None."""
    source = _document_image(projet, "dp2")
    if not source or source.suffix.lower() != ".pdf":
        return None, None
    from . import implantation as mod_implantation
    return mod_implantation.analyser_plan(source), source


def plan_dims(projet: dict) -> list[tuple[float, float]]:
    """Cotes (longueur, largeur) en m des rangées lues sur le plan de masse."""
    analyse, _ = _analyse_plan(projet)
    if not analyse:
        return []
    return [(z["longueur_m"], z["largeur_m"])
            for z in analyse["rangees"] if "longueur_m" in z]


# ================================================================== FLUX « UN GESTE »
# (refonte 17/07/2026 soir) : la seule saisie de placement est UN cliqué-glissé
# du bord AVANT de l'ombrière sur la photo. La profondeur, les hauteurs, la pente
# et le côté du poteau viennent du TYPE (ombriere.famille, catalogue). Gemini
# reçoit : la photo repérée + une PHOTO DE RÉFÉRENCE réelle du type + un prompt
# court. Plus de plan de masse, de coupe technique ni de traçage multi-lignes.

def poses_actives(projet: dict) -> list[dict] | None:
    """Ombrières tracées sur la photo active, triées de GAUCHE à DROITE.

    Chaque entrée : {bord_avant, famille, longueur_m, profondeur_m}. Le type est
    propre à chaque ombrière (repli : celui du projet, puis catalogue) ; les cotes
    sont pré-remplies depuis le plan de masse côté UI et restent modifiables.
    L'ordre gauche->droite sert à décrire les ombrières dans le prompt : aucun
    numéro n'est écrit sur l'image, ce qui déteindrait sur le rendu.
    Compatible avec l'ancien format mono ({"bord_avant": [...]}).
    """
    ins = projet.get("insertion") or {}
    photo = ins.get("photo")
    if not photo:
        return None
    brut = (ins.get("poses") or {}).get(photo) or {}
    liste = brut.get("ombrieres")
    if liste is None and brut.get("bord_avant"):
        liste = [{"bord_avant": brut["bord_avant"]}]      # ancien format
    defaut = (projet.get("ombriere") or {}).get("famille") or "START PLAINE Bas"

    ombrieres = []
    for o in (liste or []):
        ba = o.get("bord_avant")
        if not (ba and len(ba) == 2
                and all(isinstance(p, (list, tuple)) and len(p) == 2 for p in ba)):
            continue
        famille = o.get("famille") if o.get("famille") in CATALOGUE else defaut
        entree = CATALOGUE.get(famille, CATALOGUE["START PLAINE Bas"])
        # sens de la pente : où se trouve le POINT HAUT du rampant, vu du
        # photographe. "fond" = la toiture monte en s'éloignant (défaut),
        # "avant" = elle descend en s'éloignant.
        pente_vers = o.get("pente_vers") if o.get("pente_vers") in ("fond", "avant") else "fond"
        ombrieres.append({
            "bord_avant": [list(ba[0]), list(ba[1])],
            "famille": famille,
            "longueur_m": o.get("longueur_m"),
            "profondeur_m": o.get("profondeur_m") or entree["profondeur_m"],
            "pente_vers": pente_vers,
        })
    if not ombrieres:
        return None
    ombrieres.sort(key=lambda o: (o["bord_avant"][0][0] + o["bord_avant"][1][0]) / 2)
    return ombrieres


def _familles_tracees(projet: dict) -> list[str]:
    """Types distincts présents sur la photo, dans l'ordre d'apparition."""
    vues, ordre = set(), []
    for o in (poses_actives(projet) or []):
        if o["famille"] not in vues:
            vues.add(o["famille"])
            ordre.append(o["famille"])
    if not ordre:
        ordre = [(projet.get("ombriere") or {}).get("famille") or "START PLAINE Bas"]
    return ordre


def _reference_photos(projet: dict) -> list[Path]:
    """Photos de référence réelles, une par type distinct tracé (max 2)."""
    chemins = []
    for famille in _familles_tracees(projet)[:2]:
        entree = CATALOGUE.get(famille) or CATALOGUE["START PLAINE Bas"]
        nom = entree.get("reference_photo")
        if not nom:
            continue
        chemin = config.REFERENCES_DIR / nom
        if chemin.exists() and chemin not in chemins:
            chemins.append(chemin)
    return chemins


def _reference_photo(projet: dict) -> Path | None:
    """Première photo de référence (compat : route /insertion/reference)."""
    refs = _reference_photos(projet)
    return refs[0] if refs else None


def _coupe_be_pertinente(projet: dict) -> Path | None:
    """Coupe DP3 du bureau d'études : elle fait TOUJOURS foi quand elle existe.

    Règle métier (Florent, 18/07/2026) : la DP3 est la coupe du projet réel,
    c'est elle qu'on construit. Elle prime donc sur les coupes types du
    catalogue en toutes circonstances ; ces dernières ne servent que si le BE
    n'a pas encore fourni de coupe.
    """
    return image_kit(projet, "coupe_be")


def _coupes_payload(projet: dict) -> list[Path]:
    """Coupes techniques à joindre (précision de la structure).

    La coupe DP3 du bureau d'études prime toujours quand elle existe. Sinon,
    la coupe type du catalogue pour chaque type distinct tracé (max 2). Toutes
    sont nettoyées de leur cartouche avant envoi, le modèle recopiant volontiers
    la mise en page des documents joints.
    """
    be = _coupe_be_pertinente(projet)
    if be:
        return [be]
    chemins = []
    for famille in _familles_tracees(projet)[:2]:
        entree = CATALOGUE.get(famille)
        if not entree:
            continue
        source = config.COUPES_DIR / entree["coupe_pdf"]
        if not source.exists():
            continue
        sortie = config.assets_dir(projet.get("id")) / f"kit_coupe_{famille.replace(' ', '_')}.png"
        try:
            chemins.append(_coupe_nettoyee(source, sortie))
        except OSError:
            continue
    return chemins


def _fuite(a, b):
    """Direction perpendiculaire au bord avant, vers le HAUT de l'image (fuyante)."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    n = math.hypot(dx, dy) or 1.0
    px, py = -dy / n, dx / n
    if py > 0:                       # on garde la perpendiculaire qui monte
        px, py = -px, -py
    return px, py


def _points_bord(o: dict, W: int, H: int):
    """Extrémités pixel du bord avant d'une ombrière tracée."""
    return ((o["bord_avant"][0][0] * W, o["bord_avant"][0][1] * H),
            (o["bord_avant"][1][0] * W, o["bord_avant"][1][1] * H))


# ------------------------------------------------------------------ perspective

def horizon_actif(projet: dict) -> float | None:
    """Ordonnée d'horizon (0-1) enregistrée pour la photo active, ou None."""
    ins = projet.get("insertion") or {}
    photo = ins.get("photo")
    if not photo:
        return None
    v = ((ins.get("poses") or {}).get(photo) or {}).get("horizon")
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return v if 0.02 <= v <= 0.95 else None


def _camera_photo(projet: dict, W: int, H: int, chemin_photo) -> "perspective.Camera | None":
    """Caméra calibrée pour la photo active, ou None (repli approximatif).

    Horizon : valeur ajustée par l'utilisateur (poignée), sinon détection des
    fuyantes, sinon défaut 0,42 x H (léger piqué vers le sol, typique d'une
    photo de parking debout). Échelle : bord avant tracé le plus long qui
    porte une longueur réelle.
    """
    from . import perspective

    ombrieres = poses_actives(projet) or []
    candidates = [o for o in ombrieres if o.get("longueur_m")]
    if not candidates:
        return None
    ref = max(candidates,
              key=lambda o: math.hypot(*(b - a for a, b in
                                         zip(*_points_bord(o, W, H)))))
    y_h = horizon_actif(projet)
    if y_h is None:
        y_h = perspective.proposer_horizon(chemin_photo)
    y_h_px = (y_h if y_h is not None else 0.42) * H
    f = perspective.focale_px(chemin_photo, W)
    a, b = _points_bord(ref, W, H)
    return perspective.calibrer(W, H, y_h_px, f, a, b, ref["longueur_m"])


def volumes_poses(projet: dict, W: int, H: int, chemin_photo) -> list[dict | None]:
    """Volume 3D (sol + toit, px image) de chaque ombrière tracée.

    Une entrée par ombrière, None quand la géométrie n'est pas calculable
    (pas de cote, horizon incohérent) : l'appelant retombe alors sur
    l'ancienne approximation pour CETTE ombrière.
    """
    from . import perspective

    ombrieres = poses_actives(projet) or []
    cam = _camera_photo(projet, W, H, chemin_photo)
    if cam is None:
        return [None] * len(ombrieres)
    volumes: list[dict | None] = []
    for o in ombrieres:
        g = _geometrie_ombriere(o, projet)
        vers_fond = o.get("pente_vers", "fond") == "fond"
        h_avant = g["h_bas"] if vers_fond else g["h_haut"]
        h_fond = g["h_haut"] if vers_fond else g["h_bas"]
        a, b = _points_bord(o, W, H)
        volumes.append(perspective.volume_ombriere(
            cam, a, b, o.get("profondeur_m") or g["prof"], h_avant, h_fond))
    return volumes


def photo_reperee(projet: dict) -> Path | None:
    """Photo active repérée : EMPRISE AU SOL complète (quadrilatère magenta,
    bord avant plus épais) quand la perspective est calculable, sinon le seul
    bord avant (ancien comportement).

    19/07/2026 (défaut n°1 : placement/dimensionnement) : avec le seul bord
    avant, le modèle devait deviner la profondeur en perspective. Le
    quadrilatère projeté au sol (convergence réelle) la lui donne. Toujours
    des traits fins sans texte ni flèche — tout marquage riche envoyé au
    modèle finit redessiné dans l'image (leçons v5).
    """
    from PIL import ImageDraw

    ombrieres = poses_actives(projet)
    photo = image_kit(projet, "photo")
    if not ombrieres or not photo:
        return None
    image = _ouvrir_image(photo).convert("RGB")
    W, H = image.size
    dr = ImageDraw.Draw(image)
    ep = max(3, round(min(W, H) / 320))
    volumes = volumes_poses(projet, W, H, photo)
    for o, vol in zip(ombrieres, volumes):
        a, b = _points_bord(o, W, H)
        if vol:
            quad = vol["sol"]
            dr.line([*quad, quad[0]], fill=MAGENTA, width=max(2, ep - 1))
        dr.line([a, b], fill=MAGENTA, width=ep + 1)   # bord avant appuyé
    sortie = config.assets_dir(projet.get("id")) / "photo_reperee.png"
    sortie.parent.mkdir(parents=True, exist_ok=True)
    image.save(sortie)
    return sortie


def _geometrie_ombriere(o: dict, projet: dict) -> dict:
    """Hauteurs et pente EFFECTIVES d'une ombrière tracée.

    Les hauteurs viennent du type de CETTE ombrière ; les valeurs saisies au
    niveau projet (étape Caractéristiques) ne s'appliquent qu'aux ombrières du
    même type, sinon on collerait les hauteurs d'une mono à une double.
    La pente est ensuite DÉDUITE des hauteurs et de la profondeur réelles : la
    pente du catalogue, annoncée telle quelle, contredisait les cotes lues sur
    le plan (ex. 1 m de dénivelé sur 4,3 m = 13°, pas 5°).
    """
    famille = o["famille"]
    entree = CATALOGUE.get(famille, CATALOGUE["START PLAINE Bas"])
    omb = projet.get("ombriere") or {}
    meme_type = omb.get("famille") == famille
    h_bas = (omb.get("garde_au_sol_m") if meme_type else None) or entree["h_bas_m"]
    h_haut = (omb.get("hauteur_hors_tout_m") if meme_type else None) or entree["h_haut_m"]
    prof = o.get("profondeur_m") or entree["profondeur_m"]
    pente = entree["pente_deg"]
    if prof and prof > 0 and h_haut > h_bas:
        pente = round(math.degrees(math.atan((h_haut - h_bas) / prof)), 1)
    return {"h_bas": h_bas, "h_haut": h_haut, "prof": prof, "pente": pente,
            "entree": entree}


def _descriptif_ombriere(o: dict, projet: dict) -> str:
    """Phrase décrivant la structure d'une ombrière (profil, poteaux, hauteurs).

    Le profil DOUBLE est décrit sans jamais parler de « deux versants » :
    constaté le 18/07/2026, cette formulation faisait dessiner une arête au
    milieu (profil en Y ou en V inversé) alors que la coupe Solstyce est un T,
    c'est-à-dire UNE seule toiture inclinée d'un seul tenant sur poteau central.
    """
    g = _geometrie_ombriere(o, projet)
    e = g["entree"]
    vers_fond = o.get("pente_vers", "fond") == "fond"
    haut_ou = "au fond, loin du spectateur" if vers_fond else "devant, côté spectateur"
    sens = ("la toiture monte en s'éloignant du spectateur" if vers_fond
            else "la toiture descend en s'éloignant du spectateur")

    if e["double"]:
        profil = ("de type DOUBLE : une file de poteaux centraux uniques portant "
                  "UNE SEULE toiture inclinée d'un seul tenant, qui déborde en "
                  "porte-à-faux de part et d'autre du poteau. En coupe, "
                  "l'ensemble dessine un T. La pente descend de façon continue et "
                  "régulière d'un bord à l'autre : la toiture reste un plan "
                  "unique, sans arête ni sommet au milieu")
        poteaux = ""
    else:
        cote = "du côté haut" if e["poteau"] == "haut" else "du côté bas"
        profil = (f"de type MONOPENTE : une seule file de poteaux {cote} du "
                  "versant, portant UNE SEULE toiture inclinée d'un seul tenant")
        # le côté du poteau se déduit du type ET du sens de la pente : cela
        # fixe entièrement la silhouette vue du photographe.
        poteau_fond = (e["poteau"] == "haut") == vers_fond
        poteaux = (" Ses poteaux sont donc "
                   + ("au fond, du côté éloigné du spectateur."
                      if poteau_fond else "devant, du côté du spectateur."))

    return (f"{profil}. Hauteur libre {_fmt(g['h_bas'])} m au point bas et "
            f"{_fmt(g['h_haut'])} m au point haut, soit une pente douce "
            f"d'environ {_fmt(g['pente'])}°. SENS DE LA PENTE : son point haut "
            f"est {haut_ou}, autrement dit {sens}.{poteaux}")


def construire_prompt_pose(projet: dict, affinage: str = "", pose: bool = True) -> str:
    """Prompt Gemini du flux « un geste », une ou plusieurs ombrières."""
    ins = projet.get("insertion") or {}
    ombrieres = poses_actives(projet) or []
    n = len(ombrieres)
    pluriel = n > 1

    tete = ("Insère "
            + (f"{n} ombrières photovoltaïques de parking" if pluriel
               else "une ombrière photovoltaïque de parking")
            + " dans cette photographie, de façon photoréaliste, comme si "
            + ("elles avaient" if pluriel else "elle avait") + " toujours été là.")
    blocs = [tete]

    # les emprises complètes (quadrilatères projetés) sont-elles dessinées ?
    quads = False
    photo_active = image_kit(projet, "photo")
    if pose and ombrieres and photo_active:
        try:
            W_p, H_p = _ouvrir_image(photo_active).size
            quads = any(volumes_poses(projet, W_p, H_p, photo_active))
        except OSError:
            quads = False

    if pose and ombrieres:
        if quads:
            blocs.append(
                f"PLACEMENT. L'image à éditer porte {n} cadre"
                f"{'s' if pluriel else ''} magenta dessiné"
                f"{'s' if pluriel else ''} en perspective sur le sol : chacun "
                "délimite EXACTEMENT l'emprise au sol d'une ombrière. Construis "
                f"EXACTEMENT {n} ombrière{'s' if pluriel else ''} : une par "
                "cadre, ni plus, ni moins, et aucune ailleurs dans l'image. La "
                "toiture couvre toute la surface du cadre, ni plus, ni moins ; "
                "les poteaux se posent à l'intérieur du cadre. Le côté du cadre "
                "au trait le plus épais est le bord AVANT, le plus proche du "
                "spectateur. Les cadres magenta sont de simples GUIDES de "
                "tracé : ils ne doivent pas apparaître dans l'image finale, "
                "remplace-les par le sol et la structure. N'ajoute aucune "
                "flèche, aucun trait de couleur, aucun symbole ni aucun texte.")
        else:
            blocs.append(
                f"PLACEMENT. L'image à éditer porte {n} trait"
                f"{'s' if pluriel else ''} magenta, et tu dois construire "
                f"EXACTEMENT {n} ombrière{'s' if pluriel else ''} : une par trait, "
                "ni plus, ni moins, et aucune ailleurs dans l'image. Chaque trait "
                "magenta est le bord AVANT d'une ombrière, posé au sol, du côté le "
                "plus proche du spectateur : la base des poteaux avant repose "
                "précisément sur ce trait, sur toute sa longueur, et la toiture "
                "s'éloigne du spectateur vers le fond de l'image. Ne déplace, "
                "n'allonge ni ne raccourcis aucun trait. Les traits magenta sont de "
                "simples GUIDES de tracé : ils ne doivent pas apparaître dans l'image "
                "finale, remplace-les par le sol et la structure. N'ajoute aucune "
                "flèche, aucun trait de couleur, aucun symbole ni aucun texte.")
        # cotes réelles, ombrière par ombrière, dans l'ordre gauche -> droite
        details = []
        for i, o in enumerate(ombrieres, 1):
            rang = (f"Ombrière {i} (le {i}{'er' if i == 1 else 'e'} trait en "
                    "partant de la gauche)") if pluriel else "L'ombrière"
            cotes = []
            if o.get("longueur_m"):
                cotes.append(f"{_fmt(o['longueur_m'])} m de long")
            if o.get("profondeur_m"):
                cotes.append(f"{_fmt(o['profondeur_m'])} m de profondeur")
            mesure = (" mesure " + " sur ".join(cotes)) if cotes else ""
            details.append(f"{rang}{mesure}. Elle est "
                           f"{_descriptif_ombriere(o, projet)}")
        # NB : ne jamais titrer ce bloc « COTES » — constaté le 18/07/2026, le
        # modèle traçait alors de vraies lignes de cote chiffrées sur la photo.
        blocs.append(
            "PROPORTIONS (ces mesures servent uniquement à dimensionner les "
            "volumes ; elles ne doivent jamais apparaître dans l'image). "
            + " ".join(details))
    else:
        omb = projet.get("ombriere") or {}
        defaut = {"famille": omb.get("famille") or "START PLAINE Bas"}
        placement = ("PLACEMENT. Implante l'ombrière sur la zone de stationnement "
                     "la plus dégagée et cohérente de la photo.")
        if omb.get("orientation") is not None:
            # sans tracé, l'azimut du plan de masse est la seule indication
            # d'orientation disponible : autant la donner au modèle.
            placement += (f" Les rangées du projet sont orientées selon un azimut "
                          f"d'environ {_fmt(omb['orientation'])}° : aligne l'ombrière "
                          "sur les files de stationnement qui suivent cette direction.")
        blocs.append(placement)
        blocs.append("STRUCTURE. Ombrière "
                     + _descriptif_ombriere(defaut, projet) + ".")

    # repère d'échelle saisi par l'utilisateur : jusqu'ici collecté par l'UI
    # mais jamais injecté dans le prompt (constaté à l'audit du 19/07/2026).
    echelle_desc = (ins.get("echelle_desc") or "").strip()
    echelle_m = ins.get("echelle_distance_m")
    if echelle_desc and echelle_m:
        blocs.append(
            f"ECHELLE. Repère de taille réelle, visible sur la photo : "
            f"{echelle_desc} mesure {_fmt(echelle_m)} m. Sers-t'en pour caler la "
            "taille des volumes ; ne dessine ni ce repère, ni aucune cote.")

    # règle métier absolue (Florent) : Greenvolt ne pose jamais d'ombrière en Y.
    # Elle vaut pour TOUS les types, mono comme double.
    blocs.append(
        "TOITURE. Règle absolue, valable pour chaque ombrière : la toiture est un "
        "PLAN UNIQUE incliné d'un seul tenant, d'un bord à l'autre. Jamais deux "
        "versants opposés, jamais de faîtage ni d'arête au sommet, jamais de "
        "profil en V, en Y ou en papillon. Vue de bout, on ne voit qu'une seule "
        "ligne droite inclinée posée sur ses poteaux.")

    blocs.append(
        "MATERIAUX. Structure en acier galvanisé gris clair (poteaux caisson, "
        "poutres et arbalétriers), toiture de modules photovoltaïques NOIRS et "
        "mats (full black) alignés en trame régulière, sous-face claire.")

    familles = _familles_tracees(projet)[:2]
    noms = [libelle_coupe(f) for f in familles]

    coupes = _coupes_payload(projet)
    if coupes:
        if _coupe_be_pertinente(projet):
            quoi = ("Le dessin technique joint est LA coupe du projet, tracée par "
                    "le bureau d'études : c'est la structure qui sera réellement "
                    "construite, elle prime sur tout le reste")
        elif len(coupes) > 1:
            quoi = ("Les dessins techniques joints sont les coupes types, dans "
                    f"l'ordre : {' puis '.join(noms[:len(coupes)])}")
        else:
            quoi = f"Le dessin technique joint est la coupe type {noms[0]}"
        foi = "Elles font foi" if len(coupes) > 1 else "Elle fait foi"
        blocs.append(
            f"COUPE. {quoi}. {foi} pour la géométrie : silhouette vue de bout, "
            "position des poteaux sous la toiture, inclinaison, porte-à-faux et "
            "proportions. Respecte ce profil exactement. Ne recopie dans l'image "
            "ni trait de cote, ni cartouche, ni texte de ces dessins.")

    refs = _reference_photos(projet)
    if refs:
        if len(refs) > 1:
            quoi = ("Les photographies jointes montrent de vraies ombrières, dans "
                    f"l'ordre : {' puis '.join(noms[:len(refs)])}")
        else:
            quoi = "La photographie jointe montre une vraie ombrière de ce genre"
        blocs.append(
            f"REFERENCE. {quoi}. Elles servent uniquement de repère de réalisme : "
            "aspect de l'acier galvanisé, finesse des profilés, façon dont la "
            "lumière accroche la structure. N'en recopie NI le fond, NI les "
            "voitures, NI le ciel, NI la couleur des panneaux (garde des modules "
            "noirs), et si leur silhouette diffère de la coupe, c'est la COUPE qui "
            "l'emporte.")

    sous_quoi = "chaque ombrière" if pluriel else "l'ombrière"
    blocs.append(
        "INTEGRATION. Garde le reste de la scène rigoureusement identique : les "
        "voitures, le revêtement du sol et ses marquages, les bordures, les arbres, "
        "les bâtiments et le ciel restent exactement à leur place. Reproduis la "
        f"lumière du jour et la direction des ombres de la photo ; pose sous "
        f"{sous_quoi} une ombre portée douce, orientée comme celles déjà visibles "
        "dans la scène. Les poteaux sont verticaux et posés sur le bitume.")

    # dernier bloc = celui qui pèse le plus : on y remet l'interdiction
    # d'annotation, le modèle ayant tendance à dessiner les mesures reçues.
    rendu = ["RENDU. Le résultat est une photographie plein cadre, au même cadrage "
             "que l'originale, telle qu'on la prendrait sur place : aucun texte, "
             "aucun chiffre, aucune cote ni ligne de mesure, aucune flèche, aucun "
             "repère de couleur, aucun logo ni filigrane."]
    libres = (ins.get("consignes") or "").strip()
    corr = (affinage or ins.get("affinage") or "").strip()
    if libres:
        rendu.append(libres if libres.endswith((".", "!", "?")) else libres + ".")
    if corr:
        rendu.append(corr if corr.endswith((".", "!", "?")) else corr + ".")
    blocs.append(" ".join(rendu))
    return "\n\n".join(blocs)


def _bande_marqueur(projet: dict, taille: tuple[int, int]) -> np.ndarray | None:
    """Masque de la géométrie du repère (bord avant + flèche), dilaté, à `taille`.

    On connaît EXACTEMENT où le repère a été dessiné : on limite l'effacement à
    cette bande, pour ne jamais toucher le reste de la scène (enseignes rouges…).
    """
    from PIL import Image, ImageDraw

    ombrieres = poses_actives(projet)
    if not ombrieres:
        return None
    W, H = taille
    ep = max(10, round(min(W, H) / 90))
    lg = 0.12 * min(W, H)
    img = Image.new("L", (W, H), 0)
    dr = ImageDraw.Draw(img)
    photo = image_kit(projet, "photo")
    volumes = (volumes_poses(projet, W, H, photo) if photo
               else [None] * len(ombrieres))
    for o, vol in zip(ombrieres, volumes):
        a, b = _points_bord(o, W, H)
        dr.line([a, b], fill=255, width=ep)
        if vol:
            # les arêtes du quadrilatère d'emprise sont aussi dessinées sur la
            # photo envoyée : leur bande doit être effaçable de la même façon
            quad = vol["sol"]
            dr.line([*quad, quad[0]], fill=255, width=ep)
        else:
            mx, my = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
            fx, fy = _fuite(a, b)
            dr.line([(mx, my), (mx + fx * lg, my + fy * lg)], fill=255, width=ep)
    return np.asarray(img) > 0


def effacer_marqueur(projet: dict, photo_propre: Path, image_generee: bytes) -> bytes:
    """Retire les pixels résiduels du repère (magenta/cyan) laissés par Gemini.

    Le repère de pose est dessiné SUR la photo envoyée ; le modèle le conserve
    souvent. On efface uniquement dans la BANDE géométrique connue du repère
    (jamais ailleurs), aux pixels de teinte magenta/cyan, et on les remplit avec
    une version FLOUTÉE du rendu lui-même : le contenu local (ombre, structure)
    est préservé, sans cicatrice claire d'un recollage de la photo ensoleillée.
    """
    import io

    from PIL import Image, ImageFilter

    try:
        gen = Image.open(io.BytesIO(image_generee)).convert("RGB")
    except OSError:
        return image_generee
    bande = _bande_marqueur(projet, gen.size)
    if bande is None:
        return image_generee
    arr = np.asarray(gen).astype(np.int16)
    r, g, b = arr[..., 0], arr[..., 1], arr[..., 2]

    # Deux niveaux (18/07/2026). Le modèle ne se contente pas de conserver le
    # repère : il le REDESSINE déplacé et allongé, très au-delà du tracé
    # d'origine (constaté sur Anse : 27 477 pixels roses sur 27 480 tombaient
    # hors de la bande géométrique). Restreindre à la bande ne suffit donc pas.
    #
    # Le discriminant fiable n'est pas la saturation mais l'ÉQUILIBRE R≈B :
    # le magenta a rouge et bleu au-dessus du vert et proches l'un de l'autre
    # (résidu mesuré sur Anse : R149 G109 B148, b-g=38, |r-b|=1), alors qu'un
    # rouge d'enseigne a le bleu très en dessous du rouge (R220 G30 B60,
    # b-g=30, |r-b|=160) et le ciel un rouge bas. Ce test permet d'effacer le
    # repère PARTOUT sans mordre sur la scène.
    magenta = (r - g > 25) & (b - g > 25) & (abs(r - b) < 45) & (r > 90)
    # bords très estompés et cyan résiduel : ambigus, donc bande seule
    magenta_tres_pale = (r - g > 12) & (b - g > 8) & (r > 80)
    cyan = (g - r > 45) & (b - r > 45) & (r < 130) & (g > 140)
    marque = magenta | (bande & (magenta_tres_pale | cyan))
    if not marque.any():
        return image_generee
    masque = Image.fromarray((marque * 255).astype(np.uint8)).filter(ImageFilter.MaxFilter(9))
    m = np.asarray(masque) > 0

    # comblement par convolution normalisée : chaque pixel du repère est remplacé
    # par la moyenne des pixels VALIDES (hors repère) de son voisinage. Le repère
    # est donc exclu de ses propres sources (un simple flou, lui, resterait rose
    # car le trait est plus large que le rayon). Fenêtre carrée via image intégrale.
    def _boxsum(a: np.ndarray, r: int) -> np.ndarray:
        H, W = a.shape
        ii = np.zeros((H + 1, W + 1), dtype=np.float64)
        ii[1:, 1:] = np.cumsum(np.cumsum(a, axis=0), axis=1)
        y0 = np.clip(np.arange(H) - r, 0, H)
        y1 = np.clip(np.arange(H) + r + 1, 0, H)
        x0 = np.clip(np.arange(W) - r, 0, W)
        x1 = np.clip(np.arange(W) + r + 1, 0, W)
        return (ii[np.ix_(y1, x1)] - ii[np.ix_(y0, x1)]
                - ii[np.ix_(y1, x0)] + ii[np.ix_(y0, x0)])

    rayon = max(20, round(min(gen.size) / 55))   # > largeur du trait
    valide = (~m).astype(np.float64)
    den = _boxsum(valide, rayon) + 1e-6
    g32 = np.asarray(gen).astype(np.float64)
    out = np.asarray(gen).astype(np.float64).copy()
    for c in range(3):
        comble = _boxsum(g32[..., c] * valide, rayon) / den
        canal = out[..., c]
        canal[m] = comble[m]
        out[..., c] = canal
    tampon = io.BytesIO()
    Image.fromarray(np.clip(out, 0, 255).astype(np.uint8)).save(tampon, "PNG")
    return tampon.getvalue()


def _preparer_requete_pose(projet: dict, affinage: str = "") -> dict:
    """Lot du flux « un geste » : [photo repérée, coupe(s), référence(s)] + prompt.

    La coupe (DP3 du BE si fournie, sinon catalogue) donne le profil exact ; la
    photo de référence donne le réalisme. Les deux sont réduites à l'envoi.
    """
    base_propre = image_kit(projet, "photo")
    if not base_propre:
        raise InsertionError("Ajoutez d'abord une photo du site (upload ou reprise d'une pièce BE).")
    ombrieres = poses_actives(projet)
    reperee = photo_reperee(projet) if ombrieres else None
    base, mode = (reperee, "pose") if reperee else (base_propre, "libre")

    chemins: list[Path] = [base]
    roles = ["photo"]
    for coupe in _coupes_payload(projet):
        chemins.append(coupe)
        roles.append("coupe")
    for ref in _reference_photos(projet):
        chemins.append(ref)
        roles.append("reference")

    prompt = construire_prompt_pose(projet, affinage=affinage, pose=bool(ombrieres))
    return {"chemins": chemins, "roles": roles, "prompt": prompt,
            "base_propre": base_propre, "mode": mode}


def _bandes_pose(projet: dict, W: int, H: int) -> list[list[tuple[float, float]]]:
    """Emprises des ombrières pour le contrôle de présence.

    Depuis le 19/07/2026 : l'emprise PROJETÉE (perspective calibrée) quand
    elle est calculable — le contrôle mesure alors la vraie zone attendue et
    la relance auto ne se déclenche plus sur une bande mal estimée. Repli :
    l'ancienne extrusion approximative vers la fuite.
    """
    photo = image_kit(projet, "photo")
    ombrieres = poses_actives(projet) or []
    volumes = (volumes_poses(projet, W, H, photo) if photo
               else [None] * len(ombrieres))
    bandes = []
    for o, vol in zip(ombrieres, volumes):
        if vol:
            # silhouette visible : pieds du bord avant -> coins de toit du fond
            bandes.append([vol["sol"][0], vol["sol"][1],
                           vol["toit"][2], vol["toit"][3]])
            continue
        a, b = _points_bord(o, W, H)
        long_px = math.hypot(b[0] - a[0], b[1] - a[1]) or 1.0
        prof = o.get("profondeur_m") or 5.0
        L = o.get("longueur_m")
        # avec les 2 cotes réelles, le ratio est exact ; sinon largeur nominale 12 m
        ratio = min(1.4, max(0.25, prof / (L if L and L > 0 else 12.0)))
        fx, fy = _fuite(a, b)
        dp = long_px * ratio
        bandes.append([a, b, (b[0] + fx * dp, b[1] + fy * dp),
                       (a[0] + fx * dp, a[1] + fy * dp)])
    return bandes


def controle_pose(projet: dict, photo_propre: Path, image_generee: bytes) -> dict | None:
    """Contrôle de présence pour le flux « un geste » : l'ombrière a-t-elle bien
    été construite sur/derrière le bord avant tracé ? Mesure la part de la bande
    d'emprise approximative réellement modifiée par Gemini. Verdict indicatif
    (placement non garanti au pixel dans ce mode)."""
    import io

    from PIL import Image, ImageDraw, ImageFilter

    try:
        orig = _ouvrir_image(photo_propre).convert("RGB")
        gen = Image.open(io.BytesIO(image_generee)).convert("RGB")
    except OSError:
        return None
    W0, H0 = orig.size
    bandes = _bandes_pose(projet, W0, H0)
    if not bandes:
        return None
    ech = min(1.0, 1000 / max(W0, H0))
    W, H = max(1, round(W0 * ech)), max(1, round(H0 * ech))

    masque = Image.new("L", (W, H), 0)
    dessin = ImageDraw.Draw(masque)
    for bande in bandes:
        dessin.polygon([(x * ech, y * ech) for x, y in bande], fill=255)
    attendu = np.asarray(masque) > 0
    aire = int(attendu.sum())
    if aire == 0:
        return None

    gen = gen.resize((W, H), Image.LANCZOS)
    orig_s = orig.resize((W, H), Image.LANCZOS)
    o = np.asarray(orig_s.convert("L"), dtype=np.float32)
    g = np.asarray(gen.convert("L"), dtype=np.float32)
    g = (g - g.mean()) / (g.std() or 1.0) * (o.std() or 1.0) + o.mean()
    diff = Image.fromarray(np.clip(np.abs(g - o), 0, 255).astype(np.uint8))
    modifie = np.asarray(diff.filter(ImageFilter.GaussianBlur(3))) > 16

    couverture = int((attendu & modifie).sum()) / aire
    verdict = "ok" if couverture >= 0.55 else "partiel" if couverture >= 0.30 else "faible"
    return {"couverture": round(couverture, 3), "verdict": verdict}


# ------------------------------------------------------------------ état projet

def etat(projet: dict) -> dict:
    """Prêt-à-générer d'un projet : photo, plan, coupe, type d'ombrière."""
    omb = projet.get("ombriere") or {}
    photo = bool((projet.get("insertion") or {}).get("photo"))
    return {
        "photo": photo,
        "plan_de_masse": _document_image(projet, "dp2") is not None,
        "coupe": _coupe_source(projet) is not None,
        "type_ombriere": bool(omb.get("famille")),
        "prete": photo,  # la photo suffit à générer un prompt exploitable
    }


# ------------------------------------------------------------------ mode API (Gemini)

_MIMES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
          ".webp": "image/webp"}


def _part_image(chemin: Path, max_px: int | None = None) -> dict:
    """Encode une image pour Gemini, redressée (EXIF) et bornée à `max_px`.

    Le bornage vaut aussi pour la photo à éditer : sans lui, une photo de
    smartphone de 20-40 Mo devenait ~27-53 Mo en base64 et faisait rejeter la
    requête (400 opaque). La résolution de sortie vient du modèle, pas de la
    définition d'entrée. Les images sans EXIF ni excès de taille partent en
    octets bruts (aucune recompression).
    """
    try:
        with Image.open(chemin) as brut:
            orientation = brut.getexif().get(274, 1)  # tag EXIF Orientation
            im = ImageOps.exif_transpose(brut).convert("RGB")
            reencodage = orientation != 1
            if max_px and max(im.size) > max_px:
                ech = max_px / max(im.size)
                im = im.resize((round(im.width * ech), round(im.height * ech)),
                               Image.LANCZOS)
                reencodage = True
            if reencodage:
                buf = io.BytesIO()
                im.save(buf, "JPEG", quality=90)
                return {"inline_data": {"mime_type": "image/jpeg",
                                        "data": base64.b64encode(buf.getvalue()).decode()}}
    except OSError:
        pass
    mime = _MIMES.get(chemin.suffix.lower(), "image/jpeg")
    return {"inline_data": {"mime_type": mime,
                            "data": base64.b64encode(chemin.read_bytes()).decode()}}


def _extraire_image(reponse: dict) -> bytes:
    """Récupère la première image d'une réponse generateContent."""
    for cand in reponse.get("candidates", []):
        for part in (cand.get("content") or {}).get("parts", []):
            inline = part.get("inlineData") or part.get("inline_data")
            if inline and inline.get("data"):
                return base64.b64decode(inline["data"])
    # pas d'image : souvent un blocage sécurité, on remonte le texte explicatif
    for cand in reponse.get("candidates", []):
        for part in (cand.get("content") or {}).get("parts", []):
            if part.get("text"):
                raise InsertionError(f"Aucune image renvoyée : {part['text'][:300]}")
    # motifs structurés (blocage prompt / finishReason) : diagnostic explicite
    # plutôt qu'un « réponse vide » qui pousse à régénérer à l'aveugle
    blocage = (reponse.get("promptFeedback") or {}).get("blockReason")
    if blocage:
        raise InsertionError(f"Génération bloquée par Gemini (motif : {blocage}).")
    fin = next((c.get("finishReason") for c in reponse.get("candidates", [])
                if c.get("finishReason") and c["finishReason"] != "STOP"), None)
    if fin:
        raise InsertionError(f"Aucune image renvoyée (finishReason : {fin}).")
    raise InsertionError("Aucune image renvoyée par le modèle (réponse vide).")


_RATIOS_SUPPORTES = {   # aspect ratios acceptés par Nano Banana -> valeur
    1 / 1: "1:1", 3 / 2: "3:2", 2 / 3: "2:3", 3 / 4: "3:4", 4 / 3: "4:3",
    4 / 5: "4:5", 5 / 4: "5:4", 9 / 16: "9:16", 16 / 9: "16:9", 21 / 9: "21:9",
}


def _ratio_photo(chemin: Path) -> str | None:
    """Aspect ratio Nano Banana correspondant à la photo (garde le cadrage).

    Renvoyé seulement s'il colle à moins de 3 % du ratio réel : forcer un
    ratio éloigné (jusqu'à ~11 % d'écart) déformait le cadrage ET désactivait
    silencieusement `preserver_scene`, dont le garde-fou tolère 3 % d'écart.
    Sans ratio proche, on laisse le modèle suivre le format de la photo.
    """
    try:
        im = _ouvrir_image(chemin)
        r = im.width / im.height
    except OSError:
        return None
    proche, valeur = min(_RATIOS_SUPPORTES.items(), key=lambda kv: abs(kv[0] - r))
    return valeur if abs(proche - r) / r <= 0.03 else None


def _appel_gemini(parts: list[dict], aspect_ratio: str | None = None) -> bytes:
    """generateContent, sans outil (le grounding Google Search a été retiré
    le 17/07/2026 : source d'aléa, la structure vient de la coupe jointe).

    `aspect_ratio` force le cadrage de sortie (imageConfig) ; repli automatique
    sans lui si l'API le refuse (HTTP 400), pour ne jamais bloquer la génération.
    """
    cle = os.environ.get("GEMINI_API_KEY")
    if not cle:
        raise InsertionError("Clé GEMINI_API_KEY absente (fichier .env CLAUDE).")
    url = f"{GEMINI_BASE}/models/{_modele()}:generateContent"
    gen_config: dict = {"responseModalities": ["TEXT", "IMAGE"]}
    if aspect_ratio:
        gen_config["imageConfig"] = {"aspectRatio": aspect_ratio}
    corps: dict = {
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": gen_config,
    }
    try:
        with httpx.Client(timeout=180.0) as client:
            resp = client.post(url, headers={"x-goog-api-key": cle}, json=corps)
    except httpx.HTTPError as exc:
        raise InsertionError(f"Appel Gemini impossible ({exc.__class__.__name__}).") from exc
    if (resp.status_code == 400 and aspect_ratio
            and ("imageconfig" in resp.text.lower() or "aspect" in resp.text.lower())):
        # modèle sans imageConfig : on retente sans forcer le ratio. Les autres
        # 400 (payload trop gros, contenu invalide) remontent avec leur motif.
        return _appel_gemini(parts, aspect_ratio=None)
    if resp.status_code == 429:
        raise InsertionError("Quota Gemini atteint (ou facturation à vérifier) : réessayez dans un instant.")
    if resp.status_code in (401, 403):
        raise InsertionError("Clé Gemini refusée : vérifiez GEMINI_API_KEY et la facturation du projet Google.")
    if resp.status_code == 404:
        raise InsertionError(f"Modèle « {_modele()} » introuvable : ajustez GVDP_GEMINI_MODEL dans le .env.")
    if resp.status_code != 200:
        raise InsertionError(f"Gemini a répondu HTTP {resp.status_code} : {resp.text[:300]}")
    return _extraire_image(resp.json())


def decadrer(photo_origine: Path, image_generee: bytes) -> bytes:
    """Retire un éventuel habillage « type plan » autour de la photo montée.

    Malgré les consignes, le modèle reproduit parfois la mise en page des
    documents joints (cadre blanc, cartouche, légende — constaté 17/07/2026
    avec le plan de masse GVN). On repère le plus grand bloc NON blanc de
    l'image générée ; si son ratio se rapproche de celui de la photo d'origine
    alors que l'image entière s'en éloigne, on recadre dessus. Sans habillage
    détecté, l'image repart telle quelle.
    """
    import io

    from PIL import Image as PILImage

    try:
        orig = _ouvrir_image(photo_origine)
        gen = PILImage.open(io.BytesIO(image_generee)).convert("RGB")
    except OSError:
        return image_generee
    arr = np.asarray(gen.convert("L"))
    non_blanc = arr < 235

    def plus_grand_bloc(densites: np.ndarray, seuil: float) -> tuple[int, int] | None:
        """Plus long groupe d'indices contigus au-dessus du seuil (trous < 6 px)."""
        actifs = np.where(densites > seuil)[0]
        if not len(actifs):
            return None
        blocs, debut = [], actifs[0]
        for prec, cour in zip(actifs, actifs[1:]):
            if cour - prec > 6:
                blocs.append((debut, prec))
                debut = cour
        blocs.append((debut, actifs[-1]))
        return max(blocs, key=lambda b: b[1] - b[0])

    # la photo = le plus grand bloc de lignes denses (le cartouche, fait de
    # traits fins, est séparé par une bande blanche et bien plus petit)
    bloc_y = plus_grand_bloc(non_blanc.mean(axis=1), 0.35)
    if not bloc_y:
        return image_generee
    y0, y1 = bloc_y[0], bloc_y[1] + 1
    bloc_x = plus_grand_bloc(non_blanc[y0:y1].mean(axis=0), 0.35)
    if not bloc_x:
        return image_generee
    x0, x1 = bloc_x[0], bloc_x[1] + 1
    if (x1 - x0) > 0.98 * gen.width and (y1 - y0) > 0.98 * gen.height:
        return image_generee  # pas d'habillage
    ratio_o = orig.width / orig.height
    ratio_avant = abs(gen.width / gen.height - ratio_o) / ratio_o
    ratio_apres = abs((x1 - x0) / (y1 - y0) - ratio_o) / ratio_o
    if ratio_apres < ratio_avant and ratio_apres < 0.12:
        tampon = io.BytesIO()
        gen.crop((x0, y0, x1, y1)).save(tampon, "PNG")
        return tampon.getvalue()
    return image_generee


def zone_autorisee(projet: dict, W: int, H: int, chemin_photo) -> "np.ndarray | None":
    """Masque bool HxW des zones que le modèle a le DROIT de modifier.

    Union des volumes projetés (sol, face avant, toiture) agrandis de 35 %
    autour de leur centre + une copie de l'emprise au sol décalée vers le bas
    (l'ombre portée s'étale devant la structure). None dès qu'une ombrière n'a
    pas de volume calculable : on retombe alors sur la diff globale — un
    masque faux effacerait une partie de l'ombrière.
    """
    from PIL import ImageDraw

    ombrieres = poses_actives(projet) or []
    if not ombrieres:
        return None
    volumes = volumes_poses(projet, W, H, chemin_photo)
    if len(volumes) != len(ombrieres) or not all(volumes):
        return None

    def agrandi(pts, facteur=1.35):
        cx = sum(p[0] for p in pts) / len(pts)
        cy = sum(p[1] for p in pts) / len(pts)
        return [(cx + (p[0] - cx) * facteur, cy + (p[1] - cy) * facteur)
                for p in pts]

    img = Image.new("L", (W, H), 0)
    dr = ImageDraw.Draw(img)
    for vol in volumes:
        sol, toit = vol["sol"], vol["toit"]
        for quad in (sol, toit,
                     [sol[0], sol[1], toit[1], toit[0]],      # face avant
                     [sol[0], sol[1], toit[2], toit[3]]):     # silhouette
            dr.polygon(agrandi(quad), fill=255)
        decal = 0.08 * H                                       # ombre au sol
        dr.polygon(agrandi([(p[0], p[1] + decal) for p in sol]), fill=255)
    return np.asarray(img) > 0


def preserver_scene(photo_origine: Path, image_generee: bytes,
                    zone: "np.ndarray | None" = None) -> bytes:
    """Recolle les pixels d'origine partout où le modèle n'a rien construit.

    Diff en niveaux de gris (après normalisation d'exposition), flou, seuil,
    dilatation puis fondu : seules les zones réellement modifiées (l'ombrière
    et ses ombres) restent générées ; voitures, sol et bâtiments retrouvent
    leurs pixels d'origine. `zone` (masque bool à la taille de la photo)
    restreint EN PLUS les modifications à l'emprise autorisée : le ciel, les
    voitures et le bâtiment redeviennent intouchables même si le modèle les a
    repeints. Sécurités : formats incompatibles ou image presque entièrement
    changée -> on rend l'image générée telle quelle.
    """
    import io

    from PIL import Image, ImageFilter

    try:
        orig = _ouvrir_image(photo_origine).convert("RGB")
        gen = Image.open(io.BytesIO(image_generee)).convert("RGB")
    except OSError:
        return image_generee
    ratio_o = orig.width / orig.height
    ratio_g = gen.width / gen.height
    if abs(ratio_o - ratio_g) / ratio_o > 0.03:
        return image_generee  # cadrage différent : diff inexploitable
    gen = gen.resize(orig.size, Image.LANCZOS)

    o = np.asarray(orig.convert("L"), dtype=np.float32)
    g = np.asarray(gen.convert("L"), dtype=np.float32)
    ecart_type = g.std() or 1.0
    g = (g - g.mean()) / ecart_type * (o.std() or 1.0) + o.mean()  # expo alignée

    diff = Image.fromarray(np.clip(np.abs(g - o), 0, 255).astype(np.uint8))
    diff = diff.filter(ImageFilter.GaussianBlur(5))
    masque = np.asarray(diff) > 16
    if zone is not None and zone.shape == masque.shape:
        masque = masque & zone
    fraction = float(masque.mean())
    if fraction > 0.65:
        return image_generee  # tout a changé : la restauration effacerait l'ombrière

    alpha = Image.fromarray((masque * 255).astype(np.uint8))
    alpha = alpha.filter(ImageFilter.MaxFilter(15)).filter(ImageFilter.GaussianBlur(10))
    a = np.asarray(alpha, dtype=np.float32)[..., None] / 255.0
    fusion = np.asarray(gen, dtype=np.float32) * a + np.asarray(orig, dtype=np.float32) * (1 - a)

    tampon = io.BytesIO()
    Image.fromarray(fusion.astype(np.uint8)).save(tampon, "PNG")
    return tampon.getvalue()


def apercu_prompt(projet: dict) -> str:
    """Prompt qui SERAIT envoyé, pour l'aperçu éditable (sans génération)."""
    try:
        return _preparer_requete_pose(projet)["prompt"]
    except (InsertionError, OSError):
        # OSError = fichier cache momentanément verrouillé par OneDrive :
        # l'aperçu ne doit jamais planter (même règle que apercu_payload).
        return ""


def generer_image(projet: dict, affinage: str = "", prompt_override: str = "") -> dict:
    """Génère UNE insertion via Gemini (Nano Banana Pro), flux « un geste ».

    Photo repérée (bord avant + flèche de fuite) + photo de référence réelle du
    type -> génération au ratio de la photo -> décadrage + recollage de scène
    (contre la photo PROPRE). `prompt_override` : prompt édité à la main.
    Renvoie {fichier, date, etiquette, modele, prompt, controle}.
    """
    if not api_configuree():
        raise InsertionError("Mode API non configuré : clé GEMINI_API_KEY absente.")
    req = _preparer_requete_pose(projet, affinage=affinage)
    propre = req["base_propre"]   # photo NUE (le recollage se fait contre elle)
    prompt = prompt_override.strip() if prompt_override and prompt_override.strip() else req["prompt"]
    # la photo à éditer part en pleine résolution ; coupes et références, réduites
    # (simples repères de géométrie et de style, inutiles en pleine définition).
    parts: list[dict] = [{"text": prompt}]
    for role, chemin in zip(req["roles"], req["chemins"]):
        # la photo à éditer est bornée elle aussi : au-delà, le payload base64
        # dépasse la limite de requête Gemini (400 opaque). Voir _part_image.
        parts.append(_part_image(chemin, max_px=2048 if role == "photo" else 1280))

    ratio = _ratio_photo(propre)
    aspect_ratio = ratio

    # Relance auto : uniquement quand le contrôle de présence indique que rien
    # (ou presque) n'a été construit sur le tracé — couverture < 0,15. Le
    # contrôle est approximatif (bande d'emprise estimée) : relancer sur tout
    # verdict « faible » doublait la dépense sur de simples faux positifs.
    # On garde la MEILLEURE tentative. Désactivable par GVDP_AUTO_RETRY=0.
    retry = os.environ.get("GVDP_AUTO_RETRY", "1") != "0" and poses_actives(projet) is not None
    essais_max = 2 if retry else 1
    meilleur_img, meilleur_ctrl, essais = None, None, 0
    for _ in range(essais_max):
        try:
            img = _appel_gemini(parts, aspect_ratio=aspect_ratio)
        except InsertionError:
            if meilleur_img is not None:
                break     # la relance a échoué : on garde la 1re image, déjà payée
            raise
        # chaque appel qui a rendu une image est facturé : compter TOUT DE SUITE,
        # même si une étape suivante échoue (sinon le compteur de dépense dérive).
        essais += 1
        incrementer_compteur_global(1)
        img = decadrer(propre, img)
        img = effacer_marqueur(projet, propre, img)   # retire le repère magenta/cyan résiduel
        # contrôle de présence AVANT le recollage de scène (qui, en supprimant les
        # zones inchangées, gonflerait artificiellement la couverture mesurée)
        ctrl = controle_pose(projet, propre, img)
        cov = (ctrl or {}).get("couverture", 0.0)
        if meilleur_ctrl is None or cov > (meilleur_ctrl or {}).get("couverture", -1.0):
            meilleur_img, meilleur_ctrl = img, ctrl
        if not ctrl or ctrl.get("couverture", 1.0) >= 0.15:
            break                                     # assez bon : on s'arrête

    image, controle = meilleur_img, meilleur_ctrl
    if os.environ.get("GVDP_PRESERVER_SCENE", "1") != "0":
        zone = None
        if os.environ.get("GVDP_SCENE_STRICTE", "1") != "0":
            try:
                Wp, Hp = _ouvrir_image(propre).size
                zone = zone_autorisee(projet, Wp, Hp, propre)
            except OSError:
                zone = None
        image = preserver_scene(propre, image, zone=zone)
    dossier = config.assets_dir(projet["id"]) / "insertion"
    dossier.mkdir(parents=True, exist_ok=True)
    nom = f"insertion_{datetime.now().strftime('%Y%m%d-%H%M%S')}.png"
    (dossier / nom).write_bytes(image)
    rel = str((dossier / nom).relative_to(config.PROJETS_DIR)).replace("\\", "/")
    journaliser_generation({
        "date": datetime.now().isoformat(timespec="seconds"),
        "projet": projet.get("id"),
        "modele": _modele(),
        "essais": essais,
        "cout_eur": round(essais * cout_image_eur(), 2),
        "couverture": (controle or {}).get("couverture"),
        "verdict": (controle or {}).get("verdict"),
        "fichier": rel,
        "prompt": prompt,
    })
    return {
        "fichier": rel,
        "date": datetime.now().isoformat(timespec="seconds"),
        "etiquette": "visuel IA",
        "modele": _modele(),
        "prompt": prompt,
        "controle": controle,
        # nb d'appels Gemini facturés — DÉJÀ comptés au compteur global (un
        # incrément par appel réussi, au fil de l'eau) : la route ne doit pas
        # ré-incrémenter, seulement reporter au compteur du projet.
        "essais": essais,
    }
