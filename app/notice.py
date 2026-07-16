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

# Notice type (balises {{variable}}), modelée sur le dossier Soufflenheim.
TEMPLATES: dict[str, str] = {
    "presentation": (
        "Le présent dossier de déclaration préalable est déposé par {{raison_sociale}}, "
        "représentée par {{representant}} (SIRET {{siret}}). Il porte sur l'installation "
        "d'ombrières photovoltaïques sur le parc de stationnement existant situé {{adresse}}, "
        "sur la commune de {{commune}} ({{code_postal}}). L'opération a pour objet la "
        "production d'électricité d'origine renouvelable tout en assurant l'ombrage et la "
        "protection des véhicules stationnés contre les intempéries. Elle s'inscrit dans le "
        "cadre de l'obligation d'équipement des parcs de stationnement extérieurs prévue par "
        "la loi n° 2023-175 du 10 mars 2023 (loi APER, article L.171-4 du code de la "
        "construction et de l'habitation). Le maître d'ouvrage assurera l'exploitation de la "
        "centrale sur toute sa durée de vie, l'usage de stationnement du site demeurant inchangé."
    ),
    "etat_initial": (
        "Le terrain d'assiette du projet correspond aux parcelles cadastrales {{parcelles}}, "
        "sur le territoire de la commune de {{commune}} (code INSEE {{code_insee}}), pour une "
        "superficie totale de {{surface}}. Il s'agit d'une aire de stationnement déjà aménagée "
        "et imperméabilisée, dont l'usage et la desserte sont conservés en l'état. Le relief du "
        "site est faiblement marqué, ce qui permet une implantation des ombrières sans "
        "modification sensible de la topographie. Les abords immédiats conservent leur "
        "configuration actuelle : voies de circulation, cheminements piétons, espaces verts et "
        "clôtures existantes. L'accès au site s'effectue depuis la voirie publique attenante, "
        "sans création de nouvel accès."
    ),
    "description": (
        "Le projet consiste en la construction d'une ombrière photovoltaïque de coupe "
        "{{coupe}}, de {{longueur}} de longueur et {{largeur}} de largeur, portée par une "
        "structure métallique galvanisée. La couverture est constituée de {{nb_travees}} "
        "travées d'un entraxe de {{entraxe}} et d'une {{toiture}} inclinée à {{pente}}, dont le "
        "point haut culmine à {{hauteur_max}} et le point bas s'établit à {{hauteur_bas}} "
        "au-dessus du sol fini. La couverture est assurée par des modules photovoltaïques full "
        "black d'une puissance unitaire de {{module_puissance}} et de dimensions "
        "{{module_dimensions}}, pour une puissance totale installée de {{puissance_kwc}}. Aucun "
        "terrassement significatif n'est nécessaire, le parc de stationnement étant déjà "
        "réalisé, et les fondations se limitent aux massifs des poteaux support. Les eaux "
        "pluviales interceptées par la couverture sont collectées par gouttières et descentes, "
        "puis dirigées vers le dispositif de gestion des eaux pluviales existant du parking. Le "
        "nombre de places de stationnement demeure inchangé, soit {{nb_places}} places, qui "
        "restent couvertes et utilisables après travaux."
    ),
    "insertion": (
        "L'ombrière présente une volumétrie simple et régulière, d'une hauteur comparable aux "
        "équipements techniques usuels d'un parc de stationnement. La structure métallique "
        "reçoit une teinte neutre et les modules, de teinte sombre et non réfléchissante, "
        "limitent les reflets et l'impact visuel depuis les espaces environnants. La végétation "
        "existante en périphérie du site participe à l'insertion de l'ouvrage et atténue sa "
        "perception depuis les abords habités, notamment les habitations les plus proches. Le "
        "document graphique d'insertion paysagère joint au dossier illustre la perception du "
        "projet avant et après travaux depuis l'espace public. L'aspect extérieur de "
        "l'installation, sobre et homogène, garantit une intégration cohérente avec le caractère "
        "du site et de son environnement."
    ),
    "reglementaire": (
        "Au regard du document d'urbanisme en vigueur, le terrain relève du {{zonage}}, dont le "
        "règlement admet le projet dans les conditions rappelées au dossier. {{abf}} {{risques}} "
        "Le maître d'ouvrage veillera au respect des servitudes d'utilité publique éventuellement "
        "applicables au site. Compte tenu de sa nature et de ses caractéristiques, le projet "
        "relève du régime de la déclaration préalable au titre de l'article R.421-9 du code de "
        "l'urbanisme, tel que modifié pour les ombrières photovoltaïques de parc de stationnement."
    ),
    "acces_reseaux": (
        "Les accès au parc de stationnement et les circulations intérieures sont conservés à "
        "l'identique, sans création de voirie nouvelle. L'électricité produite par la centrale "
        "est injectée sur le réseau public de distribution géré par Enedis, via un point de "
        "livraison implanté à proximité de la limite de propriété. L'emplacement du point de "
        "livraison figurant sur les pièces graphiques n'est donné qu'à titre indicatif, son "
        "positionnement définitif ainsi que celui d'un éventuel transformateur relevant de "
        "l'appréciation finale du gestionnaire de réseau. Les câbles cheminent en partie haute "
        "sous la structure des ombrières, puis en tranchée souterraine jusqu'au point de "
        "livraison, sans incidence sur les autres réseaux. La demande de raccordement sera "
        "déposée auprès du gestionnaire de réseau parallèlement à l'instruction du présent "
        "dossier. La défense extérieure contre l'incendie du site n'est pas modifiée par le projet."
    ),
    "chantier": (
        "Les travaux comprennent la réalisation des massifs de fondation, le montage de la "
        "charpente métallique, la pose des modules photovoltaïques et le câblage électrique de "
        "l'installation. Le chantier est organisé par zones successives afin de maintenir autant "
        "que possible l'exploitation du parc de stationnement pendant la durée des travaux. Aucun "
        "terrassement significatif n'est prévu, le sol du parking étant déjà aménagé et "
        "imperméabilisé. Les emprises de chantier et les installations provisoires sont limitées "
        "au strict nécessaire et signalées pour la sécurité des usagers. À l'issue des travaux, "
        "les zones concernées sont nettoyées et remises en état, y compris le marquage au sol des "
        "places de stationnement. La durée prévisionnelle du chantier est de quelques semaines à "
        "quelques mois selon la surface du parc et la puissance installée."
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
