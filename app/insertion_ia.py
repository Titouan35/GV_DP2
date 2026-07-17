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
import json
import math
import os
from datetime import datetime
from pathlib import Path

import httpx
import numpy as np
import pypdfium2 as pdfium
from PIL import Image

from . import config
from .catalogue import CATALOGUE, parametres_effectifs

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


def incrementer_compteur_global() -> int:
    n = compteur_global() + 1
    config.PROJETS_DIR.mkdir(parents=True, exist_ok=True)
    _compteur_chemin().write_text(json.dumps({"images": n}), encoding="utf-8")
    return n


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


DESCRIPTIONS_COUPE = {
    "START PLAINE Bas": "monopente, poteau unique côté haut du versant",
    "START PLAINE Haut": "monopente, poteau unique côté bas du versant",
    "START PLAINE Double": "poteau central unique, toiture en T à pente continue",
}


def construire_prompt(projet: dict, affinage: str = "",
                      plan_infos: dict | None = None,
                      guides: dict | None = None,
                      idx: dict | None = None,
                      coupe_be: bool = False,
                      mode: str = "libre",
                      plan_ref: bool = False) -> str:
    """Prompt Gemini v6 (17/07/2026). Trois modes selon la base envoyee :

    - "scaffold" : la photo contient deja l'ombrière posee en VOLUME GRIS a la
      bonne position. Consigne = habiller ce volume, sans le deplacer ni le
      redimensionner. C'est le mode fiable (placement garanti par nous).
    - "axes" : la photo porte des axes magenta (repli si pas d'echelle).
    - "libre" : aucune indication, placement au juge.

    Regles Google : verbe fort, images decrites sans numero, formulation
    positive, keep-explicit. La coupe (structure) et le plan de masse (legende
    couleurs) sont joints en reference.
    """
    omb = projet.get("ombriere") or {}
    ins = projet.get("insertion") or {}
    n = len(guides["ombrieres"]) if guides and guides.get("ombrieres") else 0
    mot = "les ombrières" if n > 1 else "l'ombrière"
    mot_de = "des ombrières" if n > 1 else "de l'ombrière"

    if mode == "scaffold":
        tete = (f"Transforme {'les' if n > 1 else 'la'} forme"
                f"{'s' if n > 1 else ''} grise"
                f"{'s' if n > 1 else ''} en volume, déjà présente"
                f"{'s' if n > 1 else ''} sur cette photographie, en "
                f"{'ombrières photovoltaïques de parking' if n > 1 else 'une ombrière photovoltaïque de parking'} "
                "photoréaliste"
                f"{'s' if n > 1 else ''}, comme si elle"
                f"{'s avaient' if n > 1 else ' avait'} toujours été là.")
        blocs = [tete]
        blocs.append(
            "PLACEMENT. " + ("Chaque forme grise" if n > 1 else "La forme grise")
            + " marque l'emplacement, la taille et l'orientation EXACTS "
            + mot_de + " : garde-les rigoureusement identiques, ne déplace pas et "
            "ne redimensionne pas. Remplace simplement le volume gris par la "
            "vraie structure et sa toiture."
        )
    elif mode == "axes":
        blocs = [f"Insère {n if n > 1 else 'une'} ombrière"
                 f"{'s' if n > 1 else ''} photovoltaïque"
                 f"{'s' if n > 1 else ''} de parking dans cette photographie, "
                 "de façon photoréaliste."]
        blocs.append(
            "PLACEMENT. Un trait magenta marque l'axe " + mot_de
            + " : construis " + mot + " le long de chaque trait, centrée sur le "
            "trait et posée au sol.")
    else:
        blocs = ["Insère une ombrière photovoltaïque de parking dans cette "
                 "photographie, de façon photoréaliste."]
        blocs.append("PLACEMENT. Implante l'ombrière sur la zone de "
                     "stationnement la plus dégagée et cohérente de la photo.")

    # cotes reelles
    if plan_infos and plan_infos.get("dims_m"):
        liste = " ; ".join(f"{L:g} m x {l:g} m".replace(".", ",")
                           for L, l in plan_infos["dims_m"])
        blocs[-1] += f" Dimensions réelles au sol : {liste}."

    # structure : la coupe
    if "coupe" in (idx or {}):
        origine = ("la coupe technique du projet, dessinée par le bureau d'études"
                   if coupe_be else "la coupe technique fournie")
        struct = [f"STRUCTURE. Reproduis fidèlement le profil de {origine} : "
                  "mêmes poteaux, même position des poteaux sous la toiture, "
                  "même pente, mêmes proportions. "]
    else:
        struct = ["STRUCTURE. Poteaux en acier galvanisé et toiture inclinée. "]
    h_bas, h_haut = omb.get("garde_au_sol_m"), omb.get("hauteur_hors_tout_m")
    if h_bas and h_haut:
        struct.append(f"Hauteur {_fmt(h_bas)} m au point bas et {_fmt(h_haut)} m "
                      "au point haut. ")
    struct.append("Structure en acier galvanisé gris clair, toiture de modules "
                  "photovoltaïques noirs et mats, sous-face claire.")
    blocs.append("".join(struct))

    # reference implantation : le plan de masse et sa legende couleurs
    if plan_ref:
        blocs.append(
            "REFERENCE. Le plan de masse joint (vue de dessus) confirme "
            "l'implantation : les zones bleues quadrillées sont les panneaux, "
            "les traits rouges la trame des poteaux, les carres gris les "
            "fondations, et les mentions HAUT/BAS DE RAMPANT le sens de descente "
            "de la toiture. Sers-t'en pour l'orientation et les proportions ; "
            "ne le recopie pas dans l'image."
        )

    # integration : positif + keep-explicit + photo
    blocs.append(
        "INTEGRATION. Garde le reste de la scène rigoureusement identique : les "
        "voitures, le revêtement du sol et ses marquages, les bordures, les "
        "arbres hors ombrière, les bâtiments et le ciel restent exactement a "
        "leur place. Reproduis le grand-angle, la lumière du jour et la "
        "direction des ombres de la photo ; ajoute une ombre portée douce sous "
        "chaque ombrière. Les poteaux sont verticaux et posés sur le bitume."
    )

    rendu = ["RENDU. Le résultat est une photographie plein cadre au meme "
             "cadrage que l'originale, montrant le parking avec ses ombrières."]
    libres = (ins.get("consignes") or "").strip()
    corrections = (affinage or ins.get("affinage") or "").strip()
    if libres:
        rendu.append(libres if libres.endswith((".", "!", "?")) else libres + ".")
    if corrections:
        rendu.append(corrections if corrections.endswith((".", "!", "?")) else corrections + ".")
    blocs.append(" ".join(rendu))

    return "\n\n".join(blocs)


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


