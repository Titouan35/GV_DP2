"""Catalogue des structures types (coupes Solstyce START PLAINE, dossier ../COUPES).

Cotes relevées sur les coupes officielles (vue en coupe, mm) le 2026-07-13 :
- PLAINE Bas — Court   (éch. 1/40) : poteau côté HAUT, 3 472 mm au poteau,
  3 055 mm à l'extrémité basse.
- PLAINE Haut — Court  (éch. 1/50) : poteau côté BAS, 3 923 mm à l'extrémité
  haute, 3 512 mm à l'attache poteau.
- PLAINE Double — Court (éch. 1/40) : poteau CENTRAL, pente continue (T, pas
  de V inversé), 3 923 mm côté haut, 2 990 mm côté bas.

Pente ≈ 5° dans les trois cas. Un seul modèle paramétrique alimente DP3
(coupe), DP4 (façades/toiture) et le module Insertion IA (source unique).
"""
from __future__ import annotations

import math

# Libellés « Coupe » présentés à l'utilisateur ; la clé du catalogue reste
# l'identifiant interne (rétro-compatible avec les projets existants).
LIBELLE_COUPE: dict[str, str] = {
    "START PLAINE Bas": "Mono Bas",
    "START PLAINE Haut": "Mono Haut",
    "START PLAINE Double": "Double",
}


def libelle_coupe(famille: str | None) -> str:
    """Nom court de la coupe (Mono Bas / Mono Haut / Double)."""
    return LIBELLE_COUPE.get(famille or "", famille or "—")


# profondeur = dimension couverte dans le sens de la pente (une place ~5 m)
CATALOGUE: dict[str, dict] = {
    "START PLAINE Bas": {
        "poteau": "haut",          # côté du poteau par rapport à la pente
        "double": False,
        "profondeur_m": 5.0,
        "h_haut_m": 3.47,          # hauteur hors-tout au point haut
        "h_bas_m": 3.06,           # hauteur au point bas
        "pente_deg": 5.0,
        "coupe_pdf": "11 - BIBLIOTHEQUE START_1.5 (Fichier pour sortir plans de coupe commerciaux)-PLAINE - Bas - Court.pdf",
    },
    "START PLAINE Haut": {
        "poteau": "bas",
        "double": False,
        "profondeur_m": 5.0,
        "h_haut_m": 3.92,
        "h_bas_m": 3.51,
        "pente_deg": 5.0,
        "coupe_pdf": "11 - BIBLIOTHEQUE START_1.5 (Fichier pour sortir plans de coupe commerciaux)-PLAINE - Haut - Court.pdf",
    },
    "START PLAINE Double": {
        "poteau": "central",
        "double": True,
        "profondeur_m": 10.0,
        "h_haut_m": 3.92,
        "h_bas_m": 2.99,
        "pente_deg": 5.0,
        "coupe_pdf": "11 - BIBLIOTHEQUE START_1.5 (Fichier pour sortir plans de coupe commerciaux)-PLAINE - Double - Court.pdf",
    },
}


def parametres_effectifs(ombriere: dict) -> dict:
    """Fusionne le type du catalogue et les paramètres saisis par l'utilisateur.

    Les valeurs saisies (largeur, pente, hauteurs...) priment sur les valeurs
    par défaut du catalogue. La hauteur du point haut est recalculée depuis la
    pente si l'utilisateur fournit hauteur point bas + profondeur.
    """
    famille = ombriere.get("famille")
    base = dict(CATALOGUE.get(famille, CATALOGUE["START PLAINE Bas"]))
    base["famille"] = famille or "START PLAINE Bas"

    profondeur = ombriere.get("largeur_m") or base["profondeur_m"]
    pente = ombriere.get("pente_deg") or base["pente_deg"]
    h_bas = ombriere.get("garde_au_sol_m") or base["h_bas_m"]
    denivele = profondeur * math.tan(math.radians(pente))
    h_haut = ombriere.get("hauteur_hors_tout_m") or round(h_bas + denivele, 2)

    entraxe = ombriere.get("entraxe_m") or 5.0
    nb_travees = int(ombriere.get("nb_travees") or 4)
    longueur = ombriere.get("longueur_m") or round(nb_travees * entraxe, 2)

    return {
        **base,
        "profondeur_m": float(profondeur),
        "pente_deg": float(pente),
        "h_bas_m": float(h_bas),
        "h_haut_m": float(h_haut),
        "entraxe_m": float(entraxe),
        "nb_travees": nb_travees,
        "longueur_m": float(longueur),
    }
