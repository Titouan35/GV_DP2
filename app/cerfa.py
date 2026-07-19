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
        champs["D5A_acceptation"] = COCHE  # accepte l'échange par voie électronique
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


def _champs_projet(projet: dict) -> dict:
    omb = projet.get("ombriere") or {}
    if not omb.get("famille") and not omb.get("puissance_kwc"):
        return {}
    p = parametres_effectifs(omb)
    # les dimensions ne sont affirmées que si elles ont été SAISIES : les
    # défauts fabriqués (4 travées x 5 m = 20 m) n'ont rien à faire dans un
    # formulaire officiel (« aucune donnée inventée », plan §13)
    dims = (f"{p['longueur_m']:g} m x {p['profondeur_m']:g} m, ".replace(".", ",")
            if p["saisis"]["longueur"] else
            f"{p['profondeur_m']:g} m de profondeur, ".replace(".", ","))
    desc = (
        f"Installation d'ombrières photovoltaïques sur le parking existant : "
        f"structure {p['famille']} en acier galvanisé, "
        f"{dims}"
        f"hauteur hors tout {p['h_haut_m']:.2f} m".replace(".", ",")
        + f", pente {p['pente_deg']:g} degrés".replace(".", ",")
        + ", modules photovoltaïques full black"
    )
    if omb.get("module_puissance_wc"):
        desc += f" de {omb['module_puissance_wc']:g} Wc".replace(".", ",")
    if omb.get("puissance_kwc"):
        desc += f", puissance {omb['puissance_kwc']:g} kWc".replace(".", ",")
    if omb.get("nb_places"):
        desc += f", {omb['nb_places']} places couvertes"
    desc += ". Conforme à l'obligation de la loi APER (art. L.171-4 CCH)."
    champs = {
        "C2ZD1_description": desc,
        "C2ZA1_nouvelle": COCHE,  # une ombrière est une construction nouvelle
    }
    if omb.get("puissance_kwc"):
        champs["C2ZE1_puissance"] = f"{omb['puissance_kwc']:g}"
    champs["C2ZP1_crete"] = f"{p['h_haut_m']:.2f} m".replace(".", ",")
    # stationnement inchangé avant / après travaux
    if omb.get("nb_places"):
        champs["S1A_stationnementavant"] = str(omb["nb_places"])
        champs["S1M_stationnementapres"] = str(omb["nb_places"])
    return champs


def _champs_engagement(projet: dict) -> dict:
    """Cadre d'engagement du déclarant : lieu (commune) + date du jour."""
    loc = projet.get("localisation") or {}
    return {
        "E1L_lieu": loc.get("commune") or "",
        "E1D_date": date.today().strftime("%d/%m/%Y"),
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

    champs = {
        **_champs_demandeur(projet),
        **_champs_terrain(projet),
        **_champs_projet(projet),
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