# ------------------------------------------------------------------ guides photo

def guides_actifs(projet: dict) -> dict | None:
    """Repères tracés sur la photo ACTIVE : une ombrière = 2 traits.

    Chaque ombrière = {longueur:[A,B] (bord avant / bas de rampant),
    largeur:[C,D] (trait tracé du BAS vers le HAUT de rampant, donne la
    profondeur et le sens de pente), longueur_m, largeur_m}. Plusieurs
    ombrières possibles. None si rien d'exploitable.
    """
    ins = projet.get("insertion") or {}
    photo = ins.get("photo")
    if not photo:
        return None
    g = (ins.get("guides") or {}).get(photo) or {}
    ombrieres = []
    for o in (g.get("ombrieres") or []):
        lo, la = o.get("longueur"), o.get("largeur")
        if lo and la and len(lo) == 2 and len(la) == 2:
            ombrieres.append({
                "longueur": lo, "largeur": la,
                "longueur_m": o.get("longueur_m"), "largeur_m": o.get("largeur_m"),
            })
    if not ombrieres:
        return None
    return {"ombrieres": ombrieres}


MAGENTA = (255, 0, 200)   # trait de LONGUEUR (bord avant)
CYAN = (0, 200, 255)      # trait de LARGEUR (profondeur, bas -> haut de rampant)


def _fleche(dr, a, b, coul, ep):
    dr.line([a, b], fill=coul, width=ep)
    ang = math.atan2(b[1] - a[1], b[0] - a[0])
    t = ep * 3
    for da in (-0.5, 0.5):
        dr.line([b, (b[0] - t * math.cos(ang - da), b[1] - t * math.sin(ang - da))],
                fill=coul, width=ep)


