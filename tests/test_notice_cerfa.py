"""Notice DP11 (gabarit déterministe) et pré-remplissage du Cerfa 16702."""
import pytest
from pypdf import PdfReader

from app import config
from app.cerfa import GABARIT, preremplir
from app.notice import SECTIONS, generer_sections

PROJET = {
    "id": "test-cerfa-abc123",
    "nom": "Golf de Soufflenheim — ombrières parking",
    "mo": {
        "type": "Société",
        "raison_sociale": "Greenvolt Next France",
        "representant": "Florent Guillemin",
        "siret": "12345678900012",
        "adresse": "1 rue de l'Énergie, 69000 Lyon",
    },
    "localisation": {
        "adresse": "Allée du Golf 67620 Soufflenheim",
        "code_postal": "67620",
        "commune": "Soufflenheim",
        "code_insee": "67472",
        "parcelles": [
            {"section": "30", "numero": "0464", "com_abs": "000", "contenance_m2": 30201},
        ],
    },
    "urbanisme": {
        "zonage": {"disponible": True, "couvert": True,
                   "zones": [{"libelle": "UC3t", "libelong": "Zone urbaine UC3t"}]},
        "secteur_abf": False,
        "risques": {"disponible": True, "risques": [{"libelle": "Inondation"}]},
    },
    "ombriere": {"famille": "START PLAINE Double", "puissance_kwc": 500,
                 "nb_travees": 6, "entraxe_m": 5.0, "nb_places": 60},
    "documents": {},
    "notice": {"sections": {}},
}


def test_notice_sept_sections_remplies():
    sections = generer_sections(PROJET)
    assert set(sections) == {cle for cle, _ in SECTIONS}
    assert all(len(v) > 60 for v in sections.values())
    assert "Soufflenheim" in sections["presentation"]
    assert "30 0464" in sections["etat_initial"]
    assert "UC3t" in sections["reglementaire"]
    assert "Inondation" in sections["reglementaire"]
    assert "500 kWc" in sections["description"]


def test_notice_projet_minimal_sans_crash():
    sections = generer_sections({"nom": "X"})
    assert len(sections) == 7


def test_gabarit_cerfa_versionne():
    assert GABARIT.exists(), "gabarit officiel absent du repo"


def test_cerfa_prerempli(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROJETS_DIR", tmp_path)
    chemin, champs = preremplir(PROJET)
    assert chemin.exists()
    assert len(champs) >= 12

    lus = PdfReader(chemin).get_fields()
    assert lus["D2D_denomination"].get("/V") == "Greenvolt Next France"
    assert lus["T2S_section"].get("/V") == "30"
    assert lus["T2N_numero"].get("/V") == "0464"
    assert lus["T2T_superficie"].get("/V") == "30201"
    assert lus["C2ZE1_puissance"].get("/V") == "500"
    assert "START PLAINE Double" in (lus["C2ZD1_description"].get("/V") or "")
