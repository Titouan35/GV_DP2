"""Module Insertion IA (générateur de prompt SANS API) — tests offline.

Flux figé 16/07/2026 : plus d'appel Gemini. On teste l'assemblage déterministe
du prompt, la disponibilité du kit d'images, la conversion coupe PDF → PNG et
la génération de la fiche de validation d'emprise.
"""
import pytest
from PIL import Image

from app import config, fiche_emprise, insertion_ia


def test_apercu_sans_api():
    a = insertion_ia.apercu()
    assert a["mode"] == "generateur_prompt"
    assert a["api"] is False
    assert "ChatGPT" in a["cible"]
    assert a["mode_emploi"] and a["rappels"]


def test_prompt_reprend_type_consignes_affinage():
    projet = {
        "ombriere": {"famille": "START PLAINE Double", "nb_travees": 6,
                     "entraxe_m": 5.0, "nb_places": 60, "puissance_kwc": 500},
        "insertion": {"consignes": "garder le mât d'éclairage"},
    }
    prompt = insertion_ia.construire_prompt(projet, affinage="assombris les modules")
    # 6 blocs identifiables
    for bloc in ("RÔLE", "LA SCÈNE", "L'OBJET", "ÉCHELLE", "LUMIÈRE", "CONSIGNES"):
        assert bloc in prompt
    assert "Double" in prompt          # libellé court de la coupe (Mono/Double)
    assert "full black" in prompt
    assert "2,50 m" in prompt              # repère d'échelle place de parking
    assert "60 places" in prompt
    assert "garder le mât d'éclairage" in prompt
    assert "assombris les modules" in prompt
    assert "watermark" in prompt           # contraintes négatives


def test_prompt_projet_vide_ne_plante_pas():
    prompt = insertion_ia.construire_prompt({})
    assert "RÔLE" in prompt and "ombrière" in prompt.lower()


def test_kit_disponibilite(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROJETS_DIR", tmp_path)
    projet = {"id": "proj-x", "ombriere": {"famille": "START PLAINE Double"},
              "documents": {}, "insertion": {}}
    kit = {it["role"]: it for it in insertion_ia.kit(projet)}
    assert kit["photo"]["disponible"] is False        # pas de photo
    assert kit["plan"]["disponible"] is False          # pas de DP2
    assert kit["coupe"]["disponible"] is True          # coupe du catalogue existe


def test_image_kit_coupe_convertit_pdf_en_png(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROJETS_DIR", tmp_path)
    projet = {"id": "proj-x", "ombriere": {"famille": "START PLAINE Double"}}
    chemin = insertion_ia.image_kit(projet, "coupe")
    assert chemin is not None and chemin.exists()
    with Image.open(chemin) as im:
        assert im.size[0] > 100 and im.size[1] > 100   # rendu non trivial


def test_etat_reflete_inputs(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROJETS_DIR", tmp_path)
    e = insertion_ia.etat({"id": "p", "ombriere": {"famille": "START PLAINE Bas"},
                           "insertion": {}})
    assert e["photo"] is False
    assert e["coupe"] is True
    assert e["prete"] is False


def test_fiche_sans_image_retenue_leve(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROJETS_DIR", tmp_path)
    with pytest.raises(ValueError, match="retenue"):
        fiche_emprise.generer_fiche({"id": "p", "insertion": {}})


def test_fiche_generee_avec_insertion(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROJETS_DIR", tmp_path)
    assets = config.assets_dir("proj-x") / "insertion"
    assets.mkdir(parents=True, exist_ok=True)
    img = assets / "insertion_test.png"
    Image.new("RGB", (320, 180), (40, 60, 80)).save(img)
    rel = "proj-x.assets/insertion/insertion_test.png"
    projet = {"id": "proj-x", "nom": "Essai",
              "localisation": {"commune": "Soufflenheim"},
              "insertion": {"retenue": rel}}
    chemin = fiche_emprise.generer_fiche(projet)
    assert chemin.exists() and chemin.suffix == ".pptx"
