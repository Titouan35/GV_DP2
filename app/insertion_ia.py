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


def incrementer_compteur_global(n: int = 1) -> int:
    total = compteur_global() + max(1, int(n))
    config.PROJETS_DIR.mkdir(parents=True, exist_ok=True)
    _compteur_chemin().write_text(json.dumps({"images": total}), encoding="utf-8")
    return total


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
#
# 17/07/2026 (soir) : le toit n'est plus un aplat gris mais une TEXTURE de
# modules PV sombres (grille de cellules en perspective) + fascia et poteaux
# galvanisés. Un scaffold qui ressemble déjà à l'ombrière finie réduit la
# latitude de Gemini : il n'a plus à « inventer » une ombrière (d'où les
# redimensionnements constatés), seulement à rendre celle-ci photoréaliste.
GRIS_PANNEAU = (24, 26, 32)         # module PV (quasi noir, mat)
GRIS_CELLULE = (46, 50, 58)         # liseré entre cellules / modules
GRIS_POTEAU = (188, 192, 198)       # acier galvanisé clair
GRIS_FASCIA = (150, 154, 160)       # panne/fascia sous le bord avant
COUL_TRAIT_TOIT = (14, 15, 18, 255)


def _geometrie_ombrieres(projet: dict, W: int, H: int):
    """Génère la géométrie 3D de chaque ombrière tracée, en pixels image.

    Source unique pour le dessin du scaffold ET le contrôle géométrique
    post-génération. Pour chaque ombrière tracée renvoie un dict :
      a, b       : pieds du bord avant (bas de rampant), file gauche->droite
      vx, vy     : vecteur profondeur (bas -> haut de rampant)
      scale      : px/m (longueur du trait / longueur réelle, repli largeur)
      h_bas,h_haut : hauteurs (m) au bord avant et au fond
      av0,av1,ar0,ar1 : coins du toit (avant-G, avant-D, fond-G, fond-D)
      L_m        : longueur réelle (m) si connue
    """
    guides = guides_actifs(projet)
    if not guides or not guides.get("ombrieres"):
        return
    omb = projet.get("ombriere") or {}
    h_bas = omb.get("garde_au_sol_m") or 2.5
    h_haut = omb.get("hauteur_hors_tout_m") or 3.5

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

        yield {
            "a": a, "b": b, "vx": vx, "vy": vy, "scale": scale,
            "h_bas": h_bas, "h_haut": h_haut, "L_m": L_m,
            "av0": haut(a, h_bas), "av1": haut(b, h_bas),         # avant (bas)
            "ar0": haut(a_far, h_haut), "ar1": haut(b_far, h_haut),  # fond (haut)
        }


def _toit_panneaux(dr, av0, av1, ar1, ar0, scale, L_m):
    """Dessine le toit en modules PV : fond sombre + grille de cellules.

    Le toit est le quad (avant-G, avant-D, fond-D, fond-G). On subdivise en
    perspective par interpolation bilinéaire : colonnes le long du bord (u),
    rangées en profondeur (v). Colonnes ~ une file de modules par place de
    parking (2,3 m), rangées ~ modules de 1,7 m. Ça se lit comme des panneaux.
    """
    dr.polygon([av0, av1, ar1, ar0], fill=GRIS_PANNEAU + (255,),
               outline=COUL_TRAIT_TOIT)

    def bilin(u, v):
        av = (av0[0] + (av1[0] - av0[0]) * u, av0[1] + (av1[1] - av0[1]) * u)
        ar = (ar0[0] + (ar1[0] - ar0[0]) * u, ar0[1] + (ar1[1] - ar0[1]) * u)
        return (av[0] + (ar[0] - av[0]) * v, av[1] + (ar[1] - av[1]) * v)

    largeur_m = (L_m or (math.hypot(av1[0] - av0[0], av1[1] - av0[1]) / scale)) or 12.0
    prof_px = math.hypot(ar0[0] - av0[0], ar0[1] - av0[1])
    prof_m = prof_px / scale if scale else 8.0
    ncol = max(4, min(40, round(largeur_m / 2.3)))
    nrow = max(2, min(20, round(prof_m / 1.7)))
    ep = max(1, round(scale * 0.03))

    for i in range(1, ncol):
        u = i / ncol
        dr.line([bilin(u, 0.0), bilin(u, 1.0)], fill=GRIS_CELLULE + (255,), width=ep)
    for j in range(1, nrow):
        v = j / nrow
        dr.line([bilin(0.0, v), bilin(1.0, v)], fill=GRIS_CELLULE + (255,), width=ep)


