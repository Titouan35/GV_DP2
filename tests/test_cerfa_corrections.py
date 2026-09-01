"""Corrections du Cerfa du 01/09/2026 : trois défauts rendaient le formulaire faux.

La cartographie du gabarit officiel 16702*03 (page 7, coordonnées des champs
relevées dans le PDF) donne le sens exact de chaque case :

    y=564  C2ZP1_crete         « Indiquez sa puissance crête : ___ kW »
    y=533  C2ZR1_destination   « et la destination principale de l'énergie »
    y=499  C2ZE1_puissance     « la puissance électrique nécessaire à votre projet »

Autrement dit, les noms des champs disent vrai et le code les avait inversés :
la hauteur hors tout partait dans la puissance crête, et la puissance crête
dans la puissance de raccordement.

Doctrine appliquée ici, choisie par Florent : brouillon avec trous signalés.
Ce que l'outil ne sait pas, il le laisse vide et le DIT. Il n'invente rien et
ne coche aucune case qui engage juridiquement le déclarant.
"""
from __future__ import annotations

from datetime import date

import pytest
from pypdf import PdfReader

from app import cerfa, config

PROJET_BASE = {
    "id": "cerfa-corrections",
    "nom": "Parking test",
    "mo": {"type": "societe", "raison_sociale": "GREENVOLT NEXT FRANCE",
           "representant": "Prenom Nom", "siret": "123 456 789 00012",
           "adresse": "79 rue centrale 01500 Ambérieu", "email": "f@example.fr"},
    "localisation": {
        "adresse": "68 Rue du Jura", "commune": "Montréal-la-Cluse",
        "code_postal": "01460", "code_insee": "01265",
        "parcelles": [{"section": "AI", "numero": "0479", "contenance_m2": 7788,
                       "commune": "Montréal-la-Cluse", "code_insee": "01265"}],
    },
    "ombriere": {"famille": "START PLAINE Double", "puissance_kwc": 500,
                 "nb_places": 120},
    "urbanisme": {},
    "notice": {"sections": {}},
    "documents": {},
}


