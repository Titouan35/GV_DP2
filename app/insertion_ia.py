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


def construire_prompt(projet: dict, affinage: str = "",
                      implantation_resume: str | None = None,
                      pieces_jointes: list[str] | None = None,
                      guides: dict | None = None) -> str:
    """Assemble le prompt ultra-détaillé (6 blocs) depuis les inputs du projet.

    Déterministe : mêmes inputs → même prompt. Rédigé en français, adressé à
    l'éditeur photo Gemini. Tolère les champs manquants.
    `implantation_resume` : résumé du schéma d'implantation extrait du plan.
    `guides` : guides tracés sur la photo (guides_actifs) — contrainte n°1.
    `pieces_jointes` : rôles des images jointes, dans l'ordre d'envoi.
    """
    omb = projet.get("ombriere") or {}
    ins = projet.get("insertion") or {}
    a_type = bool(omb.get("famille") or omb.get("puissance_kwc"))
    p = parametres_effectifs(omb) if a_type else None

    plan_dispo = _document_image(projet, "dp2") is not None
    coupe_dispo = _coupe_source(projet) is not None

    # -- bloc 1 : rôle & tâche
    b1 = (
        "RÔLE — Tu es un éditeur photo expert en photomontage architectural. "
        "À partir de la PHOTO du site que je te fournis, insère une ombrière "
        "photovoltaïque de parking de façon photoréaliste et crédible, sans rien "
        "modifier d'autre dans la scène (bâtiments, véhicules, sol, ciel, "
        "marquages, végétation)."
    )

    # -- bloc 2 : la scène et l'implantation
    b2_lignes = [
        "LA SCÈNE — La photo montre l'aire de stationnement à équiper. Conserve à "
        "l'identique les mâts d'éclairage, arbres, bordures, marquages au sol et "
        "véhicules déjà présents.",
    ]
    if guides:
        consignes_guides = []
        if guides["emprises"]:
            n = len(guides["emprises"])
            consignes_guides.append(
                "les polygones VERTS délimitent l'emprise au sol EXACTE de "
                f"{'chacune des ' + str(n) + ' rangées' if n > 1 else 'la rangée'} "
                "d'ombrière : construis l'ombrière pour que sa projection au sol "
                "épouse précisément chaque polygone vert (poteaux à l'intérieur, "
                "rien qui déborde)"
            )
        if guides["calibrage"]:
            cal = guides["calibrage"]
            lib = f", {cal['libelle']}" if cal.get("libelle") else ""
            consignes_guides.append(
                "le segment JAUNE est un repère d'échelle : il mesure "
                f"{cal['distance_m']:g} m dans la réalité{lib} ; sers-t'en pour "
                "dimensionner l'ombrière".replace(".", ",")
            )
        b2_lignes.append(
            "PRIORITÉ ABSOLUE, LES TRACÉS SUR PHOTO FONT FOI : la deuxième image "
            "est la MÊME photo annotée de guides ; " + " ; ".join(consignes_guides) +
            ". Ces tracés sont des GUIDES DE TRAVAIL : ne reproduis NI les traits "
            "verts, NI le segment jaune, NI les numéros dans l'image finale."
        )
    if implantation_resume and guides:
        b2_lignes.append(
            f"Pour information, le plan de masse officiel indique : {implantation_resume}. "
            "Respecte ces proportions et ce sens de pente pour tout ce que les "
            "tracés verts ne montrent pas."
        )
    elif implantation_resume:
        b2_lignes.append(
            "IMPÉRATIF, LE SCHÉMA D'IMPLANTATION JOINT FAIT FOI : il est extrait du "
            "plan de masse officiel (vue de dessus). Les zones BLEUES sont l'emprise "
            f"exacte des rangées de panneaux ({implantation_resume}), les traits ROUGES "
            "la trame des poteaux, la grande flèche le sens de DESCENTE de la pente. "
            "Repère sur la photo les rangées de stationnement correspondantes et pose "
            "l'ombrière exactement là : même nombre de rangées, mêmes proportions, même "
            "orientation relative, versant descendant dans le sens de la flèche. "
            "Ne couvre RIEN d'autre que ces emprises, ni plus, ni moins."
        )
    elif plan_dispo:
        b2_lignes.append(
            "IMPÉRATIF, LE PLAN DE MASSE JOINT FAIT FOI : reproduis EXACTEMENT le nombre "
            "de rangées d'ombrières, leur position et leur orientation telles qu'elles "
            "figurent sur le plan, par rapport au bâtiment, à l'entrée et aux limites du "
            "parking. Ne couvre QUE les rangées indiquées au plan, ni plus, ni moins, et "
            "respecte le sens de la pente indiqué."
        )
    else:
        b2_lignes.append(
            "Aucun plan de masse joint : implante l'ombrière sur la zone de parking "
            "la plus dégagée et la plus cohérente de la photo."
        )
    b2 = " ".join(b2_lignes)

    # -- bloc 3 : l'objet, spécifié précisément (le linéaire vient du schéma)
    if p:
        double = p.get("double")
        forme = "double pente (structure en T, poteau central)" if double else \
            f"monopente (poteau côté {p.get('poteau', 'haut')})"
        modules = ("Couverture de modules photovoltaïques full black (noirs, finition "
                   "MATE, aucun reflet), sous-face claire.")
        if omb.get("module_puissance_wc") or omb.get("module_dimensions"):
            det = ", ".join(filter(None, [
                f"{omb['module_puissance_wc']:g} Wc" if omb.get("module_puissance_wc") else None,
                omb.get("module_dimensions"),
            ]))
            modules = ("Couverture de modules photovoltaïques full black (noirs, finition "
                       f"MATE, aucun reflet) de {det}, sous-face claire.")
        b3 = (
            "L'OBJET — Ombrière de parking type « {fam} », {forme}. "
            "Profil : {prof} de profondeur couverte, pente {pente}, hauteur ≈ {hh} au "
            "point haut et ≈ {hb} au point bas. Le linéaire et le nombre de rangées "
            "suivent le schéma d'implantation. Structure en acier galvanisé gris clair, "
            "sans aucun marquage de couleur ; poteaux caisson, arbalétrier effilé et "
            "bracon. {modules} {coupe}"
        ).format(
            fam=libelle_coupe(p.get("famille")),
            forme=forme,
            modules=modules,
            prof=_fmt(p.get("profondeur_m"), " m"),
            pente=_fmt(p.get("pente_deg"), "°"),
            hh=_fmt(p.get("h_haut_m"), " m"),
            hb=_fmt(p.get("h_bas_m"), " m"),
            coupe=("Reporte-toi à la COUPE technique jointe pour le profil et les "
                   "proportions exactes." if coupe_dispo else ""),
        ).strip()
    else:
        b3 = (
            "L'OBJET — Ombrière de parking classique : poteaux en acier galvanisé "
            "gris clair, structure fine, modules photovoltaïques full black (noirs "
            "mats). Renseigne le type d'ombrière à l'étape 3 pour un profil précis."
        )

    # -- bloc 4 : échelle
    nb_places = omb.get("nb_places")
    b4_lignes = []
    if nb_places:
        b4_lignes.append(
            f"ÉCHELLE — L'ombrière couvre environ {nb_places:g} places de "
            "stationnement (une place = 2,50 m de large)."
        )
    else:
        b4_lignes.append(
            "ÉCHELLE — Une place de stationnement = 2,50 m de large : sers-t'en "
            "comme repère pour dimensionner l'ombrière."
        )
    if p:
        b4_lignes.append(
            f"La garde au sol sous la panne basse est d'environ {_fmt(p.get('h_bas_m'), ' m')} "
            "(passage véhicule dessous). Dimensionne l'ombrière en cohérence avec ces repères."
        )
    echelle_desc = (ins.get("echelle_desc") or "").strip()
    echelle_d = ins.get("echelle_distance_m")
    if echelle_desc and echelle_d:
        b4_lignes.append(
            f"Repère d'échelle relevé sur la photo : {echelle_desc} mesure environ "
            f"{_fmt(echelle_d, ' m')} ; sers-t'en pour dimensionner l'ombrière à la bonne taille."
        )
    b4 = " ".join(b4_lignes)

    # -- bloc 5 : lumière & intégration
    b5 = (
        "LUMIÈRE & INTÉGRATION — Reproduis des ombres portées cohérentes en "
        "direction et longueur avec celles des mâts, arbres et véhicules déjà "
        "visibles. Conserve le grain, la netteté, l'exposition et la balance des "
        "couleurs de la photo source : le montage doit sembler pris au même instant. "
        "Surfaces MATES partout : aucun reflet spéculaire, aucun éblouissement, "
        "aucun effet miroir sur les modules ni sur la structure. "
        "Pour le réalisme de la structure et des matériaux, appuie-toi sur la PHOTO "
        "DE RÉFÉRENCE jointe (ombrière réelle en service) et sur des photos réelles "
        "d'ombrières photovoltaïques de parkings de supermarchés français "
        "(recherche d'images : « ombrière photovoltaïque parking supermarché France »)."
    )

    # -- bloc 6 : consignes libres + contraintes négatives
    libres = (ins.get("consignes") or "").strip()
    corrections = (affinage or ins.get("affinage") or "").strip()
    b6_lignes = ["CONSIGNES & INTERDITS —"]
    if libres:
        b6_lignes.append(f"Consignes : {libres}.")
    if corrections:
        b6_lignes.append(f"Corrections à appliquer : {corrections}.")
    b6_lignes.append(
        "Ne déplace pas les véhicules, ne modifie pas les bâtiments, n'invente pas "
        "d'arrière-plan. Ne peins RIEN de bleu : aucune dalle, aucun marquage, aucune "
        "surface bleue sur le bitume, aucune bande de couleur sur les poteaux. Le bleu "
        "des documents techniques est une convention de dessin, pas une couleur à "
        "reproduire. Conserve le revêtement du parking tel quel. Aucun reflet ajouté. "
        "FORMAT DE SORTIE : exactement la photo d'origine (image 1) montée, au MÊME "
        "cadrage et au MÊME ratio, plein cadre. AUCUN bandeau, AUCUN titre, AUCUNE "
        "légende, AUCUNE bordure blanche, AUCUN texte, AUCUN watermark, AUCUN logo : "
        "les mises en page des documents joints ne doivent pas déteindre sur le rendu. "
        "Rends uniquement l'image finale, photoréaliste."
    )
    b6 = " ".join(b6_lignes)

    blocs = [b1, b2, b3, b4, b5, b6]
    if pieces_jointes:
        NOMS = {"photo": "la PHOTO du site à modifier",
                "guides": "la même photo ANNOTÉE des guides (emprises vertes, repère jaune)",
                "schema": "le SCHÉMA D'IMPLANTATION (extrait du plan de masse)",
                "plan": "le PLAN DE MASSE",
                "coupe": "la COUPE technique du type d'ombrière",
                "reference": "une PHOTO DE RÉFÉRENCE d'ombrière réelle (style uniquement)"}
        liste = " ; ".join(f"image {i} = {NOMS.get(role, role)}"
                           for i, role in enumerate(pieces_jointes, 1))
        blocs.append(f"PIÈCES JOINTES — {liste}.")

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

    if role == "schema":
        # schéma d'implantation extrait du plan de masse (cache par date du plan)
        source = _document_image(projet, "dp2")
        if not source or source.suffix.lower() != ".pdf":
            return None
        from . import implantation
        sortie = assets / "kit_schema_implantation.png"
        if sortie.exists() and sortie.stat().st_mtime >= source.stat().st_mtime:
            return sortie
        return implantation.generer_schema(source, sortie)

    if role == "coupe":
        source = _coupe_source(projet)
        if not source:
            return None
        return _coupe_nettoyee(source, assets / "kit_coupe.png")

    if role == "reference":
        chemin = (config.REPO_ROOT / "app" / "gabarits" / "refs"
                  / "ombrieres_bellerive_sur_allier_ccbysa40.jpg")
        return chemin if chemin.exists() else None

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