def photo_emprise(projet: dict) -> Path | None:
    """Copie de la photo active avec, par ombrière, les 2 traits tracés.

    Longueur en MAGENTA (bord avant), largeur en CYAN fléchée du bas vers le
    haut de rampant (sens de pente). Aucun texte (déteint sur le rendu).
    """
    from PIL import ImageDraw

    guides = guides_actifs(projet)
    photo = image_kit(projet, "photo")
    if not guides or not photo:
        return None

    image = Image.open(photo).convert("RGB")
    dr = ImageDraw.Draw(image)
    l, h = image.size
    ep = max(5, round(min(l, h) / 220))

    for o in guides["ombrieres"]:
        la0, lb0 = o["longueur"]
        a = (la0[0] * l, la0[1] * h)
        b = (lb0[0] * l, lb0[1] * h)
        dr.line([a, b], fill=MAGENTA, width=ep)
        for px, py in (a, b):
            dr.ellipse([px - ep * 2, py - ep * 2, px + ep * 2, py + ep * 2], fill=MAGENTA)
        w0, w1 = o["largeur"]
        c = (w0[0] * l, w0[1] * h)
        d = (w1[0] * l, w1[1] * h)
        _fleche(dr, c, d, CYAN, ep)

    sortie = config.assets_dir(projet.get("id")) / "photo_emprise.png"
    sortie.parent.mkdir(parents=True, exist_ok=True)
    image.save(sortie)
    return sortie


# ------------------------------------------------------------------ scaffold 3D

# le placement au pixel est impossible à obtenir du modèle (constaté à
# répétition 17/07/2026) : on POSE nous-mêmes l'ombrière en volume sur la photo,
# à partir des 2 traits tracés (longueur + largeur), et Gemini ne fait plus que
# l'habillage photoréaliste. Échelle = longueur px / longueur réelle du plan ;
# sens de pente = sens du trait de largeur (bas -> haut de rampant).
GRIS_TOIT = (70, 72, 78)
GRIS_POTEAU = (188, 192, 198)
GRIS_POTEAU_OMBRE = (150, 154, 160)


