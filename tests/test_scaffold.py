"""Scaffold : l'ombrière posée en volume plein sur la photo (mode 20/07/2026).

On ne décrit plus la géométrie au modèle, on la DESSINE : ces tests vérifient
que le volume est réellement posé, que les poteaux sont du bon côté selon le
type, et que le prompt bascule bien en mode « habillage ».
"""
from __future__ import annotations

import numpy as np
import pytest
from PIL import Image

from app import config, insertion_ia, scaffold


def _projet(tmp_path, famille="START PLAINE Double", bord=None, L=30.0, prof=20.0):
    dossier = tmp_path / "p.assets" / "insertion"
    dossier.mkdir(parents=True)
    rng = np.random.default_rng(4)
    # photo claire (bitume ensoleillé) : le volume sombre doit s'y détacher
    Image.fromarray(rng.integers(150, 205, (900, 1200, 3), dtype=np.uint8)).save(
        dossier / "site.png")
    rel = "p.assets/insertion/site.png"
    return {
        "id": "p", "ombriere": {"famille": famille},
        "insertion": {"photo": rel, "photos": [rel], "poses": {rel: {
            "hauteur_vue": 1.6,
            "ombrieres": [{"bord_avant": bord or [[0.03, 0.95], [0.75, 0.80]],
                           "famille": famille, "longueur_m": L,
                           "profondeur_m": prof, "pente_vers": "fond"}]}}},
    }


def _sombres(chemin) -> np.ndarray:
    """Masque des pixels nettement plus sombres que le fond de la photo."""
    arr = np.asarray(Image.open(chemin).convert("RGB")).astype(int)
    return arr.mean(axis=2) < 120


def test_scaffold_pose_un_volume_visible(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROJETS_DIR", tmp_path)
    projet = _projet(tmp_path)
    chemin = insertion_ia.scaffold_photo(projet)
    assert chemin and chemin.exists() and chemin.name == "scaffold.png"
    sombre = _sombres(chemin)
    # la structure couvre une part significative de l'image, et vers le bas
    assert sombre.mean() > 0.02
    ys = np.nonzero(sombre.any(axis=1))[0]
    assert ys.max() > 0.6 * 900          # le volume est bien dans la moitié basse


def test_scaffold_absent_sans_cote(tmp_path, monkeypatch):
    """Sans longueur, pas de perspective calibrée donc pas de volume : on
    retombe sur la photo repérée plutôt que d'inventer une géométrie."""
    monkeypatch.setattr(config, "PROJETS_DIR", tmp_path)
    projet = _projet(tmp_path, L=None)
    projet["insertion"]["poses"]["p.assets/insertion/site.png"]["ombrieres"][0].pop("longueur_m")
    assert insertion_ia.scaffold_photo(projet) is None


@pytest.mark.parametrize("famille,attendu", [
    ("START PLAINE Double", 0.5),   # file centrale
    ("START PLAINE Bas", 1.0),      # poteau côté haut + pente vers le fond -> au fond
    ("START PLAINE Haut", 0.0),     # poteau côté bas -> devant
])
def test_position_des_poteaux_selon_le_type(tmp_path, monkeypatch, famille, attendu):
    monkeypatch.setattr(config, "PROJETS_DIR", tmp_path)
    projet = _projet(tmp_path, famille=famille)
    o = insertion_ia.poses_actives(projet)[0]
    assert insertion_ia._position_poteaux(o, projet) == attendu


def test_pente_inversee_change_le_cote_des_poteaux(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROJETS_DIR", tmp_path)
    projet = _projet(tmp_path, famille="START PLAINE Bas")
    entree = projet["insertion"]["poses"]["p.assets/insertion/site.png"]
    entree["ombrieres"][0]["pente_vers"] = "avant"
    o = insertion_ia.poses_actives(projet)[0]
    assert insertion_ia._position_poteaux(o, projet) == 0.0


def test_mode_scaffold_et_prompt_habillage(tmp_path, monkeypatch):
    """Le lot envoyé part du scaffold, et le prompt ne décrit plus la
    géométrie : il ne demande QUE l'habillage."""
    monkeypatch.setattr(config, "PROJETS_DIR", tmp_path)
    projet = _projet(tmp_path)
    req = insertion_ia._preparer_requete_pose(projet)
    assert req["mode"] == "scaffold"
    assert req["chemins"][0].name == "scaffold.png"
    p = req["prompt"]
    assert "GEOMETRIE VERROUILLEE" in p and "volume" in p
    assert "SOUS-FACE" in p
    # la géométrie n'est plus décrite : ni cotes, ni orientation, ni règle de toiture
    assert "PROPORTIONS" not in p and "ORIENTATION." not in p
    assert "m de long" not in p
    # nettement plus court que le prompt de pose
    assert len(p.split()) < 320


def test_scaffold_desactivable(tmp_path, monkeypatch):
    """GVDP_SCAFFOLD=0 rebascule sur l'ancien mode (comparaison A/B)."""
    monkeypatch.setattr(config, "PROJETS_DIR", tmp_path)
    monkeypatch.setenv("GVDP_SCAFFOLD", "0")
    projet = _projet(tmp_path)
    req = insertion_ia._preparer_requete_pose(projet)
    assert req["mode"] == "pose"
    assert "PROPORTIONS" in req["prompt"]


def test_poser_sans_volume_rend_la_photo_intacte(tmp_path):
    """Aucun volume calculable : l'image ressort telle quelle (pas de dessin
    fantaisiste sur la photo du client)."""
    image = Image.new("RGB", (200, 150), (180, 180, 180))
    assert scaffold.poser(image, [None], [{}]) is image
