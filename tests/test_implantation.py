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


def test_decadrer_retire_l_habillage_type_plan(tmp_path):
    """Photo montée servie dans un gabarit A3 (cadre blanc + cartouche) : on
    recadre sur la photo ; une image déjà plein cadre repart intacte."""
    rng = np.random.default_rng(3)
    orig = Image.fromarray(rng.integers(60, 170, (300, 400, 3), dtype=np.uint8))
    chemin = tmp_path / "orig.png"
    orig.save(chemin)

    # gabarit 842x595 : photo 4:3 en haut, cartouche texte en bas, cadre blanc
    gabarit = np.full((595, 842, 3), 255, dtype=np.uint8)
    gabarit[20:440, 41:601] = rng.integers(60, 170, (420, 560, 3), dtype=np.uint8)
    for y in range(470, 580, 14):  # lignes fines du cartouche
        gabarit[y:y + 2, 60:800] = 40
    sortie = insertion_ia.decadrer(chemin, _png(Image.fromarray(gabarit)))
    res = Image.open(io.BytesIO(sortie))
    assert (res.width, res.height) == (560, 420)   # recadré sur la photo

    plein_cadre = _png(Image.fromarray(rng.integers(60, 170, (300, 400, 3), dtype=np.uint8)))
    assert insertion_ia.decadrer(chemin, plein_cadre) == plein_cadre


def test_preserver_scene_garde_une_structure_de_meme_luminance(tmp_path):
    """Régression du 20/07/2026 : une ombrière galvanisée mate devant un ciel
    bleu a presque la même LUMINANCE que le fond. L'ancienne diff en niveaux
    de gris ne la voyait pas et la remplaçait par la photo d'origine, d'où des
    rendus translucides. La diff en couleur doit la conserver."""
    rng = np.random.default_rng(3)
    fond = np.zeros((300, 400, 3), dtype=np.float32)
    fond[:180] = [120, 170, 235]        # ciel bleu
    fond[180:] = [110, 108, 105]        # bitume
    fond = np.clip(fond + rng.normal(0, 6, fond.shape), 0, 255).astype(np.uint8)
    chemin = tmp_path / "orig.png"
    Image.fromarray(fond).save(chemin)

    gen = fond.astype(np.float32).copy()
    gen[100:150, 80:320] = [168, 175, 182]      # galva mat, luminance ~ celle du ciel
    gen = np.clip(gen + 8, 0, 255).astype(np.uint8)   # + éclaircissement du modèle

    res = np.asarray(Image.open(io.BytesIO(
        insertion_ia.preserver_scene(chemin, _png(Image.fromarray(gen))))).convert("RGB"))
    # la structure est toujours là (grise), pas remplacée par le ciel bleu
    pixel = res[125, 200].astype(int)
    assert abs(pixel[2] - pixel[0]) < 40, f"le ciel a repris le dessus : {pixel}"
    assert pixel[0] > 140, f"structure effacée : {pixel}"
    # et le ciel loin de la structure est bien restauré
    assert abs(int(res[30, 30][2]) - int(fond[30, 30][2])) < 12


def test_preserver_scene_cadrage_different(tmp_path):
    """Ratio d'image différent : on rend l'image générée sans y toucher."""
    orig = Image.new("RGB", (400, 300), (100, 100, 100))
    chemin = tmp_path / "orig.png"
    orig.save(chemin)
    gen = _png(Image.new("RGB", (400, 200), (50, 50, 50)))
    assert insertion_ia.preserver_scene(chemin, gen) == gen


def test_ratio_photo_supporte(tmp_path):
    """Le ratio de sortie est le plus proche supporté par Nano Banana."""
    p = tmp_path / "photo.jpg"
    Image.new("RGB", (4080, 3060)).save(p)   # 4:3
    assert insertion_ia._ratio_photo(p) == "4:3"
    p2 = tmp_path / "large.jpg"
    Image.new("RGB", (1920, 1080)).save(p2)  # 16:9
    assert insertion_ia._ratio_photo(p2) == "16:9"