def scaffold_photo(projet: dict) -> Path | None:
    """Photo avec l'ombrière posée en VOLUME, depuis les 2 traits tracés.

    longueur = bord avant (bas de rampant), largeur = profondeur tracée du bas
    vers le haut de rampant. Le footprint est le parallélogramme (avant + vecteur
    largeur) ; la toiture monte de h_bas (avant) à h_haut (fond) ; poteaux
    verticaux. Toit texturé en modules PV (voir _toit_panneaux). Placement EXACT
    (c'est le tracé de l'utilisateur). None sans tracé.
    """
    from PIL import ImageDraw

    photo = image_kit(projet, "photo")
    geoms = list(_geometrie_ombrieres(projet, *Image.open(photo).size)) if photo else []
    if not photo or not geoms:
        return None

    image = Image.open(photo).convert("RGB")
    W, H = image.size
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    dr = ImageDraw.Draw(overlay)

    for g in geoms:
        a, b = g["a"], g["b"]
        vx, vy, scale = g["vx"], g["vy"], g["scale"]
        h_bas, h_haut = g["h_bas"], g["h_haut"]
        av0, av1, ar0, ar1 = g["av0"], g["av1"], g["ar0"], g["ar1"]

        def haut(pt, hm):
            return (pt[0], pt[1] - hm * scale)

        # poteaux : file avant + file fond, environ tous les 6 m
        nb = max(1, int(round((g["L_m"] or math.hypot(b[0] - a[0], b[1] - a[1]) / scale) / 6)))
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
                   fill=GRIS_FASCIA + (255,))
        _toit_panneaux(dr, av0, av1, ar1, ar0, scale, g["L_m"])

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


def photo_reperee(projet: dict) -> Path | None:
    """Photo active avec CHAQUE bord avant (MAGENTA) + sa flèche de fuite (CYAN)."""
    from PIL import ImageDraw

    ombrieres = poses_actives(projet)
    photo = image_kit(projet, "photo")
    if not ombrieres or not photo:
        return None
    image = Image.open(photo).convert("RGB")
    W, H = image.size
    dr = ImageDraw.Draw(image)
    # repère MINIMAL : un simple trait fin par bord avant. Ni pastille, ni
    # numéro, ni flèche de fuite (18/07/2026 : la flèche cyan était redessinée
    # en grand par le modèle par-dessus les toitures, hors de la bande
    # d'effacement — le sens de fuite est donc porté par le texte du prompt).
    ep = max(3, round(min(W, H) / 320))
    for o in ombrieres:
        a, b = _points_bord(o, W, H)
        dr.line([a, b], fill=MAGENTA, width=ep)
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

    if pose and ombrieres:
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
        defaut = {"famille": (projet.get("ombriere") or {}).get("famille")
                  or "START PLAINE Bas"}
        blocs.append("PLACEMENT. Implante l'ombrière sur la zone de stationnement "
                     "la plus dégagée et cohérente de la photo.")
        blocs.append("STRUCTURE. Ombrière "
                     + _descriptif_ombriere(defaut, projet) + ".")

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
    for o in ombrieres:
        a, b = _points_bord(o, W, H)
        dr.line([a, b], fill=255, width=ep)
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
    magenta = (r - g > 12) & (b - g > 8) & (r > 80)          # trait + bords roses pâles
    cyan = (g - r > 45) & (b - r > 45) & (r < 130) & (g > 140)  # flèche
    marque = bande & (magenta | cyan)
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
    """Emprises approximatives des toits : un quad par ombrière, extrudé vers la
    fuite. La profondeur en px est estimée depuis la longueur du trait et les
    cotes réelles quand elles sont connues (sinon ratio nominal). Sert au
    contrôle de présence, pas à une mesure exacte."""
    bandes = []
    for o in (poses_actives(projet) or []):
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
        orig = Image.open(photo_propre).convert("RGB")
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
    """Encode une image pour Gemini. `max_px` la réduit (JPEG) au besoin :
    utile pour la photo de référence, simple repère de structure, inutile à
    envoyer en pleine résolution (payload et latence en moins, qualité égale —
    le format de sortie vient de la photo à éditer, pas de la référence)."""
    if max_px:
        try:
            with Image.open(chemin) as im:
                if max(im.size) > max_px:
                    ech = max_px / max(im.size)
                    petite = im.convert("RGB").resize(
                        (round(im.width * ech), round(im.height * ech)), Image.LANCZOS)
                    import io
                    buf = io.BytesIO()
                    petite.save(buf, "JPEG", quality=85)
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


