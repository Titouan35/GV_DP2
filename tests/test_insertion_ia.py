"""Module Insertion IA : statut, construction du prompt, appel Gemini (mocké)."""
import base64
import io

import pytest
from PIL import Image

from app import config, insertion_ia
from app.insertion_ia import InsertionError, _extraire_image, _prompt_systeme, statut


def _png_base64() -> str:
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (10, 20, 30)).save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode()


def test_statut_sans_cle(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("GVDP_IMAGE_PROVIDER", "gemini")
    s = statut()
    assert s["fournisseur"] == "gemini"
    assert s["configure"] is False
    assert any("aistudio" in etape for etape in s["guide"])


def test_statut_avec_cle(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "AIzaTESTKEY")
    monkeypatch.setenv("GVDP_IMAGE_PROVIDER", "gemini")
    assert statut()["configure"] is True


def test_prompt_reprend_type_et_consignes():
    projet = {
        "ombriere": {"famille": "START PLAINE Double", "nb_travees": 6, "entraxe_m": 5.0},
        "insertion": {"repere_distance_m": 2.5, "repere_desc": "une place",
                      "consignes": "garder le mât d'éclairage"},
    }
    prompt = _prompt_systeme(projet, avec_zone=True, avec_plan=True, avec_coupe=True)
    assert "zone rouge" in prompt
    assert "START PLAINE Double" in prompt
    assert "full black" in prompt
    assert "2,5 m" in prompt or "2.5 m" in prompt
    assert "mât d'éclairage" in prompt
    assert "PLAN DE MASSE" in prompt
    assert "COUPE" in prompt


def test_extraire_image_ok():
    reponse = {"candidates": [{"content": {"parts": [
        {"inlineData": {"mimeType": "image/png", "data": _png_base64()}},
    ]}}]}
    assert _extraire_image(reponse)[:4] == b"\x89PNG"


def test_extraire_image_snake_case():
    reponse = {"candidates": [{"content": {"parts": [
        {"inline_data": {"mime_type": "image/png", "data": _png_base64()}},
    ]}}]}
    assert _extraire_image(reponse)[:4] == b"\x89PNG"


def test_extraire_image_blocage_securite():
    reponse = {"candidates": [{"content": {"parts": [
        {"text": "Contenu bloqué pour raison de sécurité."},
    ]}}]}
    with pytest.raises(InsertionError, match="Aucune image"):
        _extraire_image(reponse)


def test_generer_mocke(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "PROJETS_DIR", tmp_path)
    monkeypatch.setenv("GEMINI_API_KEY", "AIzaTESTKEY")
    monkeypatch.setenv("GVDP_IMAGE_PROVIDER", "gemini")

    # photo du site sur le disque
    assets = config.assets_dir("proj-x") / "insertion"
    assets.mkdir(parents=True, exist_ok=True)
    photo = assets / "photo_site.jpg"
    Image.new("RGB", (64, 48), (120, 120, 120)).save(photo, "JPEG")

    appels = {"n": 0}

    def faux_appel(parts):
        appels["n"] += 1
        # au moins une image (photo annotée) doit être transmise
        assert any("inline_data" in p for p in parts)
        buf = io.BytesIO()
        Image.new("RGB", (32, 24), (0, 0, 0)).save(buf, "PNG")
        return buf.getvalue()

    monkeypatch.setattr(insertion_ia, "_appel_gemini", faux_appel)

    projet = {
        "id": "proj-x",
        "ombriere": {"famille": "START PLAINE Bas"},
        "documents": {},
        "insertion": {
            "photo": "proj-x.assets/insertion/photo_site.jpg",
            "zone": [0.2, 0.4, 0.7, 0.6],
            "repere_distance_m": 2.5, "repere_desc": "une place", "consignes": "",
        },
    }
    variantes = insertion_ia.generer(projet, nb_variantes=2, horodatage="20260713-120000")
    assert len(variantes) == 2
    assert appels["n"] == 2
    for v in variantes:
        assert (tmp_path / v["fichier"]).exists()
        assert "visuel IA" in v["etiquette"]


def test_generer_sans_cle_refuse(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "PROJETS_DIR", tmp_path)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(InsertionError, match="configuré|GEMINI"):
        insertion_ia.generer({"id": "x", "insertion": {"photo": "p"}}, nb_variantes=1)
