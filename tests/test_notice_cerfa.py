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
    chemin, champs, avertissements = preremplir(PROJET)
    assert chemin.exists()
    assert len(champs) >= 12
    assert avertissements == []   # 1 parcelle : rien à signaler

    lus = PdfReader(chemin).get_fields()
    assert lus["D2D_denomination"].get("/V") == "Greenvolt Next France"
    assert lus["T2S_section"].get("/V") == "30"
    assert lus["T2N_numero"].get("/V") == "0464"
    assert lus["T2T_superficie"].get("/V") == "30201"
    assert lus["C2ZE1_puissance"].get("/V") == "500"
    desc = lus["C2ZD1_description"].get("/V") or ""
    assert "START PLAINE Double" in desc
    # nb_travees + entraxe saisis : la longueur (6 x 5 = 30 m) est affirmée
    assert "30 m x 10 m" in desc


def test_cerfa_sans_cotes_saisies_naffirme_rien(tmp_path, monkeypatch):
    """Sans longueur/travées/entraxe saisis, aucune cote fabriquée (20 m) ne
    doit apparaître dans la description du Cerfa (plan §13)."""
    monkeypatch.setattr(config, "PROJETS_DIR", tmp_path)
    projet = {**PROJET, "ombriere": {"famille": "START PLAINE Bas",
                                     "puissance_kwc": 500}}
    chemin, champs, _ = preremplir(projet)
    desc = (PdfReader(chemin).get_fields()["C2ZD1_description"].get("/V") or "")
    assert "20 m" not in desc                 # 4 travées x 5 m : défaut fabriqué
    assert "5 m de profondeur" in desc        # cote réelle du type, elle, oui


def test_cerfa_refuse_regime_pc(tmp_path, monkeypatch):
    """>= 3 MWc => PC : générer le Cerfa DP serait un dossier erroné."""
    monkeypatch.setattr(config, "PROJETS_DIR", tmp_path)
    projet = {**PROJET, "ombriere": {**PROJET["ombriere"], "puissance_kwc": 3500}}
    with pytest.raises(ValueError, match="Permis de Construire"):
        preremplir(projet)


def test_cerfa_avertit_au_dela_de_3_parcelles(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROJETS_DIR", tmp_path)
    parcelles = [{"section": "30", "numero": f"04{i:02d}", "com_abs": "000"}
                 for i in range(5)]
    projet = {**PROJET, "localisation": {**PROJET["localisation"],
                                         "parcelles": parcelles}}
    _, _, avertissements = preremplir(projet)
    assert any("5 parcelles" in a for a in avertissements)
