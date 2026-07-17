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
                      coupe_be: bool = False) -> str:
    """Prompt Gemini v4 ULTRA-CADRÉ, co-écrit avec Florent le 17/07/2026.

    Principe : le lot d'images est fabriqué pour la tâche (photo propre,
    photo + emprise magenta, vue aérienne + emprise + flèche, coupe DP3 du
    BE) et le prompt cite chaque image UNE fois, sans redondance. Le plan de
    masse brut n'est plus joint (cartouche/ortho parasites).

    `plan_infos` : {"dims_m": [(L, l), ...]} extraites du plan.
    `guides` : guides_actifs(projet). `idx` : {role: numéro d'image jointe}
    parmi photo / photo_emprise / aerienne / coupe.
    `coupe_be` : la coupe jointe est la DP3 du bureau d'études.
    """
    idx = idx or {}
    omb = projet.get("ombriere") or {}
    ins = projet.get("insertion") or {}

    blocs = [
        "Insère une ombrière photovoltaïque de parking dans la photo "
        "(IMAGE 1), photoréaliste, comme si elle était déjà construite. "
        "Ne modifie rien d'autre de la scène."
    ]

    # -- emplacement : photo annotée + vue aérienne + cotes réelles
    empl = []
    if "photo_emprise" in idx:
        empl.append(
            f"IMAGE {idx['photo_emprise']} = cette même photo avec, en MAGENTA "
            "(rose vif), l'emprise au sol exacte de l'ombrière : construis-la "
            "pour que sa base épouse ce contour, pas ailleurs."
        )
        cal = (guides or {}).get("calibrage")
        if cal:
            lib = f" ({cal['libelle']})" if cal.get("libelle") else ""
            empl.append(
                f"Sur cette image, le segment JAUNE mesure "
                f"{_fmt(cal['distance_m'])} m dans la réalité{lib} : "
                "sers-t'en pour l'échelle."
            )
    if "aerienne" in idx:
        empl.append(
            f"IMAGE {idx['aerienne']} = vue aérienne du site : le contour "
            "MAGENTA y délimite la même emprise vue de dessus (la zone "
            "quadrillée bleue est le calepinage des panneaux du plan), et la "
            "grande flèche indique le sens de DESCENTE de la toiture. "
            "Retrouve les bâtiments et rangées de stationnement communs aux "
            "deux vues pour caler la position et l'orientation."
        )
    if plan_infos and plan_infos.get("dims_m"):
        liste = " ; ".join(
            f"{L:g} m x {l:g} m".replace(".", ",") for L, l in plan_infos["dims_m"])
        empl.append(f"Emprise réelle au sol : {liste}.")
    if not empl:
        empl.append("Implante l'ombrière sur la zone de stationnement la plus "
                    "cohérente de la photo.")
    empl.append(
        "Les contours magenta et la flèche sont des repères de travail : ne "
        "les dessine pas dans le rendu. Si des arbres se trouvent sur "
        "l'emprise, retire-les du montage ; ne touche pas aux autres arbres."
    )
    blocs.append("EMPLACEMENT — " + " ".join(empl))

    # -- structure : la coupe DP3 du BE fait foi
    struct = []
    if "coupe" in idx:
        origine = " du projet, dessinée par le bureau d'études" if coupe_be else ""
        struct.append(
            f"IMAGE {idx['coupe']} = la coupe technique{origine} : reproduis "
            "exactement ce profil (forme des poteaux, position des poteaux "
            "sous la toiture, pente, proportions)."
        )
    h_bas, h_haut = omb.get("garde_au_sol_m"), omb.get("hauteur_hors_tout_m")
    if h_bas and h_haut:
        struct.append(f"Hauteur {_fmt(h_bas)} m au point bas et "
                      f"{_fmt(h_haut)} m au point haut.")
    if omb.get("pente_deg"):
        struct.append(f"Pente {_fmt(omb['pente_deg'])}°.")
    struct.append("Acier galvanisé gris nu, toiture de modules photovoltaïques "
                  "noirs mats, sous-face claire.")
    blocs.append("STRUCTURE — " + " ".join(struct))

    # -- perspective
    blocs.append(
        "PERSPECTIVE — poteaux strictement verticaux ; les lignes de "
        "l'ombrière fuient vers les mêmes points de fuite que les marquages "
        "et bordures du parking ; base des poteaux posée sur le bitume ; "
        "l'ombrière rapetisse avec la distance."
    )

    # -- rendu + consignes libres
    rendu = [
        "RENDU — uniquement la photo (image 1) montée, plein cadre, même "
        "cadrage, même ratio, même lumière et mêmes ombres. L'ombre portée de "
        "l'ombrière est douce et translucide, cohérente avec les autres ombres "
        "de la photo, jamais un aplat noir uniforme. Aucun texte, aucun cadre, "
        "aucune légende, aucun tracé, aucune couleur peinte au sol : le bitume "
        "reste nu et ses marquages restent visibles."
    ]
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
    """Guides tracés sur la photo ACTIVE (emprises et/ou calibrage), sinon None."""
    ins = projet.get("insertion") or {}
    photo = ins.get("photo")
    if not photo:
        return None
    g = (ins.get("guides") or {}).get(photo) or {}
    emprises = [e for e in (g.get("emprises") or []) if len(e) >= 3]
    calibrage = g.get("calibrage") or None
    if calibrage and not (calibrage.get("a") and calibrage.get("b")
                          and calibrage.get("distance_m")):
        calibrage = None
    if not emprises and not calibrage:
        return None
    return {"emprises": emprises, "calibrage": calibrage}