def photo_guidee(projet: dict) -> Path | None:
    """Copie de la photo active avec les guides dessinés (pour Gemini).

    Emprises = polygones VERT vif à sommets numérotés ; calibrage = segment
    JAUNE coté. Régénérée à chaque appel (les guides changent souvent).
    """
    from PIL import ImageDraw

    guides = guides_actifs(projet)
    photo = image_kit(projet, "photo")
    if not guides or not photo:
        return None
    from .planches.base import police

    image = Image.open(photo).convert("RGB")
    dr = ImageDraw.Draw(image, "RGBA")
    l, h = image.size
    ep = max(4, round(min(l, h) / 220))

    VERT = (0, 230, 90)
    for n, emprise in enumerate(guides["emprises"], 1):
        pts = [(x * l, y * h) for x, y in emprise]
        dr.polygon(pts, fill=(0, 230, 90, 46))
        dr.line(pts + [pts[0]], fill=VERT, width=ep)
        for i, (px, py) in enumerate(pts, 1):
            r = ep * 2.2
            dr.ellipse([px - r, py - r, px + r, py + r], fill=VERT)
            dr.text((px + r + 2, py - r - 2), str(i), font=police(ep * 7), fill=VERT)
        cx = sum(p[0] for p in pts) / len(pts)
        cy = sum(p[1] for p in pts) / len(pts)
        dr.text((cx, cy), f"RANGÉE {n}", font=police(ep * 8, True),
                fill=(255, 255, 255, 235), anchor="mm")

    cal = guides["calibrage"]
    if cal:
        JAUNE = (255, 200, 0)
        a = (cal["a"][0] * l, cal["a"][1] * h)
        b = (cal["b"][0] * l, cal["b"][1] * h)
        dr.line([a, b], fill=JAUNE, width=ep)
        for px, py in (a, b):
            r = ep * 2.2
            dr.ellipse([px - r, py - r, px + r, py + r], fill=JAUNE)
        etiquette = f"{cal['distance_m']:g} m".replace(".", ",")
        if cal.get("libelle"):
            etiquette += f" ({cal['libelle']})"
        # étiquette maintenue dans le cadre (ancrage à droite près du bord)
        cx = (a[0] + b[0]) / 2
        ancre = "rb" if cx > l * 0.72 else ("lb" if cx < l * 0.28 else "mb")
        dr.text((cx, (a[1] + b[1]) / 2 - ep * 5), etiquette,
                font=police(ep * 8, True), fill=JAUNE, anchor=ancre)

    sortie = config.assets_dir(projet.get("id")) / "photo_guidee.png"
    sortie.parent.mkdir(parents=True, exist_ok=True)
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