@pytest.fixture()
def assets(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROJETS_DIR", tmp_path)
    return tmp_path


def remplir(projet: dict):
    """Génère le Cerfa et renvoie (valeurs des champs, avertissements)."""
    chemin, champs, avertissements = cerfa.preremplir(projet)
    lus = PdfReader(str(chemin)).get_fields()
    valeurs = {k: (v.get("/V") if hasattr(v, "get") else None) for k, v in lus.items()}
    return valeurs, avertissements


def projet(**surcharges) -> dict:
    p = {**PROJET_BASE}
    for cle, val in surcharges.items():
        if isinstance(val, dict) and isinstance(p.get(cle), dict):
            p[cle] = {**p[cle], **val}
        else:
            p[cle] = val
    return p


# ------------------------------------------------- (a) inversion des puissances

def test_la_puissance_crete_recoit_bien_la_puissance(assets):
    """C2ZP1_crete est la case « puissance crête : ___ kW » du formulaire.

    Elle recevait la hauteur hors tout en mètres (« 4,50 m »).
    """
    # Act
    valeurs, _ = remplir(projet())

    # Assert
    assert valeurs["C2ZP1_crete"] == "500"
    assert "m" not in (valeurs["C2ZP1_crete"] or "")


def test_la_puissance_de_raccordement_reste_vide(assets):
    """C2ZE1_puissance est la puissance électrique NÉCESSAIRE au projet.

    L'outil ne la connaît pas : il la laisse vide et le signale, au lieu d'y
    recopier la puissance crête comme il le faisait.
    """
    # Act
    valeurs, avertissements = remplir(projet())

    # Assert
    assert not valeurs.get("C2ZE1_puissance")
    assert any("raccordement" in a.lower() for a in avertissements), avertissements


def test_la_hauteur_n_apparait_dans_aucune_case_de_puissance(assets):
    """Garde-fou contre un retour de l'inversion sous une autre forme."""
    # Act
    valeurs, _ = remplir(projet(ombriere={"hauteur_hors_tout_m": 4.5}))

    # Assert
    for champ in ("C2ZP1_crete", "C2ZE1_puissance"):
        assert "4,5" not in (valeurs.get(champ) or "")


# ------------------------------------------------------ (b) date d'engagement

def test_la_date_est_ecrite_en_huit_chiffres(assets):
    """E1D_date est un champ « peigne » de 8 cases (MaxLen=8, drapeau comb).

    On y écrivait « 01/09/2026 », soit 10 caractères : les séparateurs déjà
    imprimés se doublaient et l'année était tronquée.
    """
    # Act
    valeurs, _ = remplir(projet())

    # Assert
    attendu = date.today().strftime("%d%m%Y")
    assert valeurs["E1D_date"] == attendu
    assert len(valeurs["E1D_date"]) == 8
    assert "/" not in valeurs["E1D_date"]


def test_la_date_tient_dans_la_longueur_du_champ(assets):
    """Contrôle générique : aucune valeur ne doit dépasser le MaxLen de sa case,
    sinon le lecteur PDF tronque en silence."""
    # Arrange
    chemin, _, _ = cerfa.preremplir(projet())
    lecteur = PdfReader(str(chemin))

    # Act / Assert
    for page in lecteur.pages:
        for annot in (page.get("/Annots") or []):
            champ = annot.get_object()
            nom, maxlen, valeur = champ.get("/T"), champ.get("/MaxLen"), champ.get("/V")
            if nom and maxlen and isinstance(valeur, str):
                assert len(valeur) <= int(maxlen), f"{nom} : {valeur!r} > {maxlen}"


# ------------------------------------------------------- (c) case de consentement

def test_aucune_case_de_consentement_n_est_cochee(assets):
    """D5A_acceptation engage le déclarant à recevoir les actes par voie
    électronique. Le module promettait dans son propre en-tête de ne cocher
    aucune case à risque, et la cochait."""
    # Act
    valeurs, _ = remplir(projet())

    # Assert
    assert valeurs.get("D5A_acceptation") in (None, "", "/Off")


def test_l_email_est_quand_meme_repris(assets):
    """Ne pas cocher le consentement ne doit pas faire perdre l'adresse mail."""
    valeurs, _ = remplir(projet())
    assert valeurs["D5GE1_email"] == "f@example.fr"


# --------------------------------------------- doctrine : ne rien affirmer

def test_sans_type_d_ombriere_aucune_cote_du_catalogue_n_est_affirmee(assets):
    """Sans famille choisie, catalogue.py retombe sur START PLAINE Bas. Ses
    cotes n'ont alors rien à faire dans un formulaire officiel."""
    # Arrange
    p = projet()
    p["ombriere"] = {"puissance_kwc": 500}

    # Act
    valeurs, avertissements = remplir(p)

    # Assert
    description = valeurs.get("C2ZD1_description") or ""
    assert "hauteur" not in description.lower()
    assert "START PLAINE" not in description
    assert any("type d'ombrière" in a.lower() for a in avertissements), avertissements


def test_la_conformite_aper_n_est_plus_affirmee(assets):
    """Le descriptif affirmait « Conforme à l'obligation de la loi APER » sans
    aucun test. C'est une conclusion juridique, elle appartient au déclarant."""
    valeurs, _ = remplir(projet())
    assert "Conforme" not in (valeurs.get("C2ZD1_description") or "")


def test_le_descriptif_reste_factuel_et_utile(assets):
    """Retirer les affirmations ne doit pas vider le descriptif de sa substance."""
    valeurs, _ = remplir(projet())
    description = valeurs["C2ZD1_description"]
    assert "ombrières photovoltaïques" in description
    assert "500 kWc" in description
    assert "120 places" in description


# ------------------------------------------------- trous explicitement signalés

def test_les_champs_laisses_vides_sont_tous_signales(assets):
    """Doctrine « brouillon avec trous signalés » : chaque case volontairement
    laissée vide doit apparaître dans les avertissements, sinon le BE ne peut
    pas savoir ce qu'il lui reste à compléter."""
    # Act
    _, avertissements = remplir(projet())

    # Assert
    texte = " ".join(avertissements).lower()
    assert "raccordement" in texte
    assert "destination" in texte
    assert any(a.startswith("À compléter") for a in avertissements), avertissements


def test_plus_de_trois_parcelles_reste_signale(assets):
    """Le formulaire n'a que 3 lignes de parcelles : comportement conservé."""
    # Arrange
    p = projet()
    p["localisation"] = {**p["localisation"], "parcelles": [
        {"section": "AI", "numero": str(n), "commune": "Montréal-la-Cluse",
         "code_insee": "01265"} for n in range(5)]}

    # Act
    _, avertissements = remplir(p)

    # Assert
    assert any("5 parcelles" in a for a in avertissements)


def test_la_puissance_est_ecrite_avec_une_virgule_decimale(assets):
    """Le formulaire est français : 157,78 et non 157.78.

    Le descriptif employait déjà la virgule, la case de puissance non : le même
    nombre apparaissait sous deux formes sur le même document.
    """
    # Act
    valeurs, _ = remplir(projet(ombriere={"puissance_kwc": 157.78}))

    # Assert
    assert valeurs["C2ZP1_crete"] == "157,78"
    assert "157,78 kWc" in valeurs["C2ZD1_description"]


# ------------------------------ adresse du demandeur (relecture du 01/09)

def test_l_adresse_du_demandeur_est_decoupee_dans_les_bonnes_cases(assets):
    """Le formulaire a des cases distinctes : numéro, voie, code postal,
    localité. Tout déverser dans « Voie » la tronquait à 40 caractères et
    laissait le code postal et la localité vides."""
    # Arrange
    p = projet(mo={"adresse": "Immeuble Le Danica, 21 avenue Georges Pompidou, 69003 Lyon"})

    # Act
    valeurs, _ = remplir(p)

    # Assert
    assert valeurs["D3C_code"] == "69003"
    assert valeurs["D3L_localite"] == "Lyon"
    assert "Danica" in valeurs["D3V_voie"]


def test_le_numero_de_voie_est_isole_quand_il_est_reconnaissable(assets):
    valeurs, _ = remplir(projet(mo={"adresse": "12 bis avenue de la Gare, 01000 Bourg-en-Bresse"}))
    assert valeurs["D3N_numero"] == "12 bis"
    assert valeurs["D3V_voie"] == "avenue de la Gare"
    assert valeurs["D3L_localite"] == "Bourg-en-Bresse"


def test_une_adresse_non_reconnue_n_est_pas_perdue(assets):
    """Le découpage est au mieux : s'il échoue, on ne perd rien, on retombe sur
    le comportement précédent."""
    valeurs, _ = remplir(projet(mo={"adresse": "Lieu-dit Les Granges"}))
    assert valeurs["D3V_voie"] == "Lieu-dit Les Granges"
    assert not valeurs.get("D3C_code")


def test_toutes_les_valeurs_tiennent_dans_leurs_cases_sur_une_adresse_longue(assets):
    """Le contrôle générique de MaxLen ne valait que pour l'adresse courte du
    projet de test : on l'éprouve sur un cas réellement long."""
    # Arrange
    p = projet(mo={"adresse": "Immeuble Le Danica, 21 avenue Georges Pompidou, 69003 Lyon"})

    # Act
    chemin, _, _ = cerfa.preremplir(p)

    # Assert
    for page in PdfReader(str(chemin)).pages:
        for annot in (page.get("/Annots") or []):
            champ = annot.get_object()
            nom, maxlen, valeur = champ.get("/T"), champ.get("/MaxLen"), champ.get("/V")
            if nom and maxlen and isinstance(valeur, str):
                assert len(valeur) <= int(maxlen), f"{nom} : {valeur!r} > {maxlen}"
