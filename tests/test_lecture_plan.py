"""Lecture du plan de masse (cartouche gabarit GREENVOLT NEXT FRANCE)."""
from pathlib import Path

import pytest

from app import config
from app.lecture_plan import analyser_texte, appliquer_lecture, lire_plan_masse
from app.models import Projet

# extrait réel du gabarit BE (GVNFR0178_PDM_revA, Carrefour Market Anse),
# coquilles du cartouche comprises (SUNGRROW, DESGINED)
CARTOUCHE = (
    "Local TGBT Trajet PL livraisons 10000 10000 10000 "
    "( encombrement 6 panneaux + joint 2mm) 10582 216 Panneaux Photovoltaïques "
    "1762x1134x30mm HAUT DE RAMPANT BAS DE RAMPANT 7516 6° "
    "Caractéristiques techniques Projet Ombrière : 216 modules AIKO-A490-MCE54Dw 490 Wc "
    "2 onduleurs SUNGRROW 50CX-P2 \"50kVA à cosô=1\" Puissance DC = 105.84 kWc "
    "Puissance AC nominale = 100 kVA Puissance AC max = 100 kVA "
    "0.00m +2.50m +3.50m DATE NAME SIGN TITLE: Carrefour Market ANSE DRAWN "
    "15-07-2026 HBE DESCRIPTION : PLAN DE MASSE / VRD DESGINED SCALE: 1: 200 "
    "DRAWING N°: GVNFR0178_PDM_revA"
)


def test_analyse_cartouche_greenvolt():
    lecture = analyser_texte(CARTOUCHE)
    assert lecture["puissance_kwc"] == pytest.approx(105.84)
    assert lecture["module_puissance_wc"] == 490
    assert lecture["module_dimensions"] == "1762 x 1134 mm"
    assert lecture["nb_modules"] == 216
    assert lecture["pente_deg"] == 6.0
    assert lecture["garde_au_sol_m"] == pytest.approx(2.50)
    assert lecture["hauteur_hors_tout_m"] == pytest.approx(3.50)
    assert lecture["echelle_plan"] == "1/200"
    assert lecture["reference_plan"] == "GVNFR0178_PDM_revA"


def test_analyse_texte_vide():
    assert analyser_texte("") == {}


def test_appliquer_ne_touche_pas_les_champs_saisis():
    projet = Projet(nom="Test")
    projet.ombriere.puissance_kwc = 999.0  # déjà saisie : ne pas écraser
    lecture = analyser_texte(CARTOUCHE)
    remplis = appliquer_lecture(projet, lecture)
    assert projet.ombriere.puissance_kwc == 999.0
    assert "puissance_kwc" not in remplis
    assert projet.ombriere.pente_deg == 6.0          # champ vide : rempli
    assert "pente_deg" in remplis
    assert projet.meta["plan_lecture"]["puissance_kwc"] == pytest.approx(105.84)


def test_lecture_fichier_image_est_vide(tmp_path):
    """Un plan importé en PNG/JPG (sans couche texte) donne une lecture vide."""
    f = tmp_path / "plan.png"
    f.write_bytes(b"fake")
    assert lire_plan_masse(f) == {}


PLAN_EXEMPLE = config.REPO_ROOT.parent / "EXEMPLE" / "GVNFERXXXX_Carrefour Market Anse_PDM_revA.pdf"


@pytest.mark.skipif(not PLAN_EXEMPLE.exists(), reason="PDF exemple absent (poste sans OneDrive)")
def test_lecture_pdf_reel():
    lecture = lire_plan_masse(PLAN_EXEMPLE)
    assert lecture["puissance_kwc"] == pytest.approx(105.84)
    assert lecture["echelle_plan"] == "1/200"
