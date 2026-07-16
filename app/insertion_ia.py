"""Module Insertion IA (phase 5) — générateur de prompt SANS API.

Flux figé le 2026-07-16 (PLAN_OUTIL_DP.md §6 bis) : l'outil n'appelle aucune
API et ne fait sortir aucune donnée. Au clic « Générer le prompt », il produit
deux choses :

1. Le **prompt** ultra-détaillé (6 blocs), assemblé de façon déterministe
   depuis les inputs du projet, destiné à **ChatGPT (GPT image)**.
2. Le **kit d'images à joindre** : (a) photo du site, (b) plan de masse (DP2,
   upload BE), (c) coupe du type d'ombrière choisi (COUPES/ → PNG).

L'utilisateur colle le prompt dans son ChatGPT, y joint les 3 images, génère
l'insertion, puis ré-importe l'image retenue dans l'outil (zone de dépôt).

Les visuels obtenus sont réservés au commercial, étiquetés « visuel IA », et
ne servent JAMAIS de pièce DP6 (fournie par le BE).
"""
from __future__ import annotations

from pathlib import Path

import pypdfium2 as pdfium

from . import config
from .catalogue import CATALOGUE, parametres_effectifs

# ------------------------------------------------------------------ descriptif

MODE_EMPLOI = [
    "Ouvre ChatGPT (un modèle avec génération d'image, « GPT image »).",
    "Colle le prompt (déjà copié dans le presse-papier).",
    "Joins les images du kit ci-contre (photo du site, plan de masse, coupe).",
    "Génère, puis régénère/affine dans ChatGPT jusqu'au rendu voulu.",
    "Glisse l'image retenue dans la zone de dépôt de l'outil.",
]

RAPPELS = [
    "Visuel réservé au commercial, étiqueté « visuel IA » : jamais utilisé en pièce DP6.",
    "Aucune API, aucune clé, aucun coût : la génération se fait dans TON ChatGPT.",
    "Aucune donnée du site ne sort de l'outil (c'est toi qui portes le prompt).",
    "Photo d'entrée nette, à hauteur d'œil, zone d'implantation dégagée = clé du réalisme.",
]


def apercu() -> dict:
    """Descriptif générique du module (indépendant d'un projet)."""
    return {
        "mode": "generateur_prompt",
        "api": False,
        "cible": "ChatGPT (GPT image)",
        "mode_emploi": MODE_EMPLOI,
        "rappels": RAPPELS,
    }


# ------------------------------------------------------------------ prompt

def _fmt(v, suffixe="", defaut="—"):
    """Formatte un nombre à la française (virgule décimale), sinon tel quel."""
    if v is None:
        return defaut
    if isinstance(v, (int, float)):
        return f"{v:g}".replace(".", ",") + suffixe
    return f"{v}{suffixe}"


def construire_prompt(projet: dict, affinage: str = "") -> str:
    """Assemble le prompt ultra-détaillé (6 blocs) depuis les inputs du projet.

    Déterministe : mêmes inputs → même prompt. Rédigé en français, adressé à
    ChatGPT en éditeur photo. Tolère les champs manquants (valeurs génériques).
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

    # -- bloc 2 : la scène
    b2_lignes = [
        "LA SCÈNE — La photo montre l'aire de stationnement à équiper. Conserve à "
        "l'identique les mâts d'éclairage, arbres, bordures, marquages au sol et "
        "véhicules déjà présents.",
    ]
    if plan_dispo:
        b2_lignes.append(
            "Un PLAN DE MASSE est joint : il porte l'implantation réelle et "
            "l'orientation des rangées d'ombrières — respecte-les précisément "
            "(position, sens, nombre de rangées)."
        )
    else:
        b2_lignes.append(
            "Aucun plan de masse joint : implante l'ombrière sur la zone de parking "
            "la plus dégagée et la plus cohérente de la photo."
        )
    b2 = " ".join(b2_lignes)

    # -- bloc 3 : l'objet, spécifié précisément
    if p:
        double = p.get("double")
        forme = "double pente (structure en T, poteau central)" if double else \
            f"monopente (poteau côté {p.get('poteau', 'haut')})"
        b3 = (
            "L'OBJET — Ombrière de parking type « {fam} », {forme}. "
            "Dimensions : {L} de long sur {prof} de profondeur couverte, {trav} travées "
            "à {entr} d'entraxe, pente {pente}. Hauteur hors-tout ≈ {hh} au point haut, "
            "≈ {hb} au point bas. Structure en acier galvanisé gris clair avec fines "
            "bandes bleues sur les poteaux ; poteaux caisson, arbalétrier effilé et "
            "bracon. Couverture de modules photovoltaïques full black (noirs mats, non "
            "réfléchissants), sous-face claire. {coupe}"
        ).format(
            fam=p.get("famille", "ombrière PV"),
            forme=forme,
            L=_fmt(p.get("longueur_m"), " m"),
            prof=_fmt(p.get("profondeur_m"), " m"),
            trav=_fmt(p.get("nb_travees")),
            entr=_fmt(p.get("entraxe_m"), " m"),
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
    b4 = " ".join(b4_lignes)

    # -- bloc 5 : lumière & intégration
    b5 = (
        "LUMIÈRE & INTÉGRATION — Reproduis des ombres portées cohérentes en "
        "direction et longueur avec celles des mâts, arbres et véhicules déjà "
        "visibles. Conserve le grain, la netteté, l'exposition et la balance des "
        "couleurs de la photo source : le montage doit sembler pris au même instant."
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
        "d'arrière-plan. Pas de watermark, pas de texte, pas de logo ajouté. "
        "Rends uniquement l'image finale, photoréaliste."
    )
    b6 = " ".join(b6_lignes)

    return "\n\n".join([b1, b2, b3, b4, b5, b6])


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

    if role == "coupe":
        source = _coupe_source(projet)
        if not source:
            return None
        return _pdf_premiere_page_png(source, assets / "kit_coupe.png")

    return None


def kit(projet: dict) -> list[dict]:
    """Liste des 3 images à joindre, avec leur disponibilité et un libellé."""
    projet_id = projet.get("id")
    base = f"/api/projets/{projet_id}/insertion/kit"
    items = [
        {
            "role": "photo",
            "titre": "Photo du site",
            "note": "la scène à équiper (upload ci-dessus)",
            "requis": True,
        },
        {
            "role": "plan",
            "titre": "Plan de masse",
            "note": "implantation et orientation des rangées (DP2, étape 4)",
            "requis": False,
        },
        {
            "role": "coupe",
            "titre": "Coupe du type d'ombrière",
            "note": "profil exact de la structure (étape 3)",
            "requis": False,
        },
    ]
    for it in items:
        dispo = image_kit(projet, it["role"]) is not None
        it["disponible"] = dispo
        it["url"] = f"{base}/{it['role']}?t=0" if dispo else None
    return items


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
