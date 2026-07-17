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
                "segments": [[[0.2, 0.5], [0.7, 0.55]], [[0.15, 0.65], [0.5, 0.68]]],
                "calibrage": {"a": [0.2, 0.8], "b": [0.35, 0.8],
                              "distance_m": 2.5, "libelle": "largeur d'une place"},
            }},
        },
    }


def test_photo_emprise_segments_magenta_sans_texte(tmp_path, monkeypatch):
    """v5 : 1 trait magenta par ombrière + segment jaune, aucun vert."""
    monkeypatch.setattr(config, "PROJETS_DIR", tmp_path)
    projet = _projet_avec_guides(tmp_path)
    guides = insertion_ia.guides_actifs(projet)
    assert guides and len(guides["segments"]) == 2 and guides["calibrage"]
    annotee = insertion_ia.photo_emprise(projet)
    assert annotee and annotee.exists() and annotee.name == "photo_emprise.png"
    arr = np.asarray(Image.open(annotee).convert("RGB"))
    magenta = (arr[..., 0] > 200) & (arr[..., 1] < 90) & (arr[..., 2] > 140)
    jaune = (arr[..., 0] > 200) & (arr[..., 1] > 150) & (arr[..., 2] < 90)
    vert = (arr[..., 1] > 190) & (arr[..., 0] < 120) & (arr[..., 2] < 120)
    assert magenta.sum() > 200 and jaune.sum() > 40
    assert vert.sum() == 0
    assert "2 ombrières" in insertion_ia.resume_guides(guides)


def test_guides_segment_incomplet_ignore(tmp_path, monkeypatch):
    """Un segment à 1 point (tracé en cours) n'est pas retenu."""
    monkeypatch.setattr(config, "PROJETS_DIR", tmp_path)
    rel = "x/site.jpg"
    projet = {"id": "p", "insertion": {"photo": rel, "photos": [rel],
              "guides": {rel: {"segments": [[[0.2, 0.5]]], "calibrage": None}}}}
    assert insertion_ia.guides_actifs(projet) is None


def test_guides_absents(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROJETS_DIR", tmp_path)
    assert insertion_ia.guides_actifs({"id": "p", "insertion": {}}) is None


def test_scaffold_pose_un_volume_sur_l_axe(tmp_path, monkeypatch):
    """Le scaffold pose un volume gris (poteaux + toiture) sur l'axe tracé."""
    monkeypatch.setattr(config, "PROJETS_DIR", tmp_path)
    dossier = tmp_path / "p.assets" / "insertion" / "photos"
    dossier.mkdir(parents=True)
    Image.new("RGB", (1200, 800), (150, 150, 150)).save(dossier / "site.jpg")
    rel = "p.assets/insertion/photos/site.jpg"
    projet = {"id": "p", "ombriere": {"garde_au_sol_m": 2.5, "hauteur_hors_tout_m": 3.5},
              "insertion": {"photo": rel, "photos": [rel],
                "guides": {rel: {
                    "segments": [[[0.3, 0.55], [0.7, 0.58]]],
                    "calibrage": {"a": [0.3, 0.62], "b": [0.4, 0.62],
                                  "distance_m": 2.5, "libelle": "place"}}}}}
    p = insertion_ia.scaffold_photo(projet)
    assert p and p.exists() and p.name == "scaffold.png"
    arr = np.asarray(Image.open(p).convert("RGB"))
    # du gris foncé (toiture) et du gris clair (poteaux) sont apparus
    toit = (arr[..., 0] < 100) & (arr[..., 1] < 100) & (arr[..., 2] < 100) & \
           (abs(arr[..., 0].astype(int) - arr[..., 2]) < 20)
    assert toit.sum() > 500


def test_scaffold_sans_echelle_none(tmp_path, monkeypatch):
    """Sans calibrage ni plan (pas d'échelle), le scaffold s'abstient."""
    monkeypatch.setattr(config, "PROJETS_DIR", tmp_path)
    dossier = tmp_path / "p.assets" / "insertion" / "photos"
    dossier.mkdir(parents=True)
    Image.new("RGB", (800, 600), (150, 150, 150)).save(dossier / "s.jpg")
    rel = "p.assets/insertion/photos/s.jpg"
    projet = {"id": "p", "insertion": {"photo": rel, "photos": [rel],
              "guides": {rel: {"segments": [[[0.3, 0.5], [0.7, 0.5]]], "calibrage": None}}}}
    assert insertion_ia.scaffold_photo(projet) is None


def test_ratio_photo_supporte(tmp_path):
    """Le ratio de sortie est le plus proche supporté par Nano Banana."""
    p = tmp_path / "photo.jpg"
    Image.new("RGB", (4080, 3060)).save(p)   # 4:3
    assert insertion_ia._ratio_photo(p) == "4:3"
    p2 = tmp_path / "large.jpg"
    Image.new("RGB", (1920, 1080)).save(p2)  # 16:9
    assert insertion_ia._ratio_photo(p2) == "16:9"


@anse_requis
def test_aerienne_extraite_du_plan(tmp_path, monkeypatch):
    """Fond aérien croppé + emprises auto (rangées du plan) + flèche de pente."""
    monkeypatch.setattr(config, "PROJETS_DIR", tmp_path)
    uploads = tmp_path / "p.assets" / "uploads"
    uploads.mkdir(parents=True)
    import shutil
    shutil.copy(PLAN_ANSE, uploads / "dp2.pdf")
    projet = {"id": "p", "insertion": {},
              "documents": {"dp2": {"fichier": "p.assets/uploads/dp2.pdf"}}}
    d = insertion_ia.aerienne_donnees(projet)
    assert d and d["auto"] and d["fond"].exists()
    assert len(d["emprises"]) >= 1
    for emprise in d["emprises"]:
        assert len(emprise) == 4
        for x, y in emprise:
            assert 0.0 <= x <= 1.0 and 0.0 <= y <= 1.0
    assert d["fleche"]  # HAUT/BAS DE RAMPANT localisés

    annotee = insertion_ia.aerienne_emprise(projet)
    assert annotee and annotee.exists()
    arr = np.asarray(Image.open(annotee).convert("RGB"))
    magenta = (arr[..., 0] > 200) & (arr[..., 1] < 90) & (arr[..., 2] > 140)
    assert magenta.sum() > 300

    # override utilisateur : les coins stockés priment
    projet["insertion"]["aerienne"] = {
        "emprises": [[[0.1, 0.1], [0.4, 0.1], [0.4, 0.3], [0.1, 0.3]]],
        "auto": False}
    d2 = insertion_ia.aerienne_donnees(projet)
    assert not d2["auto"] and d2["emprises"][0][0] == [0.1, 0.1]
