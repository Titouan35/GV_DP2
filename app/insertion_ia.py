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
    """Prompt Gemini co-écrit avec Florent le 17/07/2026 (v3).

    Court et déclaratif : une donnée = une source. Le plan de masse est JOINT
    BRUT et le prompt explique ses conventions de dessin (zone bleue, traits
    rouges, carrés gris, HAUT/BAS DE RAMPANT) ; le fond orthophoto sert de
    pont entre la vue aérienne et la photo au sol.

    `plan_infos` : {"echelle": "1/200"|None, "dims_m": [(L, l), ...]}.
    `guides` : guides_actifs(projet). `idx` : {role: numéro d'image jointe}.
    `coupe_be` : la coupe jointe est la DP3 du bureau d'études (elle prime :
    on ne décrit pas le profil catalogue).
    """
    idx = idx or {}
    omb = projet.get("ombriere") or {}
    ins = projet.get("insertion") or {}
    a_type = bool(omb.get("famille") or omb.get("puissance_kwc"))
    p = parametres_effectifs(omb) if a_type else None

    blocs = [
        "Modifie la photo (image 1) : ajoute une ombrière photovoltaïque de "
        "parking, photoréaliste, comme si elle était déjà construite."
    ]

    # -- le plan de masse et sa légende
    if "plan" in idx:
        echelle = ""
        if plan_infos and plan_infos.get("echelle"):
            echelle = f", à l'échelle {plan_infos['echelle']}"
        dims = ""
        if plan_infos and plan_infos.get("dims_m"):
            n = len(plan_infos["dims_m"])
            liste = " ; ".join(
                f"{L:g} m x {l:g} m".replace(".", ",") for L, l in plan_infos["dims_m"])
            dims = f" — {n} rangée{'s' if n > 1 else ''} de {liste}"
        entraxe = ""
        if omb.get("entraxe_m"):
            entraxe = f", espacées de {omb['entraxe_m']:g} m".replace(".", ",")
        blocs.append(
            f"LE PLAN DE MASSE (image {idx['plan']}) — comment le lire. C'est le "
            "plan officiel du projet, dessiné sur une photo aérienne du site, vue "
            f"de dessus{echelle}. La zone bleue quadrillée est le calepinage des "
            f"panneaux : c'est l'emprise exacte de l'ombrière à construire{dims}. "
            "Les traits rouges qui traversent la zone bleue sont les entraxes : "
            f"chaque trait rouge correspond à une file de poteaux{entraxe}. Les "
            "carrés gris dans la zone bleue sont les fondations des poteaux. Les "
            "étiquettes « HAUT DE RAMPANT » et « BAS DE RAMPANT » désignent les "
            "deux bords du versant : la toiture descend du bord HAUT vers le bord "
            "BAS. Tout le reste du plan (traits de raccordement colorés, "
            "cartouche, textes) est administratif : ignore-le."
        )

    # -- placement
    if "plan" in idx:
        placement = [
            "PLACEMENT. Repère sur la photo aérienne du plan les bâtiments, les "
            "rangées de stationnement et les marquages qui entourent la zone "
            "bleue. Retrouve ces mêmes repères dans la photo au sol (image 1) et "
            "pose l'ombrière exactement à cet emplacement, avec la même "
            "orientation que sur le plan."
        ]
    else:
        placement = [
            "PLACEMENT. Implante l'ombrière sur la zone de stationnement la plus "
            "cohérente de la photo."
        ]
    if guides and "guides" in idx:
        consignes = []
        if guides["emprises"]:
            consignes.append(
                f"Les polygones verts tracés sur l'image {idx['guides']} "
                "confirment cet emplacement directement dans la photo : l'emprise "
                "au sol de l'ombrière doit les épouser exactement."
            )
        if guides["calibrage"]:
            cal = guides["calibrage"]
            lib = f" ({cal['libelle']})" if cal.get("libelle") else ""
            consignes.append(
                f"Le segment jaune mesure {_fmt(cal['distance_m'])} m{lib}.")
        consignes.append("Ne reproduis pas ces tracés dans le rendu.")
        placement.append(" ".join(consignes))
    placement.append(
        "Si des arbres se trouvent sur l'emprise de l'ombrière, retire-les du "
        "montage (ils seraient abattus avant les travaux) ; ne touche pas aux "
        "arbres situés hors emprise."
    )
    blocs.append(" ".join(placement))

    # -- perspective (point faible constaté : lignes de fuite)
    blocs.append(
        "PERSPECTIVE. Respecte rigoureusement la perspective de la photo. Les "
        "lignes de l'ombrière (bord avant et bord arrière de la toiture, "
        "alignement des poteaux, arêtes des modules et des pannes) doivent fuir "
        "vers les MÊMES points de fuite que les lignes du parking déjà visibles "
        "(marquages au sol, bordures, façade du bâtiment) : elles sont parallèles "
        "dans la réalité, donc elles convergent vers le même point à l'horizon. "
        "Les poteaux sont strictement verticaux. Plus une partie de l'ombrière "
        "est loin de l'objectif, plus elle est petite et haute vers la ligne "
        "d'horizon. La base de chaque poteau touche le sol au niveau du bitume "
        "(l'ombrière ne flotte pas et n'est pas vue de trop haut)."
    )

    # -- structure (la coupe DP3 du BE prime sur le profil catalogue)
    if coupe_be:
        tete = ("STRUCTURE. Reproduis le profil exact de la coupe technique du "
                "projet, dessinée par le bureau d'études (dernière image)")
    elif "coupe" in idx:
        tete = "STRUCTURE. Reproduis le profil exact de la coupe technique (dernière image)"
    else:
        tete = "STRUCTURE. Ombrière de parking standard"
    details = []
    if p and not coupe_be:
        details.append(DESCRIPTIONS_COUPE.get(p["famille"], "structure standard"))
    h_bas = omb.get("garde_au_sol_m") or (p and p.get("h_bas_m"))
    h_haut = omb.get("hauteur_hors_tout_m") or (p and p.get("h_haut_m"))
    if h_bas and h_haut:
        details.append(
            f"hauteur {_fmt(h_bas)} m au point bas et {_fmt(h_haut)} m au point haut")
    pente = omb.get("pente_deg") or (p and p.get("pente_deg"))
    if pente:
        details.append(f"pente {_fmt(pente)}°")
    details.append("acier galvanisé nu")
    modules = "toiture de modules photovoltaïques noirs mats"
    if omb.get("module_dimensions"):
        modules += f" de {omb['module_dimensions']}"
    details.append(modules)
    details.append("sous-face claire")
    blocs.append(tete + " : " + ", ".join(details) + ".")

    # -- rendu + consignes libres
    rendu = ["RENDU. L'image produite est UNIQUEMENT la photo montée, plein "
             "cadre, au même cadrage et au même ratio que l'image 1 : pas de "
             "cadre, pas de cartouche, pas de légende, pas de mise en page de "
             "plan — le plan et la coupe sont des références de travail, pas des "
             "modèles de présentation. Ne peins AUCUNE couleur sur le sol : ni "
             "vert, ni bleu, ni grille, ni quadrillage. Les zones bleues du plan "
             "et les contours verts/jaunes sont des repères de travail, jamais "
             "un marquage à reproduire sur le bitume, qui reste du bitume nu. "
             "Même lumière et mêmes ombres que la photo d'origine, sans aucun "
             "texte ni tracé."]
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
            return _pdf_premiere_page_png(source, assets / "kit_coupe_be.png")
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
        # contour seul (pas de remplissage : une zone verte pleine se fait
        # recopier au sol par le modèle — constaté 17/07/2026)
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
    """Assemble images + prompt auto (sans appeler Gemini).

    Renvoie {chemins, roles, prompt}. Sert à la fois à la génération et à
    l'aperçu éditable du prompt côté UI. Lève InsertionError si pas de photo.
    """
    photo = image_kit(projet, "photo")
    if not photo:
        raise InsertionError("Ajoutez d'abord une photo du site (upload ou reprise d'une pièce BE).")

    roles: list[str] = ["photo"]
    chemins: list[Path] = [photo]

    # image 2 : le plan de masse BRUT + ses infos extraites (échelle, rangées)
    plan_infos = None
    plan = image_kit(projet, "plan")
    if plan:
        roles.append("plan")
        chemins.append(plan)
        source = _document_image(projet, "dp2")
        if source and source.suffix.lower() == ".pdf":
            from . import implantation as mod_implantation
            analyse = mod_implantation.analyser_plan(source)
            if analyse:
                plan_infos = {
                    "echelle": analyse.get("echelle_plan"),
                    "dims_m": [(z["longueur_m"], z["largeur_m"])
                               for z in analyse["rangees"] if "longueur_m" in z],
                }

    # image 3 : la photo annotée des guides, si tracés
    guides = guides_actifs(projet)
    if guides:
        annotee = photo_guidee(projet)
        if annotee:
            roles.append("guides")
            chemins.append(annotee)
        else:
            guides = None

    # dernière image : la coupe DP3 du BE prime, sinon la coupe catalogue
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
