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


def test_preserver_scene_cadrage_different(tmp_path):
    """Ratio d'image différent : on rend l'image générée sans y toucher."""
    orig = Image.new("RGB", (400, 300), (100, 100, 100))
    chemin = tmp_path / "orig.png"
    orig.save(chemin)
    gen = _png(Image.new("RGB", (400, 200), (50, 50, 50)))
    assert insertion_ia.preserver_scene(chemin, gen) == gen


def _projet_avec_guides(tmp_path):
    dossier = tmp_path / "p.assets" / "insertion" / "photos"
    dossier.mkdir(parents=True)
    Image.new("RGB", (400, 300), (120, 120, 120)).save(dossier / "site.jpg")
    rel = "p.assets/insertion/photos/site.jpg"
    return {
        "id": "p",
        "insertion": {
            "photo": rel, "photos": [rel],
            "guides": {rel: {
                "emprises": [[[0.1, 0.4], [0.6, 0.35], [0.65, 0.6], [0.12, 0.7]]],
                "calibrage": {"a": [0.2, 0.8], "b": [0.35, 0.8],
                              "distance_m": 2.5, "libelle": "largeur d'une place"},
            }},
        },
    }


def test_photo_guidee_dessine_les_traces(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROJETS_DIR", tmp_path)
    projet = _projet_avec_guides(tmp_path)
    guides = insertion_ia.guides_actifs(projet)
    assert guides and len(guides["emprises"]) == 1 and guides["calibrage"]
    annotee = insertion_ia.photo_guidee(projet)
    assert annotee and annotee.exists()
    arr = np.asarray(Image.open(annotee).convert("RGB"))
    vert = (arr[..., 1] > 190) & (arr[..., 0] < 120)   # tracés verts présents
    jaune = (arr[..., 0] > 200) & (arr[..., 1] > 150) & (arr[..., 2] < 90)
    assert vert.sum() > 200 and jaune.sum() > 60
    assert "1 emprise" in insertion_ia.resume_guides(guides)


def test_prompt_guides_confirment_le_plan(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROJETS_DIR", tmp_path)
    projet = _projet_avec_guides(tmp_path)
    guides = insertion_ia.guides_actifs(projet)
    prompt = insertion_ia.construire_prompt(
        projet, guides=guides,
        plan_infos={"echelle": "1/200", "dims_m": [(17.9, 5.4)]},
        idx={"photo": 1, "plan": 2, "guides": 3, "coupe": 4})
    assert "polygones verts tracés sur l'image 3" in prompt
    assert "épouser exactement" in prompt
    assert "2,5 m" in prompt and "largeur d'une place" in prompt
    assert "Ne reproduis pas ces tracés" in prompt
    assert "LE PLAN DE MASSE (image 2)" in prompt  # légende du plan conservée


def test_guides_absents(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROJETS_DIR", tmp_path)
    assert insertion_ia.guides_actifs({"id": "p", "insertion": {}}) is None