def scaffold_photo(projet: dict) -> Path | None:
    """Photo avec l'ombrière posée en VOLUME gris, depuis les 2 traits tracés.

    longueur = bord avant (bas de rampant), largeur = profondeur tracée du bas
    vers le haut de rampant. Le footprint est le parallélogramme (avant + vecteur
    largeur) ; la toiture monte de h_bas (avant) à h_haut (fond) ; poteaux
    verticaux. Placement EXACT (c'est le tracé de l'utilisateur). None sans tracé.
    """
    from PIL import ImageDraw

    guides = guides_actifs(projet)
    photo = image_kit(projet, "photo")
    if not guides or not guides["ombrieres"] or not photo:
        return None

    image = Image.open(photo).convert("RGB")
    W, H = image.size
    omb = projet.get("ombriere") or {}
    h_bas = omb.get("garde_au_sol_m") or 2.5
    h_haut = omb.get("hauteur_hors_tout_m") or 3.5

    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    dr = ImageDraw.Draw(overlay)

    for o in guides["ombrieres"]:
        a = (o["longueur"][0][0] * W, o["longueur"][0][1] * H)
        b = (o["longueur"][1][0] * W, o["longueur"][1][1] * H)
        c = (o["largeur"][0][0] * W, o["largeur"][0][1] * H)
        d = (o["largeur"][1][0] * W, o["largeur"][1][1] * H)
        long_px = math.hypot(b[0] - a[0], b[1] - a[1])
        # échelle px/m : longueur du trait / longueur réelle (repli : largeur)
        L_m = o.get("longueur_m")
        if L_m and L_m > 0:
            scale = long_px / L_m
        else:
            larg_px = math.hypot(d[0] - c[0], d[1] - c[1])
            l_m = o.get("largeur_m") or 8.0
            scale = larg_px / l_m if l_m else long_px / 18.0
        vx, vy = d[0] - c[0], d[1] - c[1]          # vecteur profondeur (bas->haut)

        a_far, b_far = (a[0] + vx, a[1] + vy), (b[0] + vx, b[1] + vy)

        def haut(pt, hm):
            return (pt[0], pt[1] - hm * scale)

        av0, av1 = haut(a, h_bas), haut(b, h_bas)          # avant (bas de rampant)
        ar0, ar1 = haut(a_far, h_haut), haut(b_far, h_haut)  # fond (haut de rampant)

        # poteaux : file avant + file fond, environ tous les 6 m
        nb = max(1, int(round((L_m or long_px / scale) / 6)))
        for k in range(nb + 1):
            t = k / nb
            pied_av = (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
            pied_ar = (pied_av[0] + vx, pied_av[1] + vy)
            for pied, hm in ((pied_av, h_bas), (pied_ar, h_haut)):
                tete = haut(pied, hm)
                w = max(4, 0.22 * scale)
                dr.polygon([(pied[0] - w, pied[1]), (pied[0] + w, pied[1]),
                            (tete[0] + w * 0.85, tete[1]), (tete[0] - w * 0.85, tete[1])],
                           fill=GRIS_POTEAU + (255,), outline=(90, 94, 100, 255))

        ep = max(5, 0.35 * scale)
        dr.polygon([av0, av1, (av1[0], av1[1] + ep), (av0[0], av0[1] + ep)],
                   fill=GRIS_POTEAU_OMBRE + (255,))
        dr.polygon([av0, av1, ar1, ar0], fill=GRIS_TOIT + (255,),
                   outline=(30, 32, 36, 255))

    image = Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB")
    sortie = config.assets_dir(projet.get("id")) / "scaffold.png"
    sortie.parent.mkdir(parents=True, exist_ok=True)
    image.save(sortie)
    return sortie


# ------------------------------------------------------------------ vue aérienne

def _analyse_plan(projet: dict):
    """Analyse (implantation) du plan de masse PDF, ou None."""
    source = _document_image(projet, "dp2")
    if not source or source.suffix.lower() != ".pdf":
        return None, None
    from . import implantation as mod_implantation
    return mod_implantation.analyser_plan(source), source


def _aerienne_crop(analyse) -> tuple[int, int, int, int]:
    """Fenêtre de crop (px plan) autour des rangées + flèche, marge 55 %."""
    xs, ys = [], []
    for z in analyse["rangees"]:
        for cx, cy in z["coins_px"]:
            xs.append(cx)
            ys.append(cy)
    for cle in ("pente_haut_px", "pente_bas_px"):
        if analyse[cle]:
            xs.append(analyse[cle][0])
            ys.append(analyse[cle][1])
    W, H = analyse["taille_px"]
    marge = round(0.55 * max(max(xs) - min(xs), max(ys) - min(ys))) + 60
    return (max(0, int(min(xs)) - marge), max(0, int(min(ys)) - marge),
            min(W, int(max(xs)) + marge), min(H, int(max(ys)) + marge))


def aerienne_donnees(projet: dict) -> dict | None:
    """Fond aérien (crop du plan) + emprises (stockées ou auto) + flèche.

    Renvoie {fond: Path, emprises: [[[x,y] x4]...] (0-1 crop), fleche: {a, b}
    ou None, auto: bool}. None si pas de plan analysable.
    """
    analyse, source = _analyse_plan(projet)
    if not analyse:
        return None
    x0, y0, x1, y1 = _aerienne_crop(analyse)
    lc, hc = x1 - x0, y1 - y0

    # fond nu, cache par date du plan
    assets = config.assets_dir(projet.get("id"))
    fond = assets / "aerienne_fond.png"
    if not (fond.exists() and fond.stat().st_mtime >= source.stat().st_mtime):
        with config.PDFIUM_LOCK:
            doc = pdfium.PdfDocument(str(source))
            try:
                image = doc[0].render(scale=150 / 72).to_pil().convert("RGB")
            finally:
                doc.close()
        assets.mkdir(parents=True, exist_ok=True)
        image.crop((x0, y0, x1, y1)).save(fond)

    # emprises : override utilisateur sinon rectangles PCA des rangées
    stocke = (projet.get("insertion") or {}).get("aerienne") or {}
    if stocke.get("emprises"):
        emprises = stocke["emprises"]
        auto = False
    else:
        emprises = [[[(cx - x0) / lc, (cy - y0) / hc] for cx, cy in z["coins_px"]]
                    for z in analyse["rangees"]]
        auto = True

    fleche = None
    if analyse["pente_haut_px"] and analyse["pente_bas_px"]:
        # direction haut->bas, tracée au centre de la 1re emprise
        hx, hy = analyse["pente_haut_px"]
        bx, by = analyse["pente_bas_px"]
        v = np.array([bx - hx, by - hy], dtype=float)
        n = float(np.hypot(*v)) or 1.0
        v /= n
        pts = np.array([[px * lc, py * hc] for px, py in emprises[0]])
        centre = pts.mean(axis=0)
        demi = 0.5 * min(lc, hc) / 3
        a = centre - demi * v
        b = centre + demi * v
        fleche = {"a": [float(a[0] / lc), float(a[1] / hc)],
                  "b": [float(b[0] / lc), float(b[1] / hc)]}

    return {"fond": fond, "emprises": emprises, "fleche": fleche, "auto": auto}


def aerienne_emprise(projet: dict) -> Path | None:
    """Vue aérienne annotée pour Gemini : emprises MAGENTA + flèche de pente.

    Aucun texte (anti-contamination). La flèche est sombre à liseré blanc.
    """
    from PIL import ImageDraw

    donnees = aerienne_donnees(projet)
    if not donnees:
        return None
    image = Image.open(donnees["fond"]).convert("RGB")
    dr = ImageDraw.Draw(image)
    l, h = image.size
    ep = max(4, round(min(l, h) / 160))

    for emprise in donnees["emprises"]:
        pts = [(x * l, y * h) for x, y in emprise]
        dr.line(pts + [pts[0]], fill=MAGENTA, width=ep)

    if donnees["fleche"]:
        a = np.array([donnees["fleche"]["a"][0] * l, donnees["fleche"]["a"][1] * h])
        b = np.array([donnees["fleche"]["b"][0] * l, donnees["fleche"]["b"][1] * h])
        v = b - a
        n = float(np.hypot(*v)) or 1.0
        v /= n
        p = np.array([-v[1], v[0]])
        for coul, larg in (((255, 255, 255), ep + 6), ((20, 20, 30), ep)):
            dr.line([tuple(a), tuple(b)], fill=coul, width=larg)
            for signe in (1, -1):
                pointe = b - (4.5 * ep) * v + signe * (2.6 * ep) * p
                dr.line([tuple(b), tuple(pointe)], fill=coul, width=larg)

    sortie = config.assets_dir(projet.get("id")) / "aerienne_emprise.png"
    image.save(sortie)
    return sortie


def resume_guides(guides: dict) -> str:
    """Résumé texte des repères pour l'UI."""
    n = len(guides["ombrieres"])
    return f"{n} ombrière{'s' if n > 1 else ''} tracée{'s' if n > 1 else ''}" if n else ""


def plan_dims(projet: dict) -> list[tuple[float, float]]:
    """Cotes (longueur, largeur) en m des rangées lues sur le plan de masse."""
    analyse, _ = _analyse_plan(projet)
    if not analyse:
        return []
    return [(z["longueur_m"], z["largeur_m"])
            for z in analyse["rangees"] if "longueur_m" in z]


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


def _part_image(chemin: Path) -> dict:
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
    raise InsertionError("Aucune image renvoyée par le modèle (réponse vide).")


_RATIOS_SUPPORTES = {   # aspect ratios acceptés par Nano Banana -> valeur
    1 / 1: "1:1", 3 / 2: "3:2", 2 / 3: "2:3", 3 / 4: "3:4", 4 / 3: "4:3",
    4 / 5: "4:5", 5 / 4: "5:4", 9 / 16: "9:16", 16 / 9: "16:9", 21 / 9: "21:9",
}


def _ratio_photo(chemin: Path) -> str | None:
    """Aspect ratio Nano Banana le plus proche de la photo (garde le cadrage)."""
    try:
        with Image.open(chemin) as im:
            r = im.width / im.height
    except OSError:
        return None
    return min(_RATIOS_SUPPORTES.items(), key=lambda kv: abs(kv[0] - r))[1]


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
    if resp.status_code == 400 and aspect_ratio:
        return _appel_gemini(parts, aspect_ratio=None)  # modèle sans imageConfig
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
        orig = PILImage.open(photo_origine)
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


def preserver_scene(photo_origine: Path, image_generee: bytes) -> bytes:
    """Recolle les pixels d'origine partout où le modèle n'a rien construit.

    Diff en niveaux de gris (après normalisation d'exposition), flou, seuil,
    dilatation puis fondu : seules les zones réellement modifiées (l'ombrière
    et ses ombres) restent générées ; voitures, sol et bâtiments retrouvent
    leurs pixels d'origine. Sécurités : formats incompatibles ou image presque
    entièrement changée -> on rend l'image générée telle quelle.
    """
    import io

    from PIL import Image, ImageFilter

    try:
        orig = Image.open(photo_origine).convert("RGB")
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


def _preparer_requete(projet: dict, affinage: str = "") -> dict:
    """Assemble le lot d'images v5 + le prompt auto (sans appeler Gemini).

    Méthode officielle Nano Banana : lot MINIMAL, rôles décrits en langage
    naturel (jamais numérotés). Deux images seulement :
      - base à éditer = la photo, annotée des axes magenta si tracés
      - référence structure = la coupe DP3 du BE (repli catalogue)
    La vue aérienne et le plan brut ne sont plus envoyés (source de confusion) ;
    l'implantation vient des axes que Florent trace sur la photo.
    Renvoie {chemins, roles, prompt, base_propre}. Lève InsertionError sans photo.
    """
    base_propre = image_kit(projet, "photo")
    if not base_propre:
        raise InsertionError("Ajoutez d'abord une photo du site (upload ou reprise d'une pièce BE).")

    # base à éditer : PRIORITÉ au scaffold (ombrière déjà posée en volume gris ;
    # Gemini n'a plus qu'à l'habiller sans la déplacer). Repli : axes magenta,
    # puis photo nue.
    guides = guides_actifs(projet)
    scaffold = scaffold_photo(projet) if guides else None
    if scaffold:
        base, mode = scaffold, "scaffold"
    elif guides and (annotee := photo_emprise(projet)):
        base, mode = annotee, "axes"
    else:
        base, mode = base_propre, "libre"

    roles = ["photo"]
    chemins: list[Path] = [base]

    # cotes réelles du plan
    plan_infos = None
    analyse, _ = _analyse_plan(projet)
    if analyse:
        dims = [(z["longueur_m"], z["largeur_m"])
                for z in analyse["rangees"] if "longueur_m" in z]
        if dims:
            plan_infos = {"dims_m": dims}

    # référence structure : la coupe DP3 du BE (repli catalogue)
    coupe_be = False
    coupe = image_kit(projet, "coupe_be")
    if coupe:
        coupe_be = True
    else:
        coupe = image_kit(projet, "coupe")
    if coupe:
        roles.append("coupe")
        chemins.append(coupe)

    # référence implantation : le plan de masse (crop nettoyé, avec sa légende)
    plan_ref = None
    aer = aerienne_donnees(projet)
    if aer and aer.get("fond"):
        plan_ref = aer["fond"]
        roles.append("plan")
        chemins.append(plan_ref)

    idx = {role: i + 1 for i, role in enumerate(roles)}
    prompt = construire_prompt(projet, affinage=affinage, plan_infos=plan_infos,
                               guides=guides, idx=idx, coupe_be=coupe_be,
                               mode=mode, plan_ref=bool(plan_ref))
    return {"chemins": chemins, "roles": roles, "prompt": prompt,
            "base_propre": base_propre, "mode": mode}


def apercu_prompt(projet: dict) -> str:
    """Prompt qui SERAIT envoyé, pour l'aperçu éditable (sans génération)."""
    try:
        return _preparer_requete(projet)["prompt"]
    except InsertionError:
        return ""


def generer_image(projet: dict, affinage: str = "", prompt_override: str = "") -> dict:
    """Génère UNE insertion via Gemini (Nano Banana Pro).

    Pipeline v5 (17/07/2026, méthode officielle Nano Banana) : photo annotée
    des axes maganta + coupe DP3 -> génération au ratio de la photo -> décadrage
    + recollage de la scène (contre la photo PROPRE). `prompt_override` : prompt
    édité à la main. Renvoie {fichier, date, etiquette, modele, prompt}.
    """
    if not api_configuree():
        raise InsertionError("Mode API non configuré : clé GEMINI_API_KEY absente.")
    req = _preparer_requete(projet, affinage=affinage)
    propre = req["base_propre"]   # photo NUE (le recollage se fait contre elle)
    prompt = prompt_override.strip() if prompt_override and prompt_override.strip() else req["prompt"]
    parts: list[dict] = [{"text": prompt}] + [_part_image(c) for c in req["chemins"]]

    image = _appel_gemini(parts, aspect_ratio=_ratio_photo(propre))
    image = decadrer(propre, image)
    if os.environ.get("GVDP_PRESERVER_SCENE", "1") != "0":
        image = preserver_scene(propre, image)
    dossier = config.assets_dir(projet["id"]) / "insertion"
    dossier.mkdir(parents=True, exist_ok=True)
    nom = f"insertion_{datetime.now().strftime('%Y%m%d-%H%M%S')}.png"
    (dossier / nom).write_bytes(image)
    rel = str((dossier / nom).relative_to(config.PROJETS_DIR)).replace("\\", "/")
    return {
        "fichier": rel,
        "date": datetime.now().isoformat(timespec="seconds"),
        "etiquette": "visuel IA",
        "modele": _modele(),
        "prompt": prompt,
    }
