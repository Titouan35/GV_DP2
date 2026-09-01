"""Contrôles de cohérence : les incohérences réelles doivent être vues.

Chaque test part d'un cas CONSTATÉ sur un dossier existant le 01/09/2026, pas
d'un cas théorique. Le projet de référence est volontairement cohérent : un
test qui échoue signale donc soit une régression du contrôle, soit un
faux positif.
"""
from __future__ import annotations

import pytest

from app import coherence, config


def projet_coherent(**surcharges) -> dict:
    """Dossier sans aucune anomalie : la référence de tous les tests."""
    projet = {
        "id": "coherence-test",
        "nom": "Parking test",
        "localisation": {
            "adresse": "68 Rue du Jura 01460 Montréal-la-Cluse",
            "commune": "Montréal-la-Cluse",
            "code_postal": "01460",
            "code_insee": "01265",
            "parcelles": [
                {"section": "AI", "numero": "0479", "commune": "Montréal-la-Cluse",
                 "code_insee": "01265"},
                {"section": "AI", "numero": "0242", "commune": "Montréal-la-Cluse",
                 "code_insee": "01265"},
            ],
        },
        "ombriere": {"famille": "START PLAINE Double", "puissance_kwc": 157.78},
        "notice": {"sections": {}},
        "documents": {},
    }
    projet.update(surcharges)
    return projet


def codes(anomalies) -> set[str]:
    return {a["code"] for a in anomalies}


# ------------------------------------------------------------------ référence

def test_un_dossier_coherent_ne_leve_aucune_anomalie():
    assert coherence.controler(projet_coherent()) == []


# ------------------------------------------------------------------ parcelles

def test_parcelles_de_deux_communes_signalees():
    """Cas réel : projet « Carrefour Montréal-la-Cluse » portant deux parcelles
    à Anse (69) et deux à Montréal-la-Cluse (01), 100 km d'écart."""
    # Arrange
    projet = projet_coherent()
    projet["localisation"]["parcelles"] = [
        {"section": "AK", "numero": "0308", "commune": "Anse", "code_insee": "69009"},
        {"section": "AK", "numero": "0298", "commune": "Anse", "code_insee": "69009"},
        {"section": "AI", "numero": "0479", "commune": "Montréal-la-Cluse",
         "code_insee": "01265"},
    ]

    # Act
    anomalies = coherence.controler(projet)

    # Assert
    assert "parcelles_multi_communes" in codes(anomalies)
    anomalie = next(a for a in anomalies if a["code"] == "parcelles_multi_communes")
    assert anomalie["gravite"] == coherence.BLOQUANTE
    # le message doit NOMMER les communes, sinon il n'aide pas à corriger
    assert "Anse" in anomalie["message"] and "Montréal-la-Cluse" in anomalie["message"]


def test_parcelles_toutes_hors_de_la_commune_du_terrain():
    """L'adresse a été changée après le choix des parcelles."""
    # Arrange
    projet = projet_coherent()
    projet["localisation"]["parcelles"] = [
        {"section": "AK", "numero": "0308", "commune": "Anse", "code_insee": "69009"},
    ]

    # Act / Assert
    anomalies = coherence.controler(projet)
    assert "parcelles_hors_commune" in codes(anomalies)
    assert all(a["gravite"] == coherence.BLOQUANTE
               for a in anomalies if a["code"] == "parcelles_hors_commune")


def test_parcelles_sans_code_insee_ne_declenchent_pas_de_faux_positif():
    """Une parcelle saisie à la main peut ne pas porter de code INSEE."""
    # Arrange
    projet = projet_coherent()
    projet["localisation"]["parcelles"] = [{"section": "AI", "numero": "0479"}]

    # Act / Assert
    assert coherence.controler(projet) == []


def test_aucune_parcelle_ne_declenche_pas_de_controle():
    projet = projet_coherent()
    projet["localisation"]["parcelles"] = []
    assert codes(coherence.controler(projet)) == set()


# -------------------------------------------------------------------- adresse

def test_parcelles_sans_adresse_signalees():
    """Taper une adresse sans cliquer une proposition ne l'enregistre pas."""
    # Arrange
    projet = projet_coherent()
    projet["localisation"]["adresse"] = ""

    # Act / Assert
    assert "adresse_absente" in codes(coherence.controler(projet))


# --------------------------------------------------------------------- notice

def test_notice_redigee_avec_une_autre_commune_signalee():
    """Cause prouvée de l'incohérence d'adresse dans le PPTX : la notice fige
    une copie des valeurs et n'est jamais resynchronisée."""
    # Arrange : notice rédigée du temps où le projet était à Anse
    projet = projet_coherent()
    projet["notice"]["sections"] = {
        "presentation": "Le présent dossier de déclaration préalable porte sur "
                        "l'installation d'ombrières photovoltaïques sur le parc de "
                        "stationnement existant situé 82 Avenue du Pré aux Moutons, "
                        "à Anse (69480). Le projet s'inscrit dans le cadre de la loi "
                        "APER et n'altère pas l'usage de stationnement du site.",
    }

    # Act
    anomalies = coherence.controler(projet)

    # Assert
    assert "notice_perimee_commune" in codes(anomalies)
    assert "notice_perimee_adresse" in codes(anomalies)


def test_notice_a_jour_ne_declenche_rien():
    # Arrange
    projet = projet_coherent()
    projet["notice"]["sections"] = {
        "presentation": "Le présent dossier de déclaration préalable porte sur "
                        "l'installation d'ombrières photovoltaïques sur le parc de "
                        "stationnement existant situé 68 Rue du Jura 01460 "
                        "Montréal-la-Cluse, à Montréal-la-Cluse (01460). Le projet "
                        "s'inscrit dans le cadre de la loi APER.",
    }

    # Act / Assert
    assert coherence.controler(projet) == []


