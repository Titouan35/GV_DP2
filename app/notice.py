"""Notice descriptive DP11 : notice type en 7 sections, pré-remplie.

Les paragraphes sont modelés sur une vraie notice de dossier (Soufflenheim),
rendus génériques : les informations qui varient d'un projet à l'autre sont
des balises {{variable}} remplies depuis la saisie, les APIs (GPU, Géorisques)
et le modèle paramétrique. Aucune donnée inventée (plan §13). Le brouillon est
éditable et doit être relu et validé humainement avant export.

`generer_sections` renvoie le texte rempli (stockage + export). `variables`
renvoie la liste des valeurs concrètes du projet, que l'UI surligne pour la
relecture.
"""
from __future__ import annotations

import re

from .catalogue import libelle_coupe, parametres_effectifs

SECTIONS = [
    ("presentation", "1. Présentation du projet et du demandeur"),
    ("etat_initial", "2. État initial du terrain et de ses abords"),
    ("description", "3. Description du projet"),
    ("insertion", "4. Insertion paysagère et aspect extérieur"),
    ("reglementaire", "5. Contexte réglementaire, servitudes et risques"),
    ("acces_reseaux", "6. Accès, réseaux et raccordement"),
    ("chantier", "7. Chantier et remise en état"),
]

# Notice type (balises {{variable}}), synthétique, sur la trame Soufflenheim.
TEMPLATES: dict[str, str] = {
    "presentation": (
        "Le présent dossier de déclaration préalable est déposé par {{raison_sociale}}, "
        "représentée par {{representant}} (SIRET {{siret}}). Il porte sur l'installation "
        "d'ombrières photovoltaïques sur le parc de stationnement existant situé {{adresse}}, "
        "à {{commune}} ({{code_postal}}). Le projet s'inscrit dans le cadre de la loi APER "
        "(loi n° 2023-175 du 10 mars 2023) et n'altère pas l'usage de stationnement du site."
    ),
    "etat_initial": (
        "Le terrain d'assiette correspond aux parcelles {{parcelles}}, sur la commune de "
        "{{commune}} (INSEE {{code_insee}}), pour une superficie de {{surface}}. Il s'agit "
        "d'une aire de stationnement existante, déjà aménagée et imperméabilisée, dont l'usage "
        "et les accès sont conservés en l'état."
    ),
    "description": (
        "Le projet consiste à installer une ombrière photovoltaïque de coupe {{coupe}}, de "
        "{{longueur}} sur {{largeur}}, en structure métallique galvanisée : {{nb_travees}} "
        "travées d'entraxe {{entraxe}}, {{toiture}} à {{pente}}, hauteur maximale {{hauteur_max}} "
        "et point bas {{hauteur_bas}}. Elle reçoit des modules photovoltaïques full black"
        "{{modules_detail}} pour une puissance de {{puissance_kwc}}, et couvre {{nb_places}} "
        "places qui restent utilisables. Aucun terrassement significatif n'est nécessaire, les "
        "fondations se limitant aux massifs des poteaux."
    ),
    "insertion": (
        "L'ombrière présente une volumétrie simple et régulière, de teinte neutre, avec des "
        "modules sombres non réfléchissants qui limitent l'impact visuel. Le document graphique "
        "d'insertion (pièce DP6) illustre le projet avant et après travaux depuis l'espace public."
    ),
    "reglementaire": (
        "Au document d'urbanisme, le terrain relève du {{zonage}}. {{abf}} {{risques}} Compte "
        "tenu de ses caractéristiques, le projet relève de la déclaration préalable (article "
        "R.421-9 du code de l'urbanisme)."
    ),
    "acces_reseaux": (
        "Les accès et circulations du parking sont conservés. L'électricité produite est injectée "
        "sur le réseau public de distribution (Enedis) via un point de livraison à proximité, les "
        "câbles cheminant sous les ombrières puis en souterrain. La demande de raccordement sera "
        "déposée en parallèle de l'instruction."
    ),
    "chantier": (
        "Les travaux (massifs de fondation, montage de la charpente, pose des modules, câblage) "
        "sont réalisés par zones pour maintenir l'exploitation du parking. À l'issue du chantier, "
        "les emprises sont nettoyées et le marquage au sol rétabli. La durée prévisionnelle est "
        "de quelques semaines."
    ),
}

# Ordre des balises à surligner : les plus longues d'abord (évite les recouvrements).
CLES_SURLIGNE = [
    "raison_sociale", "representant", "adresse", "parcelles", "zonage",
    "surface", "coupe", "longueur", "largeur", "entraxe", "pente",
    "hauteur_max", "hauteur_bas", "puissance_kwc", "module_puissance",
    "module_dimensions", "nb_travees", "nb_places", "commune",
    "code_postal", "code_insee", "siret",
]


