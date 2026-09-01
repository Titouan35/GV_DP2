"""La notice DP11 n'a pas le droit d'affirmer ce que l'outil ne sait pas.

Trois défauts relevés par la relecture adversariale du 01/09/2026, tous du même
genre : un texte lu par un instructeur de mairie affirmait des faits que
personne n'avait vérifiés.

Le plus gênant était le troisième : le Cerfa avait été protégé le matin même
contre les cotes du catalogue quand aucun type d'ombrière n'est choisi, mais
la notice, elle, continuait de les affirmer. L'écran de l'étape 3 et le panneau
Cohérence promettaient pourtant à l'utilisateur que la notice ne le ferait pas.
"""
from __future__ import annotations

from app import notice


def projet(**surcharges) -> dict:
    base = {
        "mo": {"raison_sociale": "GREENVOLT NEXT FRANCE"},
        "localisation": {"adresse": "68 Rue du Jura", "commune": "Montréal-la-Cluse",
                         "code_postal": "01460", "code_insee": "01265", "parcelles": []},
        "ombriere": {"famille": "START PLAINE Double", "puissance_kwc": 500},
        "urbanisme": {"secteur_abf": False, "risques": {"risques": []}},
    }
    base.update(surcharges)
    return base


def section(projet_dict: dict, cle: str) -> str:
    return notice.generer_sections(projet_dict)[cle]


# --------------------------------------------- cotes du catalogue sans type

def test_sans_type_d_ombriere_la_notice_n_affirme_aucune_cote():
    """Avec la seule puissance, parametres_effectifs retombe sur START PLAINE
    Bas. Ses cotes (5 m de profondeur, 3,50 m hors tout) n'ont rien à faire
    dans une notice que personne n'a saisies."""
    # Arrange
    p = projet(ombriere={"puissance_kwc": 500})

    # Act
    description = section(p, "description")

    # Assert
    assert "3,50" not in description
    assert "5 m de profondeur" not in description
    assert "à préciser" in description


def test_avec_un_type_les_cotes_du_produit_sont_bien_affirmees():
    """La correction ne doit pas vider la notice quand le type EST choisi :
    la profondeur du type est une cote réelle du produit."""
    description = section(projet(), "description")
    assert "profondeur" in description
    assert "Double" in description


def test_la_notice_et_le_cerfa_disent_la_meme_chose_sans_type(tmp_path, monkeypatch):
    """Les deux documents partent dans le même dossier : ils ne peuvent pas se
    contredire sur les cotes."""
    # Arrange
    from app import cerfa, config
    monkeypatch.setattr(config, "PROJETS_DIR", tmp_path)
    p = projet(ombriere={"puissance_kwc": 500})
    p["id"] = "notice-cerfa"

    # Act
    description_notice = section(p, "description")
    _, _, avertissements = cerfa.preremplir(p)

    # Assert : ni l'un ni l'autre n'affirme de cote, et le Cerfa le signale
    assert "3,50" not in description_notice
    assert any("type d'ombrière" in a.lower() for a in avertissements)


# ------------------------------------------------- ABF et risques non vérifiés

def test_sans_interrogation_des_apis_rien_n_est_affirme_sur_l_abf():
    """secteur_abf à None signifie « pas encore vérifié », pas « il n'y en a
    pas ». Affirmer l'absence de périmètre de protection est le genre de
    phrase sur laquelle un instructeur s'appuie."""
    # Arrange : étape urbanisme jamais lancée
    p = projet(urbanisme={})

    # Act
    texte = section(p, "reglementaire")

    # Assert
    assert "reste à vérifier" in texte
    assert "n'est situé dans aucun périmètre" not in texte


def test_sans_interrogation_des_apis_rien_n_est_affirme_sur_les_risques():
    texte = section(projet(urbanisme={}), "reglementaire")
    assert "restent à vérifier" in texte
    assert "Aucun risque majeur" not in texte


def test_apres_interrogation_l_absence_de_risque_peut_etre_affirmee():
    """La nuance ne doit pas rendre la notice inutile : quand Géorisques a
    répondu, l'absence de risque est un fait, et elle s'écrit."""
    texte = section(projet(urbanisme={"secteur_abf": False, "risques": {"risques": []}}),
                    "reglementaire")
    assert "Aucun risque majeur" in texte
    assert "n'est situé dans aucun périmètre" in texte


def test_les_risques_reels_sont_toujours_listes():
    p = projet(urbanisme={"secteur_abf": False,
                          "risques": {"risques": [{"libelle": "Inondation"},
                                                  {"libelle": "Séisme"}]}})
    texte = section(p, "reglementaire")
    assert "Inondation" in texte and "Séisme" in texte


# ------------------------------------------------------------------- régime

def test_la_notice_ne_declare_plus_la_dp_pour_un_projet_en_permis_de_construire():
    """Le régime était écrit en dur. Pour un projet de 5 MWc, la notice
    affirmait la déclaration préalable pendant que le Cerfa, lui, refusait de
    se générer. Les deux pièces du même dossier se contredisaient."""
    # Arrange
    p = projet(ombriere={"famille": "START PLAINE Double", "puissance_kwc": 5000})

    # Act
    texte = section(p, "reglementaire")

    # Assert
    assert "permis de construire" in texte
    assert "relève de la déclaration préalable" not in texte
    assert "3 MWc" in texte          # la raison est donnée, pas seulement le verdict


def test_le_secteur_abf_bascule_aussi_le_regime_dans_la_notice():
    p = projet(urbanisme={"secteur_abf": True, "risques": {"risques": []}})
    texte = section(p, "reglementaire")
    assert "permis de construire" in texte
    assert "ABF" in texte


def test_un_projet_ordinaire_reste_en_declaration_prealable():
    texte = section(projet(), "reglementaire")
    assert "relève de la déclaration préalable" in texte
    assert "R.421-9" in texte