def controle_couverture(projet: dict, photo_propre: Path, image_generee: bytes) -> dict | None:
    """Vérifie que Gemini a bien construit l'ombrière SUR toute l'emprise tracée.

    Panne constatée (17/07, Carrefour Anse) : le modèle raccourcit l'ombrière ou
    laisse un trou entre deux structures, malgré le scaffold. On rasterise
    l'emprise du toit attendue (les quads du scaffold) et on la compare à la zone
    réellement modifiée par Gemini (diff contre la photo propre). Le taux de
    recouvrement de l'emprise attendue donne un verdict :
      >= 0,82  ok        (l'ombrière couvre bien le tracé)
      >= 0,60  partiel   (structure raccourcie / trou partiel)
      <  0,60  faible    (placement raté, à régénérer)
    Renvoie None si aucun tracé exploitable (mode libre : rien à vérifier).
    """
    import io

    from PIL import Image, ImageDraw, ImageFilter

    try:
        orig = Image.open(photo_propre).convert("RGB")
        gen = Image.open(io.BytesIO(image_generee)).convert("RGB")
    except OSError:
        return None
    W0, H0 = orig.size
    geoms = list(_geometrie_ombrieres(projet, W0, H0))
    if not geoms:
        return None

    # résolution de travail (bornée pour la vitesse)
    ech = min(1.0, 1000 / max(W0, H0))
    W, H = max(1, round(W0 * ech)), max(1, round(H0 * ech))

    # emprise attendue = union des quads de toit
    masque_img = Image.new("L", (W, H), 0)
    dr = ImageDraw.Draw(masque_img)
    for g in geoms:
        quad = [g["av0"], g["av1"], g["ar1"], g["ar0"]]
        dr.polygon([(x * ech, y * ech) for x, y in quad], fill=255)
    attendu = np.asarray(masque_img) > 0
    aire_attendue = int(attendu.sum())
    if aire_attendue == 0:
        return None

    # zone réellement modifiée = diff (exposition alignée) gen vs photo propre
    gen = gen.resize((W, H), Image.LANCZOS)
    orig_s = orig.resize((W, H), Image.LANCZOS)
    o = np.asarray(orig_s.convert("L"), dtype=np.float32)
    g_arr = np.asarray(gen.convert("L"), dtype=np.float32)
    g_arr = (g_arr - g_arr.mean()) / (g_arr.std() or 1.0) * (o.std() or 1.0) + o.mean()
    diff = Image.fromarray(np.clip(np.abs(g_arr - o), 0, 255).astype(np.uint8))
    diff = diff.filter(ImageFilter.GaussianBlur(3))
    modifie = np.asarray(diff) > 16

    inter = int((attendu & modifie).sum())
    couverture = inter / aire_attendue
    verdict = "ok" if couverture >= 0.82 else "partiel" if couverture >= 0.60 else "faible"
    return {"couverture": round(couverture, 3), "verdict": verdict,
            "n_ombrieres": len(geoms)}


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
        return _preparer_requete_pose(projet)["prompt"]
    except InsertionError:
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
        parts.append(_part_image(chemin, max_px=None if role == "photo" else 1280))

    ratio = _ratio_photo(propre)
    aspect_ratio = ratio

    # Relance auto : si le contrôle de présence détecte un placement raté
    # (verdict « faible »), on régénère une fois et on garde la MEILLEURE
    # tentative. Ne coûte 2x que sur un échec ; sans pose (pas de contrôle),
    # une seule tentative. Désactivable par GVDP_AUTO_RETRY=0.
    retry = os.environ.get("GVDP_AUTO_RETRY", "1") != "0" and poses_actives(projet) is not None
    essais_max = 2 if retry else 1
    meilleur_img, meilleur_ctrl, essais = None, None, 0
    for _ in range(essais_max):
        essais += 1
        img = _appel_gemini(parts, aspect_ratio=aspect_ratio)
        img = decadrer(propre, img)
        img = effacer_marqueur(projet, propre, img)   # retire le repère magenta/cyan résiduel
        # contrôle de présence AVANT le recollage de scène (qui, en supprimant les
        # zones inchangées, gonflerait artificiellement la couverture mesurée)
        ctrl = controle_pose(projet, propre, img)
        cov = (ctrl or {}).get("couverture", 0.0)
        if meilleur_ctrl is None or cov > (meilleur_ctrl or {}).get("couverture", -1.0):
            meilleur_img, meilleur_ctrl = img, ctrl
        if not ctrl or ctrl.get("verdict") != "faible":
            break                                     # assez bon : on s'arrête

    image, controle = meilleur_img, meilleur_ctrl
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
        "controle": controle,
        "essais": essais,   # nb d'appels Gemini réels (coût)
    }
