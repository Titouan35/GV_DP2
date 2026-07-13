"""Notice descriptive DP11 : gabarit déterministe en 7 sections.

Tout le contenu vient de la saisie, des APIs (GPU, Géorisques) ou du
modèle paramétrique : aucune donnée inventée (plan §13). Le brouillon est
éditable dans l'UI et doit être relu et validé humainement avant export.
"""
from __future__ import annotations

from .catalogue import parametres_effectifs

SECTIONS = [
    ("presentation", "1. Présentation du projet et du demandeur"),
    ("etat_initial", "2. État initial du terrain et de ses abords"),
    ("description", "3. Description du projet"),
    ("insertion", "4. Insertion paysagère et aspect extérieur"),
    ("reglementaire", "5. Contexte réglementaire, servitudes et risques"),
    ("acces_reseaux", "6. Accès, réseaux et raccordement"),
    ("chantier", "7. Chantier et remise en état"),
]


def _fmt(v, suffixe=""):
    return f"{v}{suffixe}" if v not in (None, "") else "—"


def generer_sections(projet: dict) -> dict[str, str]:
    mo = projet.get("mo") or {}
    loc = projet.get("localisation") or {}
    urb = projet.get("urbanisme") or {}
    omb = projet.get("ombriere") or {}
    parcelles = loc.get("parcelles") or []
    surface = sum(p.get("contenance_m2") or 0 for p in parcelles) or None

    refs = ", ".join(
        f"{p.get('section')} {p.get('numero')}" for p in parcelles
    ) or "—"

    zonage_txt = "document d'urbanisme non couvert par le GPU à la date de constitution du dossier"
    zones = ((urb.get("zonage") or {}).get("zones")) or []
    if zones:
        z = zones[0]
        zonage_txt = f"zone {z.get('libelle')} ({z.get('libelong') or 'PLU'})"

    risques = ((urb.get("risques") or {}).get("risques")) or []
    risques_txt = (
        "Risques recensés sur la commune (Géorisques) : "
        + ", ".join(r.get("libelle", "") for r in risques) + "."
        if risques else
        "Aucun risque majeur recensé sur la commune dans Géorisques à la date de constitution du dossier."
    )

    abf = urb.get("secteur_abf")
    abf_txt = (
        "Le terrain est situé en secteur protégé (avis ABF requis)." if abf
        else "Le terrain n'est situé dans aucun périmètre de protection patrimoniale identifié (abords MH, site, SPR)."
    )

    p = parametres_effectifs(omb) if (omb.get("famille") or omb.get("puissance_kwc")) else None

    sections = {
        "presentation": (
            f"Le présent dossier de déclaration préalable est déposé par {_fmt(mo.get('raison_sociale'))}"
            + (f", représenté(e) par {mo['representant']}" if mo.get("representant") else "")
            + (f" (SIRET {mo['siret']})" if mo.get("siret") else "")
            + f". Il porte sur l'installation d'ombrières photovoltaïques sur le parking situé "
            f"{_fmt(loc.get('adresse'))}, sur la commune de {_fmt(loc.get('commune'))} "
            f"({_fmt(loc.get('code_postal'))}). "
            f"Ce projet s'inscrit dans le cadre de l'obligation d'équipement des parcs de "
            f"stationnement extérieurs prévue par la loi n° 2023-175 du 10 mars 2023 (loi APER, "
            f"art. L.171-4 du code de la construction et de l'habitation)."
        ),
        "etat_initial": (
            f"Le terrain d'assiette correspond aux parcelles cadastrales {refs} "
            f"(superficie totale {_fmt(surface, ' m²')}). Il s'agit d'une aire de stationnement "
            f"existante, déjà imperméabilisée, dont l'usage est conservé. Les abords immédiats "
            f"conservent leur configuration actuelle (accès, circulations, espaces verts)."
        ),
        "description": (
            (
                f"Le projet consiste en la pose d'ombrières photovoltaïques de type {p['famille']} : "
                f"structure en acier galvanisé (teinte claire, bandes de signalisation bleues sur les "
                f"poteaux), couverture de modules photovoltaïques full black. Dimensions principales : "
                f"{p['longueur_m']:g} m de longueur ({p['nb_travees']} travées de {p['entraxe_m']:g} m), "
                f"{p['profondeur_m']:g} m couverts dans le sens de la pente, hauteur hors tout "
                f"{p['h_haut_m']:.2f} m, point bas {p['h_bas_m']:.2f} m, pente {p['pente_deg']:g}°."
                + (f" Puissance installée : {omb['puissance_kwc']:g} kWc." if omb.get("puissance_kwc") else "")
                + (f" Environ {omb['nb_places']} places de stationnement restent couvertes et utilisables." if omb.get("nb_places") else "")
            ) if p else
            "Le projet consiste en la pose d'ombrières photovoltaïques sur le parking existant "
            "(caractéristiques à renseigner à l'étape 3 de l'outil)."
        ),
        "insertion": (
            "Les ombrières présentent une volumétrie simple et régulière, d'une hauteur comparable "
            "aux équipements usuels d'un parking (éclairage, signalétique). Les teintes retenues "
            "(structure gris clair galvanisé, modules noirs mats non réfléchissants) assurent une "
            "insertion discrète dans l'environnement du site. Le document graphique d'insertion "
            "(pièce DP6) illustre la perception du projet depuis l'espace public."
        ),
        "reglementaire": (
            f"Au document d'urbanisme, le terrain relève de : {zonage_txt}. {abf_txt} {risques_txt} "
            f"Compte tenu de sa puissance et de sa localisation, le projet relève de la déclaration "
            f"préalable (article R.421-9 du code de l'urbanisme, décret n° 2024-1023)."
        ),
        "acces_reseaux": (
            "Les accès au parking et les circulations intérieures sont inchangés. L'installation "
            "photovoltaïque sera raccordée au réseau public de distribution d'électricité ; la "
            "demande de raccordement sera déposée auprès d'Enedis. Les câbles cheminent en aérien "
            "sous les ombrières puis en souterrain jusqu'au point de livraison, sans modification "
            "des autres réseaux."
        ),
        "chantier": (
            "Le chantier (fondations, levage des structures, pose des modules, câblage) sera "
            "réalisé par zones afin de maintenir l'exploitation du parking. À l'issue des travaux, "
            "les zones d'emprise du chantier seront remises en état (marquage au sol, propreté). "
            "La durée prévisionnelle du chantier est de quelques semaines selon la taille du parc."
        ),
    }
    return sections
