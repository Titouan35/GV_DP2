"""Moteur de perspective (emprise projetée depuis un tracé) — offline."""
import math

import numpy as np
import pytest
from PIL import Image, ImageDraw

from app import config, insertion_ia
from app.perspective import (Camera, calibrer, focale_px, proposer_horizon,
                             volume_ombriere)

W, H, F = 1600, 1200, 1280.0


def _cam(h_cam=2.0, y_h=500.0):
    return Camera(W=W, H=H, f=F, y_h=y_h, h_cam=h_cam)


def test_aller_retour_sol_image():
    cam = _cam()
    for X0, Z0 in [(-5.0, 8.0), (3.0, 15.0), (0.0, 25.0), (7.5, 40.0)]:
        x, y = cam.sol_vers_image(X0, Z0)
        X1, Z1 = cam.image_vers_sol(x, y)
        assert abs(X1 - X0) < 1e-6 and abs(Z1 - Z0) < 1e-6


def test_horizon_est_la_limite():
    cam = _cam()
    _, y_loin = cam.sol_vers_image(0.0, 10_000.0)
    assert abs(y_loin - cam.y_h) < 1.0          # Z -> inf : y -> horizon
    assert cam.image_vers_sol(800, cam.y_h - 5) is None   # au-dessus : pas de sol


def test_calibration_retrouve_la_hauteur_camera():
    """On projette un bord de 10 m depuis une caméra à 2 m ; la calibration
    doit retrouver h_cam = 2 m depuis les pixels et la longueur réelle."""
    vraie = _cam(h_cam=2.0)
    a = vraie.sol_vers_image(-5.0, 12.0)
    b = vraie.sol_vers_image(5.0, 12.0)
    cam = calibrer(W, H, vraie.y_h, F, a, b, longueur_m=10.0)
    assert cam is not None
    assert abs(cam.h_cam - 2.0) < 1e-6


def test_calibration_refuse_geometrie_incoherente():
    # horizon SOUS le tracé : pas de sol là -> refus
    assert calibrer(W, H, 900.0, F, (400, 800), (1200, 820), 10.0) is None
    # longueur réelle absurde -> hauteur caméra hors bornes -> refus
    vraie = _cam(h_cam=2.0)
    a = vraie.sol_vers_image(-5.0, 12.0)
    b = vraie.sol_vers_image(5.0, 12.0)
    assert calibrer(W, H, vraie.y_h, F, a, b, longueur_m=500.0) is None


def test_volume_converge_en_perspective():
    """Le fond de l'emprise est PLUS COURT et PLUS HAUT dans l'image que le
    bord avant : c'est la convergence que Gemini devait deviner avant."""
    vraie = _cam(h_cam=1.8)
    a = vraie.sol_vers_image(-9.0, 10.0)
    b = vraie.sol_vers_image(9.0, 10.0)
    cam = calibrer(W, H, vraie.y_h, F, a, b, longueur_m=18.0)
    vol = volume_ombriere(cam, a, b, profondeur_m=5.0, h_avant=2.5, h_fond=3.5)
    assert vol is not None
    sol = vol["sol"]
    l_avant = math.hypot(sol[1][0] - sol[0][0], sol[1][1] - sol[0][1])
    l_fond = math.hypot(sol[2][0] - sol[3][0], sol[2][1] - sol[3][1])
    assert l_fond < l_avant * 0.97              # convergence réelle
    assert max(sol[2][1], sol[3][1]) < min(sol[0][1], sol[1][1])  # fond plus haut
    # la toiture est au-dessus du sol, et le point haut (fond, 3,5 m) monte plus
    assert all(t[1] < s[1] for t, s in zip(vol["toit"], sol))


def test_focale_exif_absente_donne_defaut(tmp_path):
    sans_exif = tmp_path / "sans_exif.png"
    Image.new("RGB", (100, 80)).save(sans_exif)
    assert focale_px(sans_exif, 1600) == 0.8 * 1600           # PNG sans EXIF
    from pathlib import Path
    assert focale_px(Path("inexistant.jpg"), 1600) == 0.8 * 1600


def test_proposer_horizon_lignes_convergentes(tmp_path):
    """Deux faisceaux de marquage convergent vers y = 0,3 H : la proposition
    doit tomber dans le voisinage (c'est une aide, pas une mesure exacte)."""
    im = Image.new("L", (960, 720), 235)
    dr = ImageDraw.Draw(im)
    fx, fy = 480, 216                            # point de fuite (0.3 * H)
    for x0 in range(-200, 1200, 90):
        dr.line([(x0, 720), (fx, fy)], fill=25, width=5)
    chemin = tmp_path / "parking.png"
    im.convert("RGB").save(chemin)
    y = proposer_horizon(chemin)
    assert y is not None and abs(y - 0.3) < 0.12


def test_proposer_horizon_image_plate_refuse(tmp_path):
    rng = np.random.default_rng(5)
    im = Image.fromarray(rng.integers(100, 140, (400, 600), dtype=np.uint8))
    chemin = tmp_path / "bruit.png"
    im.convert("RGB").save(chemin)
    assert proposer_horizon(chemin) is None


