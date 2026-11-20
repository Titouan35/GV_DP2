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

from . import config

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


def _entier(texte: str) -> int | None:
    """« 7 » -> 7. Un compte de modules n'a pas de décimale."""
    try:
        return int(texte.strip())
    except (ValueError, AttributeError):
        return None


# Chaque règle : (clé du projet, expression, conversion).
# Les libellés viennent du gabarit FVE ; l'espace avant « : » est optionnel car
# PowerPoint insère parfois une espace insécable.
REGLES = [
    ("puissance_kwc", r"Puissance\s+crête\s*:\s*([\d\s,.]+)\s*kWc", _nombre),
    # Bloc « Puissance projet » : la puissance AC est la puissance électrique
    # nécessaire au projet, celle du cadre 4.2.1 du Cerfa. À ne pas confondre
    # avec la puissance crête, qui est juste au-dessus dans le même bloc.
    #
    # Le DEUX-POINTS est la clé du motif : une autre planche porte un tableau
    # « Puissance AC estimée [kVA] | 250 | 125 », deux valeurs d'une étude de
    # variantes dont aucune n'est forcément celle retenue. Exiger « AC : »
    # écarte ce tableau et ne retient que la donnée du projet.
    ("puissance_raccordement_kva", r"Puissance\s+AC\s*:\s*([\d\s,.]+)\s*kVA", _nombre),
    ("garde_au_sol_m", r"Point\s+bas\s*:\s*([\d\s,.]+)\s*m\b", _nombre),
    ("pente_deg", r"Inclinaison\s*:\s*([\d\s,.]+)\s*°", _nombre),
    # « Nombre de panneaux en largeur : 7 (12,3m) » : la cote entre parenthèses
    # est la largeur de l'ombrière, c'est-à-dire sa profondeur au sens du Cerfa.
    ("largeur_m", r"Nombre\s+de\s+panneaux\s+en\s+largeur\s*:[^(]*\(\s*([\d\s,.]+)\s*m\s*\)", _nombre),
    # Le NOMBRE lui-même, qu'on ne lisait pas : avec le point bas, il désigne
    # la coupe type du catalogue à déposer en DP3 (app/coupes_dp3.py).
    ("nb_modules_rampant", r"Nombre\s+de\s+panneaux\s+en\s+largeur\s*:\s*(\d+)", _entier),
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
        # « Nombre : 322 » : le nombre TOTAL de modules. Il est dans le même
        # bloc, donc distinct du « Nombre » du bloc Onduleurs. Il sert à
        # estimer l'emprise au sol créée (cadre 4.3 du Cerfa).
        m = re.search(r"Nombre\s*:\s*([\d\s]+)", texte)
        if m:
            nombre = _entier(m.group(1).replace(" ", "").replace(" ", ""))
            if nombre:
                champs["nb_modules_total"] = nombre
        return champs
    return {}


def _onduleur(morceaux: list[str]) -> dict:
    """Modèle et nombre d'onduleurs : le bloc « Marque-modèle » SANS « Wc ».

    « Marque-modèle : SUNGROW SG50CX-P2 / Puissance nominale : 50 / Nombre : 2 »
    — la puissance n'y porte pas toujours d'unité, c'est l'absence de « Wc »
    qui le distingue du bloc Modules. Sert à la notice de sécurité ERP (§4.1
    et fiche technique de l'annexe 7.4, app/notices_erp.py).
    """
    for texte in morceaux:
        if "Marque-modèle" not in texte or "Wc" in texte:
            continue
        champs = {}
        # Sur la FVE de Bourges, « Puissance nominale » suit le modèle sur la
        # MÊME ligne : on s'arrête à ce libellé, sinon le modèle valait
        # « SUNGROW SG40CX-P2 Puissance nominale : 40 ».
        m = re.search(r"Marque-modèle\s*:\s*(.+?)\s*(?:Puissance\s+nominale|Nombre\s*:|[\n|]|$)",
                      texte)
        if m and m.group(1).strip():
            champs["modele_onduleur"] = m.group(1).strip()
        m = re.search(r"Nombre\s*:\s*([\d\s]+)", texte)
        if m:
            nombre = _entier(m.group(1).replace(" ", "").replace("\u202f", ""))
            if nombre:
                champs["nb_onduleurs"] = nombre
        return champs
    return {}


def lire_onduleur(chemin: Path) -> dict:
    """Seulement le bloc Onduleurs d'une FVE déjà déposée (projets montés
    avant le 28/10/2026, dont la lecture n'avait pas retenu l'onduleur)."""
    try:
        from pptx import Presentation

        return _onduleur(_texte_planches(Presentation(str(chemin))))
    except Exception:
        logger.warning("Lecture des onduleurs de la FVE impossible (%s)", Path(chemin).name,
                       exc_info=True)
        return {}


# « 1762 x 1134 », « 1762x1134 mm », « 1762 × 1134 »
_RE_DIMENSIONS = re.compile(r"(\d{3,5})\s*[x×*]\s*(\d{3,5})")


def surface_module_m2(module_dimensions: str | None) -> float:
    """Surface d'un module, en m².

    Les dimensions du PROJET priment quand elles sont lisibles : c'est la
    donnée réelle. À défaut, le module type du catalogue (config).
    """
    m = _RE_DIMENSIONS.search(module_dimensions or "")
    if m:
        largeur, hauteur = int(m.group(1)), int(m.group(2))
    else:
        largeur, hauteur = config.MODULE_DIMENSIONS_DEFAUT_MM
    return largeur * hauteur / 1_000_000


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
        champs.update(_onduleur(morceaux))

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

    Range aussi la lecture dans `projet.meta["fve"]`, comme la lecture du plan
    de masse le fait dans `meta["plan_lecture"]`. Sans cette trace, l'étape 3
    n'avait aucun moyen de dire d'où venaient les valeurs : elles apparaissaient
    dans les cases sans que personne sache qu'elles étaient à relire (demande du
    16/09/2026). Les images restent dehors : ce sont des octets, meta est du
    JSON.
    """
    proposes: list[str] = []
    correspondances = {"module_modele": "type_module"}
    champs = lecture.get("champs") or {}
    for cle, valeur in champs.items():
        attribut = correspondances.get(cle, cle)
        if not hasattr(projet.ombriere, attribut):
            continue
        if getattr(projet.ombriere, attribut) in (None, ""):
            setattr(projet.ombriere, attribut, valeur)
            proposes.append(attribut)

    # Emprise au sol créée (cadre 4.3 du Cerfa), estimée comme la surface
    # couverte de modules : nombre total x surface unitaire (demande du
    # 16/09/2026). C'est une ESTIMATION — l'emprise réglementaire est la
    # projection verticale du volume, débords de structure inclus, donc
    # légèrement supérieure à la seule surface des panneaux. Elle est proposée
    # comme les autres valeurs : surlignée, et modifiable.
    if (projet.ombriere.emprise_creee_m2 is None
            and projet.ombriere.nb_modules_total):
        surface = (projet.ombriere.nb_modules_total
                   * surface_module_m2(projet.ombriere.module_dimensions))
        projet.ombriere.emprise_creee_m2 = round(surface, 1)
        proposes.append("emprise_creee_m2")

    trace = projet.meta.get("fve") or {}
    trace.update({
        "champs": {correspondances.get(c, c): v for c, v in champs.items()},
        "proposes": proposes,
        "manquant": list(lecture.get("manquant") or []),
    })
    projet.meta["fve"] = trace
    return proposes
