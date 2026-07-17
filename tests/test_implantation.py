"""Extraction de l'implantation (code couleur GVN) + préservation de scène."""
import io

import numpy as np
import pytest
from PIL import Image

from app import config, insertion_ia
from app.implantation import analyser_plan, generer_schema, resume

PLAN_ANSE = config.REPO_ROOT.parent / "EXEMPLE" / "GVNFERXXXX_Carrefour Market Anse_PDM_revA.pdf"

anse_requis = pytest.mark.skipif(not PLAN_ANSE.exists(),
                                 reason="PDF exemple absent (poste sans OneDrive)")


@anse_requis
def test_analyse_plan_anse():
    a = analyser_plan(PLAN_ANSE)
    assert a is not None
    assert len(a["rangees"]) == 1                    # une rangée dessinée
    z = a["rangees"][0]
    assert 40 <= z["angle_deg"] <= 60                # rangée inclinée ~50°
    assert z["longueur_m"] > z["largeur_m"] > 2      # cotes réelles via 1/200
    assert a["pente_haut_px"] and a["pente_bas_px"]  # étiquettes localisées
    # HAUT est au nord-est de BAS sur ce plan (rotation 270 gérée)
    assert a["pente_haut_px"][0] > a["pente_bas_px"][0]
    assert a["pente_haut_px"][1] < a["pente_bas_px"][1]
    assert "rangée" in resume(a)


@anse_requis
def test_schema_genere(tmp_path):
    chemin = generer_schema(PLAN_ANSE, tmp_path / "schema.png")
    assert chemin and chemin.exists()
    with Image.open(chemin) as im:
        arr = np.asarray(im.convert("RGB"))
    # le schéma contient du bleu (panneaux) et du blanc (fond épuré)
    bleu = (arr[..., 2] > 150) & (arr[..., 2] > arr[..., 0] + 30)
    assert bleu.mean() > 0.02
    blanc = (arr > 245).all(axis=2)
    assert blanc.mean() > 0.4


def test_analyse_image_non_pdf(tmp_path):
    f = tmp_path / "plan.png"
    f.write_bytes(b"fake")
    assert analyser_plan(f) is None


def _png(im: Image.Image) -> bytes:
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def test_preserver_scene_restaure_hors_zone(tmp_path):
    """Un carré ajouté est conservé, une dérive légère ailleurs est annulée."""
    rng = np.random.default_rng(7)
    fond = rng.integers(90, 160, (300, 400, 3), dtype=np.uint8)
    orig = Image.fromarray(fond)
    chemin = tmp_path / "orig.png"
    orig.save(chemin)

    gen = fond.copy()
    gen[100:200, 150:300] = (20, 20, 25)             # « l'ombrière »
    derive = np.clip(gen.astype(int) + 6, 0, 255).astype(np.uint8)  # dérive globale
    sortie = insertion_ia.preserver_scene(chemin, _png(Image.fromarray(derive)))

    res = np.asarray(Image.open(io.BytesIO(sortie)).convert("RGB"))
    # l'ombrière construite est toujours là (sombre)
    assert res[130:170, 200:260].mean() < 60
    # loin de la zone, les pixels d'origine sont revenus (dérive annulée)
    coin = res[10:60, 10:60].astype(int) - fond[10:60, 10:60].astype(int)
    assert abs(coin.mean()) < 2


def test_preserver_scene_cadrage_different(tmp_path):
    """Ratio d'image différent : on rend l'image générée sans y toucher."""
    orig = Image.new("RGB", (400, 300), (100, 100, 100))
    chemin = tmp_path / "orig.png"
    orig.save(chemin)
    gen = _png(Image.new("RGB", (400, 200), (50, 50, 50)))
    assert insertion_ia.preserver_scene(chemin, gen) == gen
