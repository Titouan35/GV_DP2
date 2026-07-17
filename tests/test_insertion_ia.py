"""Module Insertion IA (génération Gemini) — tests offline.

Flux 17/07/2026 : génération directe via l'API. On teste l'assemblage
déterministe du prompt (dont flèche de pente + poteaux du plan de masse),
la résolution des images d'entrée, le compteur de dépense local et la
fiche de validation d'emprise. Aucun appel réseau.
"""
import pytest
from PIL import Image

from app import config, fiche_emprise, insertion_ia


def test_apercu_expose_cout_et_modele():
    a = insertion_ia.apercu()
    assert "api_configuree" in a
    assert a["api_modele"]
    assert a["cout_image_eur"] > 0
    assert a["images_global"] >= 0


def test_prompt_v4_emplacement_double_vue():
    """Prompt v4 : photo + emprise magenta + aérienne + coupe BE, ultra-cadré."""
    projet = {"ombriere": {"garde_au_sol_m": 2.5, "hauteur_hors_tout_m": 3.5,
                           "pente_deg": 6}}
    guides = {"emprises": [[[0.1, 0.2], [0.3, 0.2], [0.3, 0.4], [0.1, 0.4]]],
              "calibrage": {"a": [0.0, 0.0], "b": [0.1, 0.0],
                            "distance_m": 2.5, "libelle": "largeur d'une place"}}
    prompt = insertion_ia.construire_prompt(
        projet, plan_infos={"dims_m": [(18.8, 8.1)]}, guides=guides,
        idx={"photo": 1, "photo_emprise": 2, "aerienne": 3, "coupe": 4},
        coupe_be=True)
    assert prompt.startswith("Insère une ombrière")
    assert "IMAGE 2" in prompt and "MAGENTA" in prompt
    assert "segment JAUNE mesure 2,5 m" in prompt and "largeur d'une place" in prompt
    assert "IMAGE 3 = vue aérienne" in prompt and "DESCENTE" in prompt
    assert "18,8 m x 8,1 m" in prompt
    assert "repères de travail" in prompt
    assert "arbres" in prompt and "retire-les" in prompt
    assert "IMAGE 4 = la coupe technique du projet" in prompt
    assert "bureau d'études" in prompt
    assert "2,5 m au point bas" in prompt and "Pente 6°" in prompt
    assert "PERSPECTIVE" in prompt and "points de fuite" in prompt
    assert "poteaux strictement verticaux" in prompt
    assert "RENDU" in prompt and "bitume reste nu" in prompt
    # plus de légende du plan brut (il n'est plus joint)
    assert "PLAN DE MASSE" not in prompt and "cartouche" not in prompt


def test_prompt_v4_sans_calage():
    """Sans emprise ni aérienne : placement libre mais cadré."""
    prompt = insertion_ia.construire_prompt({}, idx={"photo": 1})
    assert "zone de stationnement la plus cohérente" in prompt
    assert "PERSPECTIVE" in prompt and "RENDU" in prompt


def test_prompt_v4_consignes_et_affinage():
    projet = {"insertion": {"consignes": "garder le mât d'éclairage"}}
    prompt = insertion_ia.construire_prompt(projet, affinage="assombris les modules")
    assert "garder le mât d'éclairage." in prompt
    assert "assombris les modules." in prompt
    assert "bandes bleues" not in prompt


def test_prompt_projet_vide_ne_plante_pas():
    prompt = insertion_ia.construire_prompt({})
    assert "Insère une ombrière" in prompt


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


def test_compteur_global_incremente(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROJETS_DIR", tmp_path)
    assert insertion_ia.compteur_global() == 0
    assert insertion_ia.incrementer_compteur_global() == 1
    assert insertion_ia.incrementer_compteur_global() == 2
    assert insertion_ia.compteur_global() == 2


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
