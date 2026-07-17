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


def test_prompt_v6_mode_scaffold():
    """Mode scaffold : habiller le volume gris, sans le déplacer ; sans numéros ; positif."""
    projet = {"ombriere": {"garde_au_sol_m": 2.5, "hauteur_hors_tout_m": 3.5}}
    guides = {"segments": [[[0.2, 0.5], [0.7, 0.55]], [[0.15, 0.65], [0.5, 0.68]]],
              "calibrage": None}
    prompt = insertion_ia.construire_prompt(
        projet, plan_infos={"dims_m": [(18.8, 8.1), (13.1, 4.9)]}, guides=guides,
        idx={"photo": 1, "coupe": 2}, coupe_be=True, mode="scaffold", plan_ref=True)
    # verbe fort + consigne "habiller le volume gris à sa place"
    assert prompt.startswith("Transforme les formes grises")
    assert "garde-les rigoureusement identiques" in prompt
    assert "ne déplace pas" in prompt and "ne redimensionne pas" in prompt
    # pas de numérotation d'images
    assert "IMAGE 1" not in prompt and "image 2" not in prompt
    assert "18,8 m x 8,1 m ; 13,1 m x 4,9 m" in prompt
    # coupe (structure) décrite par son rôle
    assert "coupe technique du projet" in prompt and "bureau d'études" in prompt
    assert "2,5 m au point bas" in prompt
    # plan de masse remis en référence avec sa légende couleurs
    assert "zones bleues quadrillées sont les panneaux" in prompt
    assert "traits rouges la trame des poteaux" in prompt and "HAUT/BAS DE RAMPANT" in prompt
    # formulation POSITIVE
    assert "rigoureusement identique" in prompt
    assert "ne peins" not in prompt and "aucune couleur" not in prompt


def test_prompt_v6_mode_axes_et_libre():
    guides = {"segments": [[[0.2, 0.5], [0.7, 0.55]]], "calibrage": None}
    p_axes = insertion_ia.construire_prompt({}, guides=guides, idx={"photo": 1}, mode="axes")
    assert p_axes.startswith("Insère une ombrière")
    assert "trait magenta marque l'axe" in p_axes
    p_libre = insertion_ia.construire_prompt({}, idx={"photo": 1})
    assert "zone de stationnement la plus dégagée" in p_libre


def test_prompt_v6_consignes_et_affinage():
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