MAGENTA = (255, 0, 200)   # couleur d'emprise : absente des scènes de parking
JAUNE = (255, 200, 0)     # repère d'échelle


def photo_emprise(projet: dict) -> Path | None:
    """Copie de la photo active avec l'emprise au sol tracée en MAGENTA.

    Contours seuls, AUCUN texte ni numéro (tout ce qui est écrit sur une
    image jointe finit par déteindre sur le rendu — constaté 17/07/2026).
    Le calibrage est un simple segment jaune ; sa valeur va dans le prompt.
    """
    from PIL import ImageDraw

    guides = guides_actifs(projet)
    photo = image_kit(projet, "photo")
    if not guides or not photo:
        return None

    image = Image.open(photo).convert("RGB")
    dr = ImageDraw.Draw(image)
    l, h = image.size
    ep = max(4, round(min(l, h) / 220))

    for emprise in guides["emprises"]:
        pts = [(x * l, y * h) for x, y in emprise]
        dr.line(pts + [pts[0]], fill=MAGENTA, width=ep)
        for px, py in pts:
            r = ep * 1.8
            dr.ellipse([px - r, py - r, px + r, py + r], fill=MAGENTA)

    cal = guides["calibrage"]
    if cal:
        a = (cal["a"][0] * l, cal["a"][1] * h)
        b = (cal["b"][0] * l, cal["b"][1] * h)
        dr.line([a, b], fill=JAUNE, width=ep)
        for px, py in (a, b):
            r = ep * 1.8
            dr.ellipse([px - r, py - r, px + r, py + r], fill=JAUNE)

    sortie = config.assets_dir(projet.get("id")) / "photo_emprise.png"
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
    """Résumé texte des guides pour le prompt."""
    bouts = []
    n = len(guides["emprises"])
    if n:
        bouts.append(f"{n} emprise{'s' if n > 1 else ''} tracée{'s' if n > 1 else ''}")
    cal = guides["calibrage"]
    if cal:
        lib = f" ({cal['libelle']})" if cal.get("libelle") else ""
        bouts.append(f"repère d'échelle {cal['distance_m']:g} m{lib}".replace(".", ","))
    return " · ".join(bouts)


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