def _appel_gemini(parts: list[dict], recherche: bool = True) -> bytes:
    """generateContent avec grounding Google Search (repli sans outil si refusé)."""
    cle = os.environ.get("GEMINI_API_KEY")
    if not cle:
        raise InsertionError("Clé GEMINI_API_KEY absente (fichier .env CLAUDE).")
    url = f"{GEMINI_BASE}/models/{_modele()}:generateContent"
    corps: dict = {
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {"responseModalities": ["TEXT", "IMAGE"]},
    }
    if recherche:
        corps["tools"] = [{"google_search": {}}]  # inspiration images réelles
    try:
        with httpx.Client(timeout=180.0) as client:
            resp = client.post(url, headers={"x-goog-api-key": cle}, json=corps)
    except httpx.HTTPError as exc:
        raise InsertionError(f"Appel Gemini impossible ({exc.__class__.__name__}).") from exc
    if resp.status_code == 400 and recherche:
        # le modèle (ex. Lite) ne supporte pas le grounding : on réessaie sans
        return _appel_gemini(parts, recherche=False)
    if resp.status_code == 429:
        raise InsertionError("Quota Gemini atteint (ou facturation à vérifier) : réessayez dans un instant.")
    if resp.status_code in (401, 403):
        raise InsertionError("Clé Gemini refusée : vérifiez GEMINI_API_KEY et la facturation du projet Google.")
    if resp.status_code == 404:
        raise InsertionError(f"Modèle « {_modele()} » introuvable : ajustez GVDP_GEMINI_MODEL dans le .env.")
    if resp.status_code != 200:
        raise InsertionError(f"Gemini a répondu HTTP {resp.status_code} : {resp.text[:300]}")
    return _extraire_image(resp.json())


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


