"""Moteur réglementaire : régime DP/PC et complétude."""
from app.models import Localisation, Parcelle, Projet
from app.regles import PIECES_DP, completude, determiner_regime, evaluer


def test_regime_dp_par_defaut():
    r = determiner_regime(500, False)
    assert r["regime"] == "DP"
    assert r["cerfa"] == "13404"


def test_regime_pc_si_3mwc():
    r = determiner_regime(3000, False)
    assert r["regime"] == "PC"
    assert r["cerfa"] == "13409"
    assert any("3 MWc" in raison for raison in r["raisons"])


def test_regime_pc_si_abf():
    r = determiner_regime(500, True)
    assert r["regime"] == "PC"
    assert any("ABF" in raison for raison in r["raisons"])


def test_regime_sans_puissance():
    # Puissance non saisie : DP par défaut tant que rien ne bascule
    r = determiner_regime(None, None)
    assert r["regime"] == "DP"


def test_completude_projet_vide():
    projet = Projet(nom="Test")
    c = completude(projet)
    assert c["total"] == len(PIECES_DP) == 12
    assert c["pretes"] == 0
    statuts = {p["code"]: p["statut"] for p in c["pieces"]}
    assert statuts["dp1_situation"] == "a_completer"
    assert statuts["dp6"] == "en_attente"


def test_completude_localisation_validee():
    projet = Projet(
        nom="Soufflenheim",
        localisation=Localisation(
            code_insee="67473",
            parcelles=[Parcelle(section="30", numero="0464", contenance_m2=30201)],
        ),
    )
    c = completude(projet)
    statuts = {p["code"]: p["statut"] for p in c["pieces"]}
    assert statuts["dp1_situation"] == "prete"
    assert statuts["dp1_cadastral"] == "prete"
    assert statuts["dp1_aerien"] == "prete"
    # dp11 ne doit PAS être capturée par le préfixe dp1 (régression du 13/07)
    assert statuts["dp11"] == "a_generer"


def test_evaluer_integre_regime_et_completude():
    projet = Projet(nom="Test")
    projet.ombriere.puissance_kwc = 4000
    ev = evaluer(projet)
    assert ev["regime"]["regime"] == "PC"
    assert "completude" in ev


def test_surface_terrain_somme_contenances():
    loc = Localisation(
        parcelles=[
            Parcelle(section="30", numero="0464", contenance_m2=30201),
            Parcelle(section="30", numero="0465", contenance_m2=1000),
            Parcelle(section="30", numero="0466"),  # sans contenance
        ]
    )
    assert loc.surface_terrain_m2 == 31201
