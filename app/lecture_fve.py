"""Lecture de la Fiche de Validation d'Emprises (FVE) produite par le BE.

Le bureau d'études fournit, en plus des pièces du dossier, une FVE PowerPoint
qui contient déjà une bonne partie de ce que l'outil faisait ressaisir à la
main : puissance crête, module, point bas, inclinaison, largeur d'ombrière, et
surtout le photomontage d'insertion paysagère (la pièce DP6).

Deux principes, hérités de la lecture du plan de masse (app/lecture_plan.py) :

1. On PROPOSE, on n'impose pas. Les valeurs lues ne remplissent que les champs
   VIDES, elles sont renvoyées à l'interface qui les surligne, et l'utilisateur
   les corrige librement. Une FVE est un document d'avant-projet dont les
   valeurs bougent : l'importer en silence figerait une version périmée dans un
   dossier réglementaire, exactement le mécanisme qui a produit les
   incohérences d'adresse du 01/09/2026.
2. On ne DEVINE jamais. La FVE parle d'un « taux d'autoconsommation de 95 % » :
   on pourrait en déduire une autoconsommation avec vente du surplus, et se
   tromper. La destination de l'électricité reste donc un choix explicite de
   l'utilisateur à l'étape 3.

La localisation et les parcelles ne sont volontairement PAS touchées : elles
fonctionnent bien par géocodage et cadastre (décision de Florent, 01/09/2026).

Le gabarit de FVE est stable (confirmé par Florent) : la lecture s'appuie sur
les libellés du modèle. Tout libellé absent est signalé, jamais deviné.
"""
from __future__ import annotations

import io
import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

# Libellé de la planche portant le photomontage, et des deux images qu'elle
# porte. La liaison libellé -> image se fait par proximité VERTICALE : sur la
# FVE de référence, « Image source » est à 0,86 pouce et sa photo à 0,72 ;
# « Insertion Paysagère » à 3,92 et sa photo à 3,92.
LIBELLE_SOURCE = "image source"
LIBELLE_INSERTION = "insertion paysag"

# En dessous, une image est un pictogramme de la mise en page, pas une photo.
TAILLE_MINI_PHOTO = 200_000

# Titre de planche du gabarit : « 03 INSERTION PAYSAGERE », « 02 IMPLANTATION ».
_RE_TITRE_SECTION = re.compile(r"^\d{1,2}\s")


def _nombre(texte: str) -> float | None:
    """« 157,78 » ou « 3.5 » -> float. None si ce n'est pas un nombre."""
    try:
        return float(texte.replace(",", ".").strip())
    except (ValueError, AttributeError):
        return None


# Chaque règle : (clé du projet, expression, conversion).
# Les libellés viennent du gabarit FVE ; l'espace avant « : » est optionnel car
# PowerPoint insère parfois une espace insécable.
REGLES = [
    ("puissance_kwc", r"Puissance\s+crête\s*:\s*([\d\s,.]+)\s*kWc", _nombre),
    ("garde_au_sol_m", r"Point\s+bas\s*:\s*([\d\s,.]+)\s*m\b", _nombre),
    ("pente_deg", r"Inclinaison\s*:\s*([\d\s,.]+)\s*°", _nombre),
    # « Nombre de panneaux en largeur : 7 (12,3m) » : la cote entre parenthèses
    # est la largeur de l'ombrière, c'est-à-dire sa profondeur au sens du Cerfa.
    ("largeur_m", r"Nombre\s+de\s+panneaux\s+en\s+largeur\s*:[^(]*\(\s*([\d\s,.]+)\s*m\s*\)", _nombre),
]


def _texte_planches(prs) -> list[str]:
    """Texte de chaque forme, planche par planche (une entrée par forme)."""
    morceaux = []
    for planche in prs.slides:
        for forme in planche.shapes:
            if forme.has_text_frame and forme.text_frame.text.strip():
                morceaux.append(forme.text_frame.text)
            if getattr(forme, "has_table", False):
                for ligne in forme.table.rows:
                    morceaux.append(" | ".join(c.text for c in ligne.cells))
    return morceaux


