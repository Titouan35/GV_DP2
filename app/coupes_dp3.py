"""Coupes types préchargées en DP3, choisies d'après la lecture de la FVE.

Le bureau d'études fournit toujours la même coupe pour une configuration
donnée : le nombre de modules du rampant et la hauteur de point bas suffisent
à la désigner. La FVE porte ces deux valeurs (« Nombre de panneaux en
largeur », « Point bas »), l'outil peut donc déposer la bonne coupe en DP3
sans que personne la cherche à la main (demande du 16/09/2026).

Trois garde-fous, hérités de la doctrine du projet :

1. Le point bas doit CORRESPONDRE. Les deux coupes du catalogue sont à 3,50 m.
   Un projet à 4,00 m recevrait un dessin faux — et un dessin faux dans une
   pièce cotée d'un dossier réglementaire ne se voit pas à la relecture. Hors
   correspondance, on ne dépose rien et on dit pourquoi.

2. On ne remplace JAMAIS une DP3 déjà déposée par le BE. La coupe type est un
   point de départ, pas une autorité.

3. La pièce est marquée PROVISOIRE (`origine: "catalogue"`). Elle porte le
   cartouche du dossier dont elle est issue, pas celui du projet en cours :
   l'assemblage l'étiquette et le dépôt la signale, comme la coupe
   paramétrique de secours (app/assemblage.py).
"""
from __future__ import annotations

from . import config

# Tolérance sur le point bas, en mètres. Une FVE écrit « 3,5 » et l'utilisateur
# peut saisir « 3.50 » : c'est la même cote. Au-delà, c'est une autre ombrière.
TOLERANCE_POINT_BAS_M = 0.01

# Clé : nombre de modules du rampant, tel que lu sur la FVE.
CATALOGUE_DP3 = {
    6: {
        "fichier": "coupe_occipark_6_modules_portrait_point_bas_3m50.pdf",
        "libelle": "Coupe type OCCIPARK · 6 modules portrait · point bas 3,50 m",
        "point_bas_m": 3.5,
        "rampant_m": 10.56,      # cote 10560 mm relevée sur le dessin
    },
    7: {
        "fichier": "coupe_classique_7_modules_portrait_point_bas_3m50.pdf",
        "libelle": "Coupe type classique · 7 modules portrait · point bas 3,50 m",
        "point_bas_m": 3.5,
        "rampant_m": 12.04,      # 12041,29 mm sur le rampant
        "hauteur_hors_tout_m": 5.19,   # 5185,42 mm
    },
}


def dossier() -> "config.Path":
    return config.REPO_ROOT / "app" / "gabarits" / "coupes_dp3"


def choisir(nb_modules: int | None, point_bas_m: float | None) -> tuple[dict | None, str | None]:
    """Coupe type correspondante, ou (None, raison du refus).

    La raison est rendue à l'utilisateur : un silence laisserait croire que la
    FVE n'a rien donné, alors que c'est la configuration qui n'est pas au
    catalogue.
    """
    if not nb_modules:
        return None, None          # la FVE ne dit rien : rien à signaler
    entree = CATALOGUE_DP3.get(int(nb_modules))
    if entree is None:
        connus = ", ".join(str(k) for k in sorted(CATALOGUE_DP3))
        return None, (f"Aucune coupe type pour {nb_modules} modules de rampant "
                      f"(catalogue : {connus}). Déposez la coupe du BE.")
    # la virgule décimale ne s'applique qu'aux NOMBRES : passer .replace(".", ",")
    # sur la phrase entière transformait aussi le point final en virgule
    cote = f"{entree['point_bas_m']:g}".replace(".", ",")
    if point_bas_m is None:
        return None, ("Coupe type non déposée : le point bas n'est pas connu, "
                      f"or la coupe {nb_modules} modules n'existe qu'en {cote} m.")
    if abs(float(point_bas_m) - entree["point_bas_m"]) > TOLERANCE_POINT_BAS_M:
        saisi = f"{point_bas_m:g}".replace(".", ",")
        return None, (f"Coupe type non déposée : point bas {saisi} m, or la coupe "
                      f"{nb_modules} modules est cotée {cote} m.")
    chemin = dossier() / entree["fichier"]
    if not chemin.exists():
        return None, f"Coupe type introuvable sur le disque ({entree['fichier']})."
    return {**entree, "chemin": chemin}, None
