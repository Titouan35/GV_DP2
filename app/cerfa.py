"""Pré-remplissage du Cerfa de Déclaration Préalable.

ATTENTION réglementaire (constaté le 13/07/2026) : le formulaire DP n'est
plus le 13404 mais le **Cerfa n° 16702*03** (renumérotation des formulaires
d'urbanisme ; source : entreprendre.service-public.gouv.fr, fiche R2028).
Le gabarit officiel est versionné dans app/gabarits/cerfa_16702.pdf
(AcroForm, 20 pages, 386 champs).

Le pré-remplissage reste un BROUILLON : relecture obligatoire par le BE
avant dépôt (aucune case à risque n'est cochée automatiquement).
"""
from __future__ import annotations

from datetime import date

from pypdf import PdfReader, PdfWriter

from . import config
from .catalogue import parametres_effectifs

GABARIT = config.REPO_ROOT / "app" / "gabarits" / "cerfa_16702.pdf"
NUMERO_CERFA = "16702*03"

COCHE = "/Oui"  # état « coché » des cases du gabarit


def _champs_demandeur(projet: dict) -> dict:
    mo = projet.get("mo") or {}
    champs: dict[str, str] = {}
    if (mo.get("type") or "").lower() == "particulier":
        champs["D1N_nom"] = mo.get("raison_sociale") or mo.get("representant") or ""
    else:
        champs["D2D_denomination"] = mo.get("raison_sociale") or ""
        champs["D2R_raison"] = mo.get("raison_sociale") or ""
        champs["D2S_siret"] = (mo.get("siret") or "").replace(" ", "")
        champs["D2J_type"] = mo.get("type") or ""
        champs["D2N_nom"] = mo.get("representant") or ""
    # adresse du demandeur : non structurée chez nous → tout dans « voie »
    champs["D3V_voie"] = mo.get("adresse") or ""
    champs["D3T_telephone"] = mo.get("telephone") or ""
    if mo.get("email"):
        champs["D5GE1_email"] = mo["email"]
        # D5A_acceptation (accepter les actes par voie électronique) N'EST PLUS
        # cochée : c'est un consentement qui engage juridiquement le déclarant.
        # Le module promettait dans son en-tête de ne cocher aucune case à
        # risque, et cochait celle-là. Elle reste à la main du déclarant.
    return champs


def _champs_terrain(projet: dict) -> dict:
    loc = projet.get("localisation") or {}
    champs = {
        "T2V_voie": loc.get("adresse") or "",
        "T2L_localite": loc.get("commune") or "",
        "T2C_code": loc.get("code_postal") or "",
    }
    parcelles = (loc.get("parcelles") or [])[:3]  # le gabarit n'a que 3 lignes
    suffixes = ["", "P2", "P3"]
    for parc, sfx in zip(parcelles, suffixes):
        champs[f"T2F{sfx}_prefixe"] = parc.get("com_abs") or "000"
        champs[f"T2S{sfx}_section"] = parc.get("section") or ""
        champs[f"T2N{sfx}_numero"] = parc.get("numero") or ""
        if parc.get("contenance_m2"):
            champs[f"T2T{sfx}_superficie"] = str(parc["contenance_m2"])
    return champs