def _valeurs(projet: dict) -> dict[str, str]:
    mo = projet.get("mo") or {}
    loc = projet.get("localisation") or {}
    urb = projet.get("urbanisme") or {}
    omb = projet.get("ombriere") or {}
    parcelles = loc.get("parcelles") or []
    surface = sum(p.get("contenance_m2") or 0 for p in parcelles) or None
    refs = ", ".join(f"{p.get('section')} {p.get('numero')}" for p in parcelles) or "—"
    p = parametres_effectifs(omb) if (omb.get("famille") or omb.get("puissance_kwc")) else None

    zones = ((urb.get("zonage") or {}).get("zones")) or []
    zonage = (
        f"zonage {zones[0].get('libelle')} ({zones[0].get('libelong') or 'PLU'})"
        if zones else
        "document d'urbanisme non couvert par le GPU à la date de constitution du dossier"
    )
    risques = ((urb.get("risques") or {}).get("risques")) or []
    risques_txt = (
        "Des risques sont recensés sur la commune (Géorisques) : "
        + ", ".join(r.get("libelle", "") for r in risques) + "."
        if risques else
        "Aucun risque majeur n'est recensé sur la commune dans Géorisques à la date de "
        "constitution du dossier."
    )
    abf_txt = (
        "Le terrain est situé en secteur protégé au titre des abords des monuments historiques "
        "ou d'un site, l'avis de l'architecte des Bâtiments de France est requis."
        if urb.get("secteur_abf") else
        "Le terrain n'est situé dans aucun périmètre de protection patrimoniale identifié "
        "(abords de monument historique, site, SPR)."
    )
    double = bool(p and p.get("double"))
    toiture = ("toiture à double pente (structure en T)" if double else "toiture monopente")
    md = ", ".join(filter(None, [
        f"{omb['module_puissance_wc']:g} Wc" if omb.get("module_puissance_wc") else None,
        omb.get("module_dimensions"),
    ]))
    modules_detail = f" de {md}" if md else ""

    def num(v, suf="", dec=False):
        if v in (None, ""):
            return "—"
        s = (f"{v:.2f}" if dec else f"{v:g}").replace(".", ",")
        return s + suf

    return {
        "raison_sociale": mo.get("raison_sociale") or "le maître d'ouvrage",
        "representant": mo.get("representant") or "son représentant",
        "siret": mo.get("siret") or "—",
        "adresse": loc.get("adresse") or "—",
        "commune": loc.get("commune") or "—",
        "code_postal": loc.get("code_postal") or "—",
        "code_insee": loc.get("code_insee") or "—",
        "parcelles": refs,
        "surface": f"{surface:,} m²".replace(",", " ") if surface else "—",
        "coupe": libelle_coupe(omb.get("famille")) if omb.get("famille") else "—",
        "toiture": toiture,
        "modules_detail": modules_detail,
        "longueur": num(p["longueur_m"], " m") if p else "—",
        "largeur": num(omb.get("largeur_m") or (p["profondeur_m"] if p else None), " m"),
        "nb_travees": str(p["nb_travees"]) if p else "—",
        "entraxe": num(p["entraxe_m"], " m") if p else "—",
        "pente": num(p["pente_deg"], "°") if p else "—",
        "hauteur_max": num(p["h_haut_m"], " m", dec=True) if p else "—",
        "hauteur_bas": num(p["h_bas_m"], " m", dec=True) if p else "—",
        "puissance_kwc": num(omb.get("puissance_kwc"), " kWc"),
        "nb_places": str(omb["nb_places"]) if omb.get("nb_places") else "—",
        "module_puissance": num(omb.get("module_puissance_wc"), " Wc"),
        "module_dimensions": (omb.get("module_dimensions") or "—"),
        "zonage": zonage,
        "abf": abf_txt,
        "risques": risques_txt,
    }


def _remplir(tpl: str, vals: dict[str, str]) -> str:
    return re.sub(r"\{\{(\w+)\}\}", lambda m: vals.get(m.group(1), m.group(0)), tpl)


def generer_sections(projet: dict) -> dict[str, str]:
    """Notice type remplie depuis les données du projet (texte propre, exportable)."""
    vals = _valeurs(projet)
    return {cle: _remplir(tpl, vals) for cle, tpl in TEMPLATES.items()}


def variables(projet: dict) -> list[str]:
    """Valeurs concrètes du projet à surligner dans l'UI (les plus longues d'abord)."""
    vals = _valeurs(projet)
    vus: list[str] = []
    for cle in CLES_SURLIGNE:
        v = (vals.get(cle) or "").strip()
        if v and v != "—" and len(v) >= 2 and v not in vus:
            vus.append(v)
    vus.sort(key=len, reverse=True)
    return vus
