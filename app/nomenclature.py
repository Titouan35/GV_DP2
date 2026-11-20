"""Noms des fichiers TÉLÉCHARGÉS par le bureau d'études.

Pourquoi ce module (16/09/2026). Le Cerfa sortait sous le nom
`cerfa_16702_prerempli.pdf` — le MÊME pour tous les projets. Deux dossiers
téléchargés d'affilée donnaient `cerfa_16702_prerempli.pdf` et
`cerfa_16702_prerempli(1).pdf`, impossibles à distinguer sans les ouvrir. Le
dossier PPTX, lui, sortait sous `Dossier_DP_carrefour-market-63600-ambert-1ceaeb.pptx`,
c'est-à-dire l'identifiant interne, hachage aléatoire compris.

La règle retenue : MOT-CLÉ puis VILLE.

    CERFA_16702-03_AMBERT.pdf
    DOSSIER_DP_AMBERT.pptx
    DOSSIER_DP_AMBERT.pdf

Le mot-clé d'abord : dans un dossier de téléchargements où se mélangent les
projets, le tri alphabétique regroupe alors tous les Cerfa, puis tous les
dossiers, chacun classé par commune.

Ce module ne touche QUE le nom présenté au téléchargement. Les fichiers gardent
sur le disque leur nom technique stable, dont dépendent le cache de
régénération, les écritures atomiques et les motifs de purge
(`routes_projets._MOTIFS_CACHE`).
"""
from __future__ import annotations

import re
import unicodedata

# Le numéro officiel s'écrit « 16702*03 ». L'astérisque est interdit dans un
# nom de fichier Windows : le tiret le remplace.
CERFA_DANS_LE_NOM = "16702-03"


def _ascii_majuscules(texte: str) -> str:
    """« Montréal-la-Cluse » -> « MONTREAL-LA-CLUSE ».

    Sans accent ni espace : un nom de fichier voyage par courriel, par clé USB
    et par SharePoint, et les accents y survivent mal.
    """
    sans_accent = unicodedata.normalize("NFKD", texte or "")
    sans_accent = "".join(c for c in sans_accent if not unicodedata.combining(c))
    sans_accent = sans_accent.encode("ascii", "ignore").decode()
    propre = re.sub(r"[^A-Za-z0-9]+", "-", sans_accent).strip("-")
    return propre.upper()[:60]


def ville(projet: dict) -> str:
    """Commune du terrain, pour le nom de fichier.

    Repli sur le nom du projet puis sur son identifiant : un nom de fichier
    doit toujours sortir, même sur un projet dont la localisation n'est pas
    encore faite.
    """
    loc = projet.get("localisation") or {}
    for candidat in (loc.get("commune"), projet.get("nom"), projet.get("id")):
        propre = _ascii_majuscules(candidat or "")
        if propre:
            return propre
    return "PROJET"


def cerfa(projet: dict) -> str:
    return f"CERFA_{CERFA_DANS_LE_NOM}_{ville(projet)}.pdf"


def cerfa_erp(projet: dict) -> str:
    """Autorisation de travaux ERP, Cerfa 13824*04 (app/cerfa_erp.py)."""
    return f"CERFA_13824-04_{ville(projet)}.pdf"


def notice_erp(projet: dict, quoi: str) -> str:
    """Notices ERP en .docx (app/notices_erp.py) : quoi = accessibilite | securite."""
    return f"NOTICE_{quoi.upper()}_{ville(projet)}.docx"


def dossier(projet: dict, extension: str = "pptx") -> str:
    return f"DOSSIER_DP_{ville(projet)}.{extension.lstrip('.')}"


def pages_separees(projet: dict) -> str:
    """Archive des pages découpées, une par pièce (app/decoupe_pdf.py)."""
    return f"PAGES_DP_{ville(projet)}.zip"
