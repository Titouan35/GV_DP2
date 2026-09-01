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

    # Assert : sans empreinte (notice d'avant le 01/09/2026), le contrôle
    # retombe sur la recherche de texte. C'est une heuristique, donc elle
    # AVERTIT sans bloquer le dépôt.
    assert "notice_peut_etre_perimee_commune" in codes(anomalies)
    assert "notice_peut_etre_perimee_adresse" in codes(anomalies)
    assert all(a["gravite"] == coherence.SERIEUSE for a in anomalies)


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


# ------------------------------------------- notice : comparaison par empreinte

def test_notice_avec_empreinte_detecte_une_valeur_qui_a_change():
    """Depuis le 01/09/2026 la notice enregistre les valeurs qui la portent.

    On compare donc des faits au lieu de chercher une chaîne. La recherche de
    chaîne se trompait dans les deux sens, et ne couvrait que 2 des 23 valeurs.
    """
    # Arrange : notice rédigée quand la puissance valait 250 kWc
    from app import notice as mod_notice
    projet = projet_coherent()
    empreinte = mod_notice.valeurs_ancrage(projet)
    projet["notice"] = {"sections": {"presentation": "Texte relu par le BE."},
                        "valeurs": empreinte}
    projet["ombriere"]["puissance_kwc"] = 999      # la puissance change après coup

    # Act
    anomalies = coherence.controler(projet)

    # Assert
    anomalie = next(a for a in anomalies if a["code"] == "notice_perimee")
    assert anomalie["gravite"] == coherence.BLOQUANTE
    assert "puissance" in anomalie["message"]
    # le message donne les DEUX valeurs, sinon on ne sait pas quoi corriger
    assert "999" in anomalie["message"]


def test_notice_reformulee_a_la_main_n_est_pas_declaree_perimee():
    """Faux positif relevé par la relecture : une notice relue et reformulée
    par le bureau d'études ne répète pas forcément l'adresse mot pour mot. La
    déclarer périmée poussait à la régénérer, donc à détruire la relecture."""
    # Arrange : texte entièrement réécrit, mais données inchangées
    from app import notice as mod_notice
    projet = projet_coherent()
    projet["notice"] = {
        "sections": {"presentation": "Le parking du magasin, en entrée de ville, "
                                     "accueille un projet d'ombrières."},
        "valeurs": mod_notice.valeurs_ancrage(projet),
    }

    # Act / Assert
    assert coherence.controler(projet) == []


def test_notice_citant_la_bonne_commune_ET_l_ancienne_est_detectee():
    """Faux négatif relevé par la relecture : le test de sous-chaîne passait au
    vert dès que la bonne commune apparaissait quelque part, même si l'ancienne
    restait dans une autre section."""
    # Arrange : la notice porte l'empreinte du temps où le projet était à Anse
    from app import notice as mod_notice
    ancien_projet = projet_coherent()
    ancien_projet["localisation"] = {**ancien_projet["localisation"],
                                     "commune": "Anse", "code_insee": "69009"}
    empreinte = mod_notice.valeurs_ancrage(ancien_projet)

    projet = projet_coherent()
    projet["notice"] = {
        "sections": {"presentation": "Projet à Montréal-la-Cluse.",
                     "etat_initial": "Le terrain se situe sur la commune de Anse."},
        "valeurs": empreinte,
    }

    # Act / Assert
    assert "notice_perimee" in codes(coherence.controler(projet))


# --------------------------------------------------------- typographie

def test_une_commune_collee_depuis_word_ne_declenche_pas_de_faux_positif():
    """Word remplace l'apostrophe droite par une apostrophe courbe. Sans
    normalisation, « L'Arbresle » et « L’Arbresle » sont deux communes, et le
    contrôle bloquait un dépôt légitime."""
    # Arrange
    projet = projet_coherent()
    projet["localisation"] = {**projet["localisation"], "commune": "L'Arbresle",
                              "adresse": "1 rue de L'Arbresle", "code_insee": "69010",
                              "parcelles": []}
    projet["notice"] = {"sections": {"presentation":
        "Le présent dossier de déclaration préalable porte sur l'installation "
        "d'ombrières photovoltaïques sur le parc de stationnement existant situé "
        "1 rue de L\u2019Arbresle, commune de L\u2019Arbresle. Le projet s'inscrit "
        "dans le cadre de la loi APER."}}

    # Act / Assert
    assert coherence.controler(projet) == []


# --------------------------------------------------------------- régime

def test_un_projet_en_permis_de_construire_est_bloque():
    """Trou critique relevé par la relecture : au-delà de 3 MWc le Cerfa
    refusait de se générer, mais le dossier PPTX sortait « prêt au dépôt »."""
    # Arrange
    projet = projet_coherent()
    projet["ombriere"]["puissance_kwc"] = 3200

    # Act
    anomalies = coherence.controler(projet)

    # Assert
    anomalie = next(a for a in anomalies if a["code"] == "regime_permis_de_construire")
    assert anomalie["gravite"] == coherence.BLOQUANTE
    assert "3 MWc" in anomalie["message"]
    assert coherence.bloquantes(projet)


def test_le_secteur_abf_bascule_aussi_en_permis_de_construire():
    projet = projet_coherent()
    projet["urbanisme"] = {"secteur_abf": True}
    assert "regime_permis_de_construire" in codes(coherence.controler(projet))


def test_un_projet_sous_le_seuil_n_est_pas_bloque():
    projet = projet_coherent()
    projet["ombriere"]["puissance_kwc"] = 2999
    assert "regime_permis_de_construire" not in codes(coherence.controler(projet))
