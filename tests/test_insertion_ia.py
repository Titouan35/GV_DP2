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


def test_prompt_v3_legende_du_plan():
    """Le prompt explique les conventions du plan de masse (validé 17/07)."""
    projet = {"ombriere": {"famille": "START PLAINE Double", "entraxe_m": 10.0,
                           "garde_au_sol_m": 2.5, "hauteur_hors_tout_m": 3.5,
                           "module_dimensions": "1762 x 1134 mm"}}
    prompt = insertion_ia.construire_prompt(
        projet,
        plan_infos={"echelle": "1/200", "dims_m": [(17.9, 5.4)]},
        idx={"photo": 1, "plan": 2, "coupe": 3})
    assert "LE PLAN DE MASSE (image 2)" in prompt
    assert "photo aérienne" in prompt
    assert "à l'échelle 1/200" in prompt
    assert "zone bleue quadrillée" in prompt and "calepinage" in prompt
    assert "17,9 m x 5,4 m" in prompt
    assert "traits rouges" in prompt and "espacées de 10 m" in prompt
    assert "carrés gris" in prompt and "fondations" in prompt
    assert "HAUT DE RAMPANT" in prompt and "descend du bord HAUT" in prompt
    assert "ignore-le" in prompt          # cartouche/raccordements
    assert "PLACEMENT" in prompt and "mêmes repères" in prompt
    assert "arbres" in prompt and "hors emprise" in prompt
    # structure catalogue décrite (pas de coupe BE)
    assert "poteau central unique" in prompt
    assert "2,5 m au point bas et 3,5 m au point haut" in prompt
    assert "acier galvanisé nu" in prompt and "noirs mats" in prompt
    assert "RENDU" in prompt and "sans aucun texte ni tracé" in prompt


def test_prompt_v3_coupe_be_prime():
    """Avec une DP3 importée du BE, on ne décrit pas le profil catalogue."""
    projet = {"ombriere": {"famille": "START PLAINE Double", "pente_deg": 6}}
    prompt = insertion_ia.construire_prompt(
        projet, idx={"photo": 1, "coupe": 2}, coupe_be=True)
    assert "dessinée par le bureau d'études" in prompt
    assert "poteau central unique" not in prompt   # la coupe BE fait foi
    assert "pente 6°" in prompt


def test_prompt_v3_consignes_et_affinage():
    projet = {"ombriere": {"famille": "START PLAINE Bas"},
              "insertion": {"consignes": "garder le mât d'éclairage"}}
    prompt = insertion_ia.construire_prompt(projet, affinage="assombris les modules")
    assert "garder le mât d'éclairage." in prompt
    assert "assombris les modules." in prompt
    assert "bandes bleues" not in prompt
    assert "travées" not in prompt


def test_prompt_projet_vide_ne_plante_pas():
    prompt = insertion_ia.construire_prompt({})
    assert "Modifie la photo" in prompt and "ombrière" in prompt.lower()


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