def generer_image(projet: dict, affinage: str = "") -> dict:
    """Génère UNE insertion via Gemini (Nano Banana Pro).

    Pipeline : photo + SCHÉMA D'IMPLANTATION (extrait du plan de masse ; repli
    plan brut) + coupe + photo de référence réelle -> génération -> recollage
    de la scène hors zones construites. Renvoie le dict image {fichier, date,
    etiquette, modele, prompt}. Lève InsertionError avec un message actionnable.
    """
    if not api_configuree():
        raise InsertionError("Mode API non configuré : clé GEMINI_API_KEY absente.")
    photo = image_kit(projet, "photo")
    if not photo:
        raise InsertionError("Ajoutez d'abord une photo du site (upload ou reprise d'une pièce BE).")

    # guides tracés sur la photo (contrainte n°1) + schéma extrait du plan
    implantation_resume = None
    roles: list[str] = ["photo"]
    chemins: list[Path] = [photo]
    guides = guides_actifs(projet)
    if guides:
        annotee = photo_guidee(projet)
        if annotee:
            roles.append("guides")
            chemins.append(annotee)
        else:
            guides = None

    # implantation tirée du plan : résumé texte toujours ; le schéma n'est
    # JOINT que sans guides photo (sinon sa mise en page déteint sur le rendu)
    source = _document_image(projet, "dp2")
    if source:
        from . import implantation as mod_implantation
        analyse = mod_implantation.analyser_plan(source) if source.suffix.lower() == ".pdf" else None
        if analyse:
            implantation_resume = mod_implantation.resume(analyse)
    if not guides:
        schema = image_kit(projet, "schema")
        if schema:
            roles.append("schema")
            chemins.append(schema)
        elif (plan := image_kit(projet, "plan")):
            roles.append("plan")
            chemins.append(plan)
    for role in ("coupe", "reference"):
        chemin = image_kit(projet, role)
        if chemin:
            roles.append(role)
            chemins.append(chemin)

    prompt = construire_prompt(projet, affinage=affinage,
                               implantation_resume=implantation_resume,
                               pieces_jointes=roles, guides=guides)
    parts: list[dict] = [{"text": prompt}] + [_part_image(c) for c in chemins]

    image = _appel_gemini(parts)
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