def _champs_projet(projet: dict) -> tuple[dict, list[str]]:
    """Cadre « projet » du formulaire. Renvoie (champs, trous signalés).

    Cartographie relevée sur le gabarit officiel (page 7), qui donne le sens
    exact de chaque case et corrige une inversion présente jusqu'au 01/09/2026 :

        C2ZP1_crete        « Indiquez sa puissance crête : ___ kW »
        C2ZR1_destination  « et la destination principale de l'énergie »
        C2ZE1_puissance    « la puissance électrique nécessaire à votre projet »

    Le code écrivait la hauteur hors tout dans la puissance crête, et la
    puissance crête dans la puissance de raccordement.
    """
    omb = projet.get("ombriere") or {}
    trous: list[str] = []
    if not omb.get("famille") and not omb.get("puissance_kwc"):
        return {}, ["À compléter : aucune caractéristique d'ombrière n'est saisie."]

    desc = "Installation d'ombrières photovoltaïques sur le parking existant"

    # Les cotes du catalogue ne sont affirmées QUE si un type a été choisi.
    # Sans famille, catalogue.py retombe silencieusement sur START PLAINE Bas :
    # ses cotes n'ont rien à faire dans un formulaire officiel.
    if omb.get("famille"):
        p = parametres_effectifs(omb)
        dims = (f"{p['longueur_m']:g} m x {p['profondeur_m']:g} m, ".replace(".", ",")
                if p["saisis"]["longueur"] else
                f"{p['profondeur_m']:g} m de profondeur, ".replace(".", ","))
        desc += (
            f" : structure {p['famille']} en acier galvanisé, "
            f"{dims}"
            f"hauteur hors tout {p['h_haut_m']:.2f} m".replace(".", ",")
            + f", pente {p['pente_deg']:g} degrés".replace(".", ",")
        )
    else:
        trous.append(
            "À compléter : aucun type d'ombrière n'est choisi (étape 3). "
            "Les dimensions et la hauteur ne sont donc pas portées au formulaire."
        )

    desc += ", modules photovoltaïques full black"
    if omb.get("module_puissance_wc"):
        desc += f" de {omb['module_puissance_wc']:g} Wc".replace(".", ",")
    if omb.get("puissance_kwc"):
        desc += f", puissance {omb['puissance_kwc']:g} kWc".replace(".", ",")
    if omb.get("nb_places"):
        desc += f", {omb['nb_places']} places couvertes"
    desc += "."
    # La conformité à la loi APER est une CONCLUSION JURIDIQUE, pas un fait
    # mesuré. Elle était affirmée en dur sans le moindre test. Elle appartient
    # au déclarant, qui signe : l'outil ne la met plus dans sa bouche.

    champs = {
        "C2ZD1_description": desc,
        "C2ZA1_nouvelle": COCHE,  # une ombrière est une construction nouvelle
    }

    # puissance crête, en kW, dans la case qui la demande. Virgule décimale :
    # le formulaire est français, et le reste du descriptif l'emploie déjà.
    if omb.get("puissance_kwc"):
        champs["C2ZP1_crete"] = f"{omb['puissance_kwc']:g}".replace(".", ",")
    else:
        trous.append("À compléter : la puissance crête (kW) n'est pas renseignée.")

    # La puissance électrique nécessaire au raccordement et la destination de
    # l'énergie produite ne sont pas des données de l'outil. On les laisse
    # vides plutôt que d'y recopier une valeur voisine.
    trous.append(
        "À compléter à la main : puissance électrique nécessaire au "
        "raccordement (cadre 4.2.1) — l'outil ne la connaît pas."
    )
    trous.append(
        "À compléter à la main : destination principale de l'énergie produite "
        "(vente totale, autoconsommation...) — l'outil ne la connaît pas."
    )

    # stationnement inchangé avant / après travaux
    if omb.get("nb_places"):
        champs["S1A_stationnementavant"] = str(omb["nb_places"])
        champs["S1M_stationnementapres"] = str(omb["nb_places"])
    return champs, trous


def _champs_engagement(projet: dict) -> dict:
    """Cadre d'engagement du déclarant : lieu (commune) + date du jour."""
    loc = projet.get("localisation") or {}
    return {
        "E1L_lieu": loc.get("commune") or "",
        # champ « peigne » de 8 cases (MaxLen=8, drapeau comb) : les séparateurs
        # sont déjà imprimés. « 01/09/2026 » y entrait sur 10 caractères, ce qui
        # doublait les séparateurs et tronquait l'année.
        "E1D_date": date.today().strftime("%d%m%Y"),
    }


def preremplir(projet: dict):
    """Remplit le gabarit et l'écrit dans les assets du projet.

    Renvoie (chemin, champs remplis, avertissements). Refuse un projet en
    régime PC : le 16702 est le formulaire de la DÉCLARATION PRÉALABLE, le
    générer pour un PC produirait un dossier erroné.
    """
    if not GABARIT.exists():
        raise FileNotFoundError(
            "Gabarit Cerfa absent (app/gabarits/cerfa_16702.pdf)."
        )
    from . import regles  # local : évite un cycle d'import
    reg = regles.determiner_regime(
        (projet.get("ombriere") or {}).get("puissance_kwc"),
        (projet.get("urbanisme") or {}).get("secteur_abf"),
    )
    if reg["regime"] != regles.REGIME_DP:
        raise ValueError(
            "Le projet relève du Permis de Construire (" + " ; ".join(reg["raisons"])
            + ") : le Cerfa DP 16702 ne s'applique pas."
        )

    avertissements: list[str] = []
    toutes_parcelles = (projet.get("localisation") or {}).get("parcelles") or []
    if len(toutes_parcelles) > 3:
        avertissements.append(
            f"{len(toutes_parcelles)} parcelles : seules les 3 premières figurent "
            "sur le formulaire (3 lignes), joignez la liste complète sur papier "
            "libre — la notice les mentionne toutes."
        )

    champs_projet, trous = _champs_projet(projet)
    avertissements.extend(trous)

    champs = {
        **_champs_demandeur(projet),
        **_champs_terrain(projet),
        **champs_projet,
        **_champs_engagement(projet),
    }
    champs = {k: v for k, v in champs.items() if v}

    reader = PdfReader(GABARIT)
    writer = PdfWriter()
    writer.append(reader)
    for page in writer.pages:
        writer.update_page_form_field_values(page, champs, auto_regenerate=False)
    try:  # afficher les valeurs dans tous les lecteurs PDF
        writer.set_need_appearances_writer(True)
    except AttributeError:  # pragma: no cover - selon version pypdf
        pass

    chemin = config.assets_dir(projet["id"]) / "cerfa_16702_prerempli.pdf"
    with open(chemin, "wb") as f:
        writer.write(f)
    return chemin, sorted(champs), avertissements