def test_notice_comparee_sans_tenir_compte_des_accents_ni_de_la_casse():
    """« MONTREAL-LA-CLUSE » saisi en capitales sans accent est la même commune.
    Sans cette tolérance, le contrôle crierait au loup en permanence."""
    # Arrange
    projet = projet_coherent()
    projet["notice"]["sections"] = {
        "presentation": "LE PRESENT DOSSIER DE DECLARATION PREALABLE PORTE SUR "
                        "L'INSTALLATION D'OMBRIERES PHOTOVOLTAIQUES SUR LE PARKING "
                        "EXISTANT SITUE 68 RUE DU JURA 01460 MONTREAL-LA-CLUSE, "
                        "COMMUNE DE MONTREAL-LA-CLUSE. PROJET LOI APER.",
    }

    # Act / Assert
    assert coherence.controler(projet) == []


def test_notice_vide_ne_declenche_rien():
    projet = projet_coherent()
    projet["notice"]["sections"] = {"presentation": "", "description": "   "}
    assert coherence.controler(projet) == []


# --------------------------------------------------------------------- pièces

def test_piece_declaree_mais_fichier_absent_signalee(tmp_path, monkeypatch):
    """Cas réel : le JSON déclarait dp6.jpg, le fichier n'existait plus, et la
    checklist affichait pourtant « 11/11 pièces prêtes »."""
    # Arrange
    monkeypatch.setattr(config, "PROJETS_DIR", tmp_path)
    projet = projet_coherent(documents={
        "dp6": {"fichier": "coherence-test.assets/uploads/dp6.jpg",
                "nom_fichier": "Deux peignes doubles 2.jpg"},
    })

    # Act
    anomalies = coherence.controler(projet)

    # Assert
    assert "fichier_manquant_dp6" in codes(anomalies)
    anomalie = next(a for a in anomalies if a["code"] == "fichier_manquant_dp6")
    assert anomalie["gravite"] == coherence.BLOQUANTE
    assert "Deux peignes doubles 2.jpg" in anomalie["message"]


def test_piece_dont_le_fichier_existe_ne_declenche_rien(tmp_path, monkeypatch):
    # Arrange
    monkeypatch.setattr(config, "PROJETS_DIR", tmp_path)
    fichier = tmp_path / "coherence-test.assets" / "uploads" / "dp6.jpg"
    fichier.parent.mkdir(parents=True)
    fichier.write_bytes(b"contenu")
    projet = projet_coherent(documents={
        "dp6": {"fichier": "coherence-test.assets/uploads/dp6.jpg",
                "nom_fichier": "dp6.jpg"},
    })

    # Act / Assert
    assert coherence.controler(projet) == []


# ------------------------------------------------------------------- ombrière

def test_type_d_ombriere_absent_signale_en_attention():
    """Doctrine « brouillon avec trous signalés » : on n'empêche pas de
    travailler, on prévient que les cotes catalogue ne seront pas affirmées."""
    # Arrange
    projet = projet_coherent()
    projet["ombriere"]["famille"] = None

    # Act
    anomalies = coherence.controler(projet)

    # Assert
    anomalie = next(a for a in anomalies if a["code"] == "type_ombriere_absent")
    assert anomalie["gravite"] == coherence.ATTENTION
    assert not coherence.bloquantes(projet)


def test_puissance_absente_signalee():
    projet = projet_coherent()
    projet["ombriere"]["puissance_kwc"] = None
    assert "puissance_absente" in codes(coherence.controler(projet))


# ------------------------------------------------------------------- tri, API

def test_anomalies_triees_les_plus_graves_d_abord():
    # Arrange : une attention et une bloquante dans le même dossier
    projet = projet_coherent()
    projet["ombriere"]["famille"] = None
    projet["localisation"]["parcelles"] = [
        {"section": "AK", "numero": "0308", "commune": "Anse", "code_insee": "69009"},
    ]

    # Act
    anomalies = coherence.controler(projet)

    # Assert
    gravites = [a["gravite"] for a in anomalies]
    assert gravites[0] == coherence.BLOQUANTE
    assert gravites[-1] == coherence.ATTENTION


def test_bloquantes_ne_retient_que_les_bloquantes():
    projet = projet_coherent()
    projet["ombriere"]["famille"] = None
    assert coherence.bloquantes(projet) == []


def test_chaque_anomalie_dit_ou_corriger():
    """Un message sans point d'entrée fait perdre plus de temps qu'il n'en fait
    gagner : l'utilisateur doit savoir sur quel écran aller."""
    # Arrange
    projet = projet_coherent()
    projet["ombriere"] = {}
    projet["localisation"]["adresse"] = ""

    # Act / Assert
    for anomalie in coherence.controler(projet):
        assert anomalie["ou"].startswith("Étape"), anomalie


def test_le_controle_ne_modifie_pas_le_projet():
    """Contrôler doit être sans effet de bord : le module est appelé à chaque
    évaluation, y compris pendant l'assemblage."""
    # Arrange
    projet = projet_coherent()
    projet["ombriere"]["famille"] = None
    import copy
    avant = copy.deepcopy(projet)

    # Act
    coherence.controler(projet)

    # Assert
    assert projet == avant


def test_note_de_travail_trop_courte_n_est_pas_jugee():
    """On ne peut rien conclure de l'absence d'une adresse dans trois mots.

    Crier au loup sur une amorce ferait ignorer les vraies alertes. Le contrôle
    ne se prononce qu'à partir d'un texte de longueur de notice.
    """
    # Arrange
    projet = projet_coherent()
    projet["notice"]["sections"] = {"presentation": "Texte de test."}

    # Act / Assert
    assert coherence.controler(projet) == []