def _module(morceaux: list[str]) -> dict:
    """Puissance unitaire et modèle du module.

    Le bloc « Modules » et le bloc « Onduleurs » portent les MÊMES libellés
    (« Marque-modèle », « Puissance nominale »). On les distingue par l'unité :
    le module est en Wc, l'onduleur en kVA.
    """
    for texte in morceaux:
        if "Marque-modèle" not in texte or "Wc" not in texte:
            continue
        champs = {}
        m = re.search(r"Puissance\s+nominale\s*:\s*([\d\s,.]+)\s*Wc", texte)
        if m:
            champs["module_puissance_wc"] = _nombre(m.group(1))
        m = re.search(r"Marque-modèle\s*:\s*([^\n|]+)", texte)
        if m:
            champs["module_modele"] = m.group(1).strip()
        return champs
    return {}


def _images_insertion(prs) -> dict:
    """Photo du site et photomontage d'insertion, par proximité des libellés.

    On renvoie l'image ENTIÈRE, pas le recadrage de la FVE : PowerPoint n'y
    montre qu'un bandeau (28 % de la surface sur la fiche de référence), choisi
    pour la mise en page de la fiche. L'image complète cadre bien mieux la
    planche DP6 du dossier.
    """
    from pptx.util import Emu

    resultat: dict[str, bytes] = {}
    for planche in prs.slides:
        libelles: list[tuple[float, str]] = []
        photos: list[tuple[float, bytes]] = []
        for forme in planche.shapes:
            haut = Emu(forme.top or 0).inches
            if forme.has_text_frame:
                minuscule = forme.text_frame.text.strip().lower()
                # Le TITRE de la planche (« 03 INSERTION PAYSAGERE ») contient
                # le même mot que le libellé de l'image et se trouve en haut,
                # donc plus près de la photo « source » que de l'insertion :
                # sans ce filtre, les deux rôles pointaient la même image.
                if _RE_TITRE_SECTION.match(minuscule):
                    continue
                if LIBELLE_SOURCE in minuscule:
                    libelles.append((haut, "source"))
                elif LIBELLE_INSERTION in minuscule and len(minuscule) < 40:
                    libelles.append((haut, "insertion"))
            if forme.__class__.__name__ == "Picture":
                try:
                    blob = forme.image.blob
                except (ValueError, AttributeError):
                    continue          # image liée, non embarquée
                if len(blob) >= TAILLE_MINI_PHOTO:
                    photos.append((haut, blob))
        if not (libelles and photos):
            continue
        for haut_libelle, role in libelles:
            if role in resultat:
                continue
            _, blob = min(photos, key=lambda p: abs(p[0] - haut_libelle))
            resultat[role] = blob
    return resultat


def lire_fve(chemin: Path) -> dict:
    """Lit une FVE .pptx. Renvoie {champs, images, trouve, manquant}.

    Ne lève jamais : un fichier illisible rend un résultat vide et tracé, comme
    la lecture du plan de masse.
    """
    vide = {"champs": {}, "images": {}, "trouve": [], "manquant": []}
    if Path(chemin).suffix.lower() != ".pptx":
        return vide
    try:
        from pptx import Presentation

        prs = Presentation(str(chemin))
        morceaux = _texte_planches(prs)
        texte = "\n".join(morceaux)

        champs: dict[str, float | str] = {}
        manquant: list[str] = []
        for cle, motif, convertir in REGLES:
            m = re.search(motif, texte, re.IGNORECASE)
            valeur = convertir(m.group(1)) if m else None
            if valeur is not None:
                champs[cle] = valeur
            else:
                manquant.append(cle)
        champs.update(_module(morceaux))

        return {
            "champs": champs,
            "images": _images_insertion(prs),
            "trouve": sorted(champs),
            "manquant": manquant,
        }
    except Exception:      # FVE corrompue, chiffrée, ou gabarit inattendu
        logger.warning("Lecture de la FVE impossible (%s)", Path(chemin).name,
                       exc_info=True)
        return vide


def appliquer_lecture(projet, lecture: dict) -> list[str]:
    """Remplit les champs VIDES de l'ombrière. Renvoie les champs proposés.

    Ne réécrit jamais une valeur déjà saisie : la FVE propose, l'utilisateur
    dispose. `module_modele` n'a pas de champ dédié dans le modèle, il alimente
    `type_module` qui sert d'information libre.
    """
    proposes: list[str] = []
    correspondances = {"module_modele": "type_module"}
    for cle, valeur in (lecture.get("champs") or {}).items():
        attribut = correspondances.get(cle, cle)
        if not hasattr(projet.ombriere, attribut):
            continue
        if getattr(projet.ombriere, attribut) in (None, ""):
            setattr(projet.ombriere, attribut, valeur)
            proposes.append(attribut)
    return proposes