def _appel_gemini(parts: list[dict]) -> bytes:
    """generateContent, sans outil (le grounding Google Search a été retiré
    le 17/07/2026 : source d'aléa, la structure vient de la coupe jointe)."""
    cle = os.environ.get("GEMINI_API_KEY")
    if not cle:
        raise InsertionError("Clé GEMINI_API_KEY absente (fichier .env CLAUDE).")
    url = f"{GEMINI_BASE}/models/{_modele()}:generateContent"
    corps: dict = {
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {"responseModalities": ["TEXT", "IMAGE"]},
    }
    try:
        with httpx.Client(timeout=180.0) as client:
            resp = client.post(url, headers={"x-goog-api-key": cle}, json=corps)
    except httpx.HTTPError as exc:
        raise InsertionError(f"Appel Gemini impossible ({exc.__class__.__name__}).") from exc
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
    """Assemble le lot d'images v4 + le prompt auto (sans appeler Gemini).

    Lot fabriqué pour la tâche, une image = un rôle :
      1. photo du site (la cible à éditer)
      2. photo + emprise MAGENTA (si tracée)
      3. vue aérienne (crop de l'ortho du plan) + emprise + flèche de pente
      4. coupe DP3 du BE
    Le plan brut n'est PLUS joint (cartouche et habillage parasites).
    Renvoie {chemins, roles, prompt}. Lève InsertionError si pas de photo.
    """
    photo = image_kit(projet, "photo")
    if not photo:
        raise InsertionError("Ajoutez d'abord une photo du site (upload ou reprise d'une pièce BE).")

    roles: list[str] = ["photo"]
    chemins: list[Path] = [photo]

    # 2. la photo annotée de l'emprise (magenta) + échelle (jaune)
    guides = guides_actifs(projet)
    if guides:
        annotee = photo_emprise(projet)
        if annotee:
            roles.append("photo_emprise")
            chemins.append(annotee)
        else:
            guides = None

    # 3. la vue aérienne annotée (emprise + flèche) + cotes réelles du plan
    plan_infos = None
    aerienne = aerienne_emprise(projet)
    if aerienne:
        roles.append("aerienne")
        chemins.append(aerienne)
        analyse, _ = _analyse_plan(projet)
        if analyse:
            plan_infos = {
                "dims_m": [(z["longueur_m"], z["largeur_m"])
                           for z in analyse["rangees"] if "longueur_m" in z],
            }

    # 4. la coupe DP3 du BE (repli catalogue si pas encore déposée)
    coupe_be = False
    coupe = image_kit(projet, "coupe_be")
    if coupe:
        coupe_be = True
    else:
        coupe = image_kit(projet, "coupe")
    if coupe:
        roles.append("coupe")
        chemins.append(coupe)

    idx = {role: i + 1 for i, role in enumerate(roles)}
    prompt = construire_prompt(projet, affinage=affinage, plan_infos=plan_infos,
                               guides=guides, idx=idx, coupe_be=coupe_be)
    return {"chemins": chemins, "roles": roles, "prompt": prompt}


def apercu_prompt(projet: dict) -> str:
    """Prompt qui SERAIT envoyé, pour l'aperçu éditable (sans génération)."""
    try:
        return _preparer_requete(projet)["prompt"]
    except InsertionError:
        return ""


def generer_image(projet: dict, affinage: str = "", prompt_override: str = "") -> dict:
    """Génère UNE insertion via Gemini (Nano Banana Pro).

    Pipeline v3 (17/07/2026) : photo + PLAN DE MASSE BRUT (le prompt en
    explique les conventions) + guides tracés (s'il y en a) + coupe DP3 du BE
    (sinon coupe catalogue nettoyée) -> génération -> décadrage + recollage de
    la scène. `prompt_override` : prompt édité à la main (l'auto est ignoré,
    les images restent les mêmes). Renvoie {fichier, date, etiquette, modele, prompt}.
    """
    if not api_configuree():
        raise InsertionError("Mode API non configuré : clé GEMINI_API_KEY absente.")
    req = _preparer_requete(projet, affinage=affinage)
    photo = req["chemins"][0]
    prompt = prompt_override.strip() if prompt_override and prompt_override.strip() else req["prompt"]
    parts: list[dict] = [{"text": prompt}] + [_part_image(c) for c in req["chemins"]]

    image = _appel_gemini(parts)
    image = decadrer(photo, image)
    if os.environ.get("GVDP_PRESERVER_SCENE", "1") != "0":
        image = preserver_scene(photo, image)
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
