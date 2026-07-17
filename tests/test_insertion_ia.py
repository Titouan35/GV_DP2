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


def test_prompt_plan_mentionne_fleche_et_poteaux(tmp_path, monkeypatch):
    """Avec un DP2, le prompt impose la flèche de pente et les repères poteaux."""
    monkeypatch.setattr(config, "PROJETS_DIR", tmp_path)
    uploads = tmp_path / "p.assets" / "uploads"
    uploads.mkdir(parents=True)
    Image.new("RGB", (80, 60), (250, 250, 250)).save(uploads / "dp2.png")
    projet = {"id": "p", "ombriere": {"famille": "START PLAINE Bas"},
              "documents": {"dp2": {"fichier": "p.assets/uploads/dp2.png"}}}
    prompt = insertion_ia.construire_prompt(projet)
    assert "FLÈCHE" in prompt and "sens de la pente" in prompt
    assert "POTEAUX" in prompt


def test_prompt_projet_vide_ne_plante_pas():
    prompt = insertion_ia.construire_prompt({})
    assert "RÔLE" in prompt and "ombrière" in prompt.lower()


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
