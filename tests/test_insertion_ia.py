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


def test_prompt_v5_methode_google():
    """Prompt v5 : verbe fort, images décrites sans numéro, formulation positive."""
    projet = {"ombriere": {"garde_au_sol_m": 2.5, "hauteur_hors_tout_m": 3.5}}
    guides = {"segments": [[[0.2, 0.5], [0.7, 0.55]], [[0.15, 0.65], [0.5, 0.68]]],
              "calibrage": {"a": [0.0, 0.0], "b": [0.1, 0.0],
                            "distance_m": 2.5, "libelle": "largeur d'une place"}}
    prompt = insertion_ia.construire_prompt(
        projet, plan_infos={"dims_m": [(18.8, 8.1), (13.1, 4.9)]}, guides=guides,
        idx={"photo": 1, "coupe": 2}, coupe_be=True)
    # verbe fort + nombre exact d'ombrières
    assert prompt.startswith("Insère 2 ombrières")
    # placement par les axes magenta, sans numéroter les images
    assert "trait magenta" in prompt and "chaque trait magenta" in prompt
    assert "IMAGE 1" not in prompt and "IMAGE 2" not in prompt
    assert "trait jaune mesure 2,5 m" in prompt and "largeur d'une place" in prompt
    assert "18,8 m x 8,1 m ; 13,1 m x 4,9 m" in prompt
    # structure décrite par son rôle (coupe BE), pas numérotée
    assert "coupe technique du projet" in prompt and "bureau d'études" in prompt
    assert "2,5 m de haut au point bas" in prompt
    # formulation POSITIVE : keep-explicit, pas d'interdits en rafale
    assert "rigoureusement identique" in prompt
    assert "grand-angle" in prompt and "lignes de fuite" in prompt
    assert "ne peins" not in prompt and "aucune couleur" not in prompt
    assert "photographie plein cadre" in prompt


def test_prompt_v5_une_ombriere_et_sans_calage():
    projet = {"insertion": {"photo": "p", "photos": ["p"],
              "guides": {"p": {"segments": [[[0.2, 0.5], [0.7, 0.55]]], "calibrage": None}}}}
    guides = insertion_ia.guides_actifs(projet)
    prompt = insertion_ia.construire_prompt(projet, guides=guides, idx={"photo": 1})
    assert prompt.startswith("Insère une ombrière")
    assert "le long du trait magenta" in prompt
    # sans repère : placement libre
    p2 = insertion_ia.construire_prompt({}, idx={"photo": 1})
    assert "zone de stationnement la plus dégagée" in p2


def test_prompt_v5_consignes_et_affinage():
    projet = {"insertion": {"consignes": "garder le mât d'éclairage"}}
    prompt = insertion_ia.construire_prompt(projet, affinage="assombris les modules")
    assert "garder le mât d'éclairage." in prompt
    assert "assombris les modules." in prompt


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