# ------------------------------------------------------------------ intégration

def _projet_pose(tmp_path, horizon=None):
    dossier = tmp_path / "p.assets" / "insertion"
    dossier.mkdir(parents=True)
    Image.new("RGB", (1600, 1200), (150, 150, 150)).save(dossier / "site.jpg")
    rel = "p.assets/insertion/site.jpg"
    entree = {"ombrieres": [{"bord_avant": [[0.2, 0.7], [0.8, 0.72]],
                             "famille": "START PLAINE Bas",
                             "longueur_m": 20.0, "profondeur_m": 5.0,
                             "pente_vers": "fond"}]}
    if horizon is not None:
        entree["horizon"] = horizon
    return {"id": "p", "ombriere": {},
            "insertion": {"photo": rel, "photos": [rel], "poses": {rel: entree}}}


def test_volumes_poses_et_photo_reperee(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROJETS_DIR", tmp_path)
    projet = _projet_pose(tmp_path, horizon=0.40)
    photo = config.PROJETS_DIR / projet["insertion"]["photo"]
    volumes = insertion_ia.volumes_poses(projet, 1600, 1200, photo)
    assert len(volumes) == 1 and volumes[0] is not None
    assert len(volumes[0]["sol"]) == 4 and len(volumes[0]["toit"]) == 4
    # la photo repérée porte le cadre d'emprise (des pixels magenta au-dessus
    # du bord avant : le fond du quadrilatère)
    reperee = insertion_ia.photo_reperee(projet)
    arr = np.asarray(Image.open(reperee).convert("RGB"))
    magenta = (arr[..., 0] > 200) & (arr[..., 1] < 90) & (arr[..., 2] > 140)
    ys = np.nonzero(magenta.any(axis=1))[0]
    assert magenta.sum() > 500
    assert ys.min() < 0.68 * 1200               # le fond monte au-dessus du bord


def test_volumes_sans_cote_indisponibles(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROJETS_DIR", tmp_path)
    projet = _projet_pose(tmp_path)
    projet["insertion"]["poses"]["p.assets/insertion/site.jpg"]["ombrieres"][0]["longueur_m"] = None
    photo = config.PROJETS_DIR / projet["insertion"]["photo"]
    assert insertion_ia.volumes_poses(projet, 1600, 1200, photo) == [None]


def test_zone_autorisee_couvre_le_volume(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROJETS_DIR", tmp_path)
    projet = _projet_pose(tmp_path, horizon=0.40)
    photo = config.PROJETS_DIR / projet["insertion"]["photo"]
    zone = insertion_ia.zone_autorisee(projet, 1600, 1200, photo)
    assert zone is not None and zone.shape == (1200, 1600)
    vol = insertion_ia.volumes_poses(projet, 1600, 1200, photo)[0]
    for x, y in vol["sol"] + vol["toit"]:
        assert zone[min(1199, max(0, round(y))), min(1599, max(0, round(x)))]
    assert not zone[20, 20]                      # le ciel reste interdit
    assert 0.02 < zone.mean() < 0.6              # ni vide ni tout-permis


def test_preserver_scene_zone_stricte(tmp_path, monkeypatch):
    """Une retouche HORS zone autorisée est annulée, une retouche dans la
    zone est conservée."""
    monkeypatch.setattr(config, "PROJETS_DIR", tmp_path)
    projet = _projet_pose(tmp_path, horizon=0.40)
    photo = config.PROJETS_DIR / projet["insertion"]["photo"]
    import io
    rng = np.random.default_rng(3)
    fond = rng.integers(90, 170, (1200, 1600, 3), dtype=np.uint8)
    Image.fromarray(fond).save(photo)            # photo texturée (diff exploitable)
    zone = insertion_ia.zone_autorisee(projet, 1600, 1200, photo)
    vol = insertion_ia.volumes_poses(projet, 1600, 1200, photo)[0]
    cx = int(sum(p[0] for p in vol["sol"]) / 4)
    cy = int(sum(p[1] for p in vol["sol"]) / 4)

    gen = fond.copy()
    gen[cy - 40:cy + 40, cx - 60:cx + 60] = (20, 22, 26)   # « l'ombrière » (dans la zone)
    gen[30:110, 30:170] = (250, 60, 60)                     # vandalisme du ciel (hors zone)
    buf = io.BytesIO()
    Image.fromarray(gen).save(buf, "PNG")

    sortie = insertion_ia.preserver_scene(photo, buf.getvalue(), zone=zone)
    res = np.asarray(Image.open(io.BytesIO(sortie)).convert("RGB"))
    assert res[cy - 20:cy + 20, cx - 30:cx + 30].mean() < 60          # ombrière gardée
    coin = res[40:100, 40:160].astype(int) - fond[40:100, 40:160].astype(int)
    assert abs(coin.mean()) < 3                                        # ciel restauré
