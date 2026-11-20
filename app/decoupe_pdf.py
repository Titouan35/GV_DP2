"""Découpe du dossier PDF en une page par pièce, pour le dépôt en ligne.

Pourquoi (16/09/2026). Les portails d'urbanisme demandent les pièces UNE PAR
UNE : un fichier pour la DP1, un pour la DP2, etc. Le dossier assemblé est un
PDF unique, que le bureau d'études devait redécouper à la main dans Acrobat
avant chaque dépôt.

Le nom de chaque page vient du PPTX qui a servi à produire le PDF : son
en-tête donne le titre de la planche, son cartouche donne le badge de pièce
(« DP1 », « DP6 », « Notice »…). D'où des fichiers déposables tels quels :

    01_GARDE.pdf
    02_DP1_Plan-de-situation-et-cadastral.pdf
    05_DP3_Coupe-du-terrain-et-de-la-construction.pdf

Le PPTX est la source des libellés plutôt que le PDF : le texte y est
structuré, là où le PDF sorti de PowerPoint ne se relit qu'en devinant la mise
en page.
"""
from __future__ import annotations

import logging
import zipfile
from pathlib import Path

from pypdf import PdfReader, PdfWriter

from .nomenclature import _ascii_majuscules

logger = logging.getLogger(__name__)

# Repères verticaux sur une planche de 7,5 pouces de haut (1280 x 720 px) :
# l'en-tête est à 44 px du haut, le cartouche à 671 px. Ces deux bandes
# suffisent à identifier une planche sans instrumenter tout l'assemblage.
_BANDE_ENTETE_PO = 1.0
_BANDE_CARTOUCHE_PO = (6.7, 7.1)
_LONGUEUR_BADGE_MAX = 8


def libelles_planches(chemin_pptx: Path) -> list[str]:
    """Un libellé par planche du PPTX, dans l'ordre. Vide si illisible."""
    try:
        from pptx import Presentation
        from pptx.util import Emu
    except ImportError:                                   # pragma: no cover
        return []
    try:
        prs = Presentation(str(chemin_pptx))
    except (OSError, ValueError):
        logger.warning("Libellés de planches illisibles (%s)", chemin_pptx.name)
        return []

    libelles = []
    for planche in prs.slides:
        titre, badge = "", ""
        for forme in planche.shapes:
            if not forme.has_text_frame:
                continue
            texte = forme.text_frame.text.strip()
            if not texte:
                continue
            haut = Emu(forme.top or 0).inches
            if haut < _BANDE_ENTETE_PO and not titre and len(texte) < 80:
                titre = texte
            elif (_BANDE_CARTOUCHE_PO[0] < haut < _BANDE_CARTOUCHE_PO[1]
                    and not badge and len(texte) <= _LONGUEUR_BADGE_MAX):
                badge = texte
        # Le badge ne s'ajoute que s'il APPORTE quelque chose : « Notice » +
        # « Notice descriptive » donnait « NOTICE-NOTICE-DESCRIPTIVE ».
        if badge and badge.lower() in titre.lower():
            badge = ""
        libelles.append(" ".join(filter(None, [badge, titre])).strip())
    return libelles


def _nom_page(numero: int, libelle: str) -> str:
    """« 2, "DP1 Plan de situation" » -> « 02_DP1-PLAN-DE-SITUATION.pdf ».

    La première planche est la page de garde : ni en-tête ni badge, d'où un
    libellé vide qu'il faut nommer explicitement.
    """
    propre = _ascii_majuscules(libelle)
    if not propre:
        propre = "PAGE-DE-GARDE" if numero == 1 else "PLANCHE"
    return f"{numero:02d}_{propre}.pdf"


def decouper(chemin_pdf: Path, chemin_pptx: Path | None, sortie: Path) -> list[Path]:
    """Écrit une page par PDF dans `sortie`. Renvoie les fichiers produits.

    Les libellés viennent du PPTX quand il est là et que ses planches
    correspondent une à une aux pages. Sinon on numérote : mieux vaut des noms
    pauvres que des noms FAUX sur des pièces qu'on va déposer en mairie.
    """
    lecteur = PdfReader(str(chemin_pdf))
    libelles = libelles_planches(chemin_pptx) if chemin_pptx and chemin_pptx.exists() else []
    if libelles and len(libelles) != len(lecteur.pages):
        logger.warning("%d planches PPTX pour %d pages PDF : libellés ignorés.",
                       len(libelles), len(lecteur.pages))
        libelles = []

    sortie.mkdir(parents=True, exist_ok=True)
    for ancien in sortie.glob("*.pdf"):
        ancien.unlink(missing_ok=True)      # jamais de page d'un export précédent

    produits = []
    for i, page in enumerate(lecteur.pages):
        ecrivain = PdfWriter()
        ecrivain.add_page(page)
        chemin = sortie / _nom_page(i + 1, libelles[i] if libelles else "")
        with open(chemin, "wb") as f:
            ecrivain.write(f)
        produits.append(chemin)
    return produits


def archiver(pages: list[Path], chemin_zip: Path) -> Path:
    """Regroupe les pages dans une archive : un navigateur ne télécharge pas
    quinze fichiers d'un coup, et le BE veut les garder ensemble."""
    chemin_zip.parent.mkdir(parents=True, exist_ok=True)
    tmp = chemin_zip.with_suffix(".zip.tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
        for page in pages:
            zf.write(page, arcname=page.name)   # à plat : pas de sous-dossier
    tmp.replace(chemin_zip)                     # atomique
    return chemin_zip
