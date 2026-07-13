"""Catalogue paramétrique + génération offline des dessins DP3/DP4."""
import pytest

from app.catalogue import CATALOGUE, parametres_effectifs
from app.planches import base
from app.planches.ombriere import dessiner_coupe, dessiner_facades


def test_catalogue_trois_familles():
    assert set(CATALOGUE) == {"START PLAINE Bas", "START PLAINE Haut", "START PLAINE Double"}
    assert CATALOGUE["START PLAINE Double"]["poteau"] == "central"


def test_parametres_defauts_bas():
    p = parametres_effectifs({"famille": "START PLAINE Bas"})
    assert p["poteau"] == "haut"
    assert p["profondeur_m"] == 5.0
    assert p["longueur_m"] == pytest.approx(4 * 5.0)


def test_parametres_saisie_prioritaire():
    p = parametres_effectifs({
        "famille": "START PLAINE Double",
        "largeur_m": 12.0,
        "pente_deg": 5.0,
        "garde_au_sol_m": 2.5,
        "nb_travees": 6,
        "entraxe_m": 5.5,
    })
    assert p["profondeur_m"] == 12.0
    assert p["h_bas_m"] == 2.5
    # h_haut dérivée de la pente : 2,5 + 12·tan(5°) ≈ 3,55
    assert p["h_haut_m"] == pytest.approx(3.55, abs=0.02)
    assert p["longueur_m"] == pytest.approx(33.0)


def test_hauteur_hors_tout_saisie_gagne():
    p = parametres_effectifs({"famille": "START PLAINE Bas", "hauteur_hors_tout_m": 4.2})
    assert p["h_haut_m"] == 4.2


PROJET_MIN = {
    "nom": "Test",
    "localisation": {"commune": "Soufflenheim", "code_insee": "67472"},
}


@pytest.mark.parametrize("famille", list(CATALOGUE))
def test_coupe_et_facades_se_dessinent(famille):
    projet = {**PROJET_MIN, "ombriere": {"famille": famille, "nb_travees": 4, "entraxe_m": 5.0}}
    for fn in (dessiner_coupe, dessiner_facades):
        img = fn(projet)
        assert img.size == (base.PLATE_W, base.PLATE_H)


def test_echelle_nominale_format():
    assert base.echelle_nominale(2000 / base.PX_PAR_MM / 1000).startswith("1/2 000")
