"""Flux « insertion un geste » (17/07/2026 soir) : type + bord avant tracé.

Placement par un cliqué-glissé du bord avant, référence photo réelle du type,
prompt court, effacement du repère. Cf. insertion_ia.py section FLUX « UN GESTE ».
"""
import io

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from app import config, insertion_ia
from app.main import app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROJETS_DIR", tmp_path / "PROJETS")
    return TestClient(app)


def _png(im: Image.Image) -> bytes:
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def _projet_photo(tmp_path, famille="START PLAINE Bas", bord_avant=None, ombrieres=None):
    """Projet minimal avec une photo texturée + éventuellement des ombrières.

    `bord_avant` : raccourci mono-ombrière. `ombrieres` : liste complète.
    """
    dossier = tmp_path / "p.assets" / "insertion" / "photos"
    dossier.mkdir(parents=True)
    rng = np.random.default_rng(5)
    Image.fromarray(rng.integers(110, 200, (900, 1200, 3), dtype=np.uint8)).save(dossier / "site.png")
    rel = "p.assets/insertion/photos/site.png"
    ins = {"photo": rel, "photos": [rel]}
    if ombrieres:
        ins["poses"] = {rel: {"ombrieres": ombrieres}}
    elif bord_avant:
        ins["poses"] = {rel: {"ombrieres": [{"bord_avant": bord_avant}]}}
    return {"id": "p", "ombriere": {"famille": famille}, "insertion": ins}, config.PROJETS_DIR / rel


# ------------------------------------------------------------------ pose

def test_poses_actives_valide_et_invalide(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROJETS_DIR", tmp_path)
    projet, _ = _projet_photo(tmp_path, bord_avant=[[0.2, 0.6], [0.8, 0.6]])
    poses = insertion_ia.poses_actives(projet)
    assert poses and len(poses) == 1
    assert poses[0]["bord_avant"] == [[0.2, 0.6], [0.8, 0.6]]
    assert poses[0]["famille"] == "START PLAINE Bas"
    assert poses[0]["profondeur_m"] == 5.0          # défaut du catalogue
    # tracé incomplet -> ignoré
    rel = projet["insertion"]["photo"]
    projet["insertion"]["poses"][rel]["ombrieres"] = [{"bord_avant": [[0.2, 0.6]]}]
    assert insertion_ia.poses_actives(projet) is None
    projet["insertion"]["poses"] = {}
    assert insertion_ia.poses_actives(projet) is None


def test_poses_triees_gauche_droite_et_types_propres(tmp_path, monkeypatch):
    """Plusieurs ombrières : ordre gauche->droite, chacune avec son type."""
    monkeypatch.setattr(config, "PROJETS_DIR", tmp_path)
    projet, _ = _projet_photo(tmp_path, ombrieres=[
        {"bord_avant": [[0.60, 0.6], [0.90, 0.6]], "famille": "START PLAINE Double",
         "longueur_m": 30.0, "profondeur_m": 10.0},
        {"bord_avant": [[0.10, 0.7], [0.35, 0.7]], "famille": "START PLAINE Haut",
         "longueur_m": 12.0},
    ])
    poses = insertion_ia.poses_actives(projet)
    assert len(poses) == 2
    assert poses[0]["famille"] == "START PLAINE Haut"     # le plus à gauche d'abord
    assert poses[1]["famille"] == "START PLAINE Double"
    assert poses[0]["profondeur_m"] == 5.0               # défaut du type Haut
    assert insertion_ia._familles_tracees(projet) == ["START PLAINE Haut",
                                                      "START PLAINE Double"]


def test_ancien_format_mono_reste_lisible(tmp_path, monkeypatch):
    """Un projet enregistré avant le multi (bord_avant seul) reste exploitable."""
    monkeypatch.setattr(config, "PROJETS_DIR", tmp_path)
    projet, _ = _projet_photo(tmp_path)
    rel = projet["insertion"]["photo"]
    projet["insertion"]["poses"] = {rel: {"bord_avant": [[0.2, 0.6], [0.8, 0.6]]}}
    poses = insertion_ia.poses_actives(projet)
    assert poses and len(poses) == 1 and poses[0]["bord_avant"][1] == [0.8, 0.6]


def test_reference_photo_par_type(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROJETS_DIR", tmp_path)
    for famille, attendu in [("START PLAINE Bas", "ombriere_mono_galva.jpg"),
                             ("START PLAINE Double", "ombriere_double_galva.jpg")]:
        projet, _ = _projet_photo(tmp_path.parent / famille, famille=famille)
        ref = insertion_ia._reference_photo(projet)
        assert ref is not None and ref.exists() and ref.name == attendu


def test_photo_reperee_trace_bord_avant(tmp_path, monkeypatch):
    """La photo repérée ne porte QUE les bords avant en magenta.

    Aucune flèche cyan : le modèle la redessinait en grand par-dessus les
    toitures (18/07/2026), hors de la bande d'effacement. Le sens de fuite est
    désormais uniquement décrit dans le prompt.
    """
    monkeypatch.setattr(config, "PROJETS_DIR", tmp_path)
    projet, _ = _projet_photo(tmp_path, ombrieres=[
        {"bord_avant": [[0.10, 0.7], [0.40, 0.7]]},
        {"bord_avant": [[0.55, 0.7], [0.85, 0.7]]},
    ])
    rep = insertion_ia.photo_reperee(projet)
    assert rep and rep.exists() and rep.name == "photo_reperee.png"
    arr = np.asarray(Image.open(rep).convert("RGB"))
    magenta = (arr[..., 0] > 200) & (arr[..., 1] < 90) & (arr[..., 2] > 140)
    cyan = (arr[..., 0] < 90) & (arr[..., 1] > 150) & (arr[..., 2] > 200)
    assert magenta.sum() > 100        # les deux traits
    assert cyan.sum() == 0            # plus aucune flèche envoyée au modèle


def test_fuite_pointe_vers_le_haut(tmp_path):
    """La flèche de fuite est perpendiculaire au bord et monte dans l'image."""
    fx, fy = insertion_ia._fuite((0.2 * 100, 0.6 * 100), (0.8 * 100, 0.6 * 100))
    assert fy < 0 and abs(fx) < 1e-6            # bord horizontal -> fuite verticale haut


# ------------------------------------------------------------------ prompt

def test_prompt_pose_mono(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROJETS_DIR", tmp_path)
    projet, _ = _projet_photo(tmp_path, famille="START PLAINE Bas",
                              bord_avant=[[0.2, 0.6], [0.8, 0.6]])
    p = insertion_ia.construire_prompt_pose(projet, pose=True)
    assert "bord AVANT" in p and "magenta" in p
    assert "MONOPENTE" in p and "full black" in p.lower()
    assert "REFERENCE" in p and "GUIDES" in p        # référence + anti-repère


def test_prompt_double_decrit_un_T_pas_un_Y(tmp_path, monkeypatch):
    """Le profil Double doit être décrit comme UNE toiture d'un seul tenant.

    Constaté le 18/07/2026 : parler de « deux versants » faisait dessiner une
    arête centrale (profil en Y / V inversé) au lieu du T de la coupe Solstyce.
    """
    monkeypatch.setattr(config, "PROJETS_DIR", tmp_path)
    projet, _ = _projet_photo(tmp_path, famille="START PLAINE Double",
                              bord_avant=[[0.2, 0.6], [0.8, 0.6]])
    p = insertion_ia.construire_prompt_pose(projet, pose=True)
    assert "DOUBLE" in p and "dessine un T" in p
    assert "UNE SEULE toiture" in p and "sans arête ni sommet au milieu" in p
    # la formulation fautive est bannie ; « versants » ne subsiste que dans
    # l'interdiction explicite (« Jamais deux versants opposés »)
    assert "toiture à deux versants" not in p
    assert "Jamais deux versants opposés" in p
    assert "10 m de profondeur" in p                 # cote du type Double


def test_jamais_de_profil_en_Y(tmp_path, monkeypatch):
    """Règle métier absolue : Greenvolt ne pose jamais d'ombrière en Y.

    L'interdiction vaut pour TOUS les types, mono comme double, et doit être
    portée par le prompt indépendamment du type tracé.
    """
    monkeypatch.setattr(config, "PROJETS_DIR", tmp_path)
    for famille in ("START PLAINE Bas", "START PLAINE Haut", "START PLAINE Double"):
        projet, _ = _projet_photo(tmp_path / famille, famille=famille,
                                  bord_avant=[[0.2, 0.6], [0.8, 0.6]])
        p = insertion_ia.construire_prompt_pose(projet, pose=True)
        assert "PLAN UNIQUE incliné" in p
        assert "en V, en Y ou en papillon" in p
        assert "UNE SEULE toiture" in p


def test_coupe_be_prime_toujours(tmp_path, monkeypatch):
    """La DP3 du BE fait foi même si des types différents sont tracés.

    Règle métier (18/07/2026) : c'est la coupe du projet réel, celle qu'on
    construit. Les coupes catalogue ne servent qu'à défaut.
    """
    monkeypatch.setattr(config, "PROJETS_DIR", tmp_path)
    projet, _ = _projet_photo(tmp_path, famille="START PLAINE Bas", ombrieres=[
        {"bord_avant": [[0.1, 0.6], [0.4, 0.6]], "famille": "START PLAINE Bas"},
        {"bord_avant": [[0.6, 0.6], [0.9, 0.6]], "famille": "START PLAINE Double"},
    ])
    # sans DP3 : on retombe sur les coupes types, une par type tracé
    assert len(insertion_ia._coupes_payload(projet)) == 2

    # avec une DP3 fournie par le BE : elle prime, seule et pour tout le monde
    uploads = config.PROJETS_DIR / "p.assets" / "uploads"
    uploads.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (900, 600), (250, 250, 250)).save(uploads / "dp3.png")
    projet["documents"] = {"dp3": {"fichier": "p.assets/uploads/dp3.png"}}
    coupes = insertion_ia._coupes_payload(projet)
    assert len(coupes) == 1 and coupes[0].name == "dp3.png"
    p = insertion_ia.construire_prompt_pose(projet, pose=True)
    assert "elle prime sur tout le reste" in p


def test_sens_de_pente_par_defaut_et_inverse(tmp_path, monkeypatch):
    """Le sens de la pente (côté du point haut) est explicite dans le prompt."""
    monkeypatch.setattr(config, "PROJETS_DIR", tmp_path)
    projet, _ = _projet_photo(tmp_path, bord_avant=[[0.2, 0.6], [0.8, 0.6]])
    assert insertion_ia.poses_actives(projet)[0]["pente_vers"] == "fond"   # défaut
    p = insertion_ia.construire_prompt_pose(projet, pose=True)
    assert "SENS DE LA PENTE" in p
    assert "point haut est au fond" in p and "monte en s'éloignant" in p

    rel = projet["insertion"]["photo"]
    projet["insertion"]["poses"][rel]["ombrieres"][0]["pente_vers"] = "avant"
    p2 = insertion_ia.construire_prompt_pose(projet, pose=True)
    assert "point haut est devant" in p2 and "descend en s'éloignant" in p2


def test_cote_des_poteaux_deduit_du_type_et_du_sens(tmp_path, monkeypatch):
    """Type + sens de pente fixent le côté des poteaux (silhouette complète).

    Mono Bas = poteau côté HAUT : si le point haut est au fond, les poteaux
    sont au fond ; si le point haut passe devant, ils passent devant.
    Mono Haut = poteau côté BAS : le raisonnement s'inverse.
    """
    monkeypatch.setattr(config, "PROJETS_DIR", tmp_path)
    cas = [
        ("START PLAINE Bas", "fond", "poteaux sont donc au fond"),
        ("START PLAINE Bas", "avant", "poteaux sont donc devant"),
        ("START PLAINE Haut", "fond", "poteaux sont donc devant"),
        ("START PLAINE Haut", "avant", "poteaux sont donc au fond"),
    ]
    for i, (famille, sens, attendu) in enumerate(cas):
        projet, _ = _projet_photo(tmp_path / f"c{i}", famille=famille, ombrieres=[
            {"bord_avant": [[0.2, 0.6], [0.8, 0.6]], "famille": famille,
             "pente_vers": sens},
        ])
        p = insertion_ia.construire_prompt_pose(projet, pose=True)
        assert attendu in p, f"{famille} / {sens} : attendu « {attendu} »"


def test_double_na_pas_de_cote_de_poteau(tmp_path, monkeypatch):
    """Poteau central : pas de côté proche/lointain, mais le sens reste dit."""
    monkeypatch.setattr(config, "PROJETS_DIR", tmp_path)
    projet, _ = _projet_photo(tmp_path, famille="START PLAINE Double",
                              bord_avant=[[0.2, 0.6], [0.8, 0.6]])
    p = insertion_ia.construire_prompt_pose(projet, pose=True)
    assert "poteaux sont donc" not in p
    assert "SENS DE LA PENTE" in p


def test_route_pose_conserve_le_sens(client):
    """Le sens choisi survit à l'aller-retour serveur ; valeur invalide -> défaut."""
    pid = _creer_projet_avec_photo(client)
    photo = client.get(f"/api/projets/{pid}/insertion/photos-disponibles").json()["active"]
    r = client.put(f"/api/projets/{pid}/insertion/pose", json={"photo": photo, "ombrieres": [
        {"bord_avant": [[0.2, 0.6], [0.5, 0.6]], "pente_vers": "avant"},
        {"bord_avant": [[0.6, 0.6], [0.9, 0.6]], "pente_vers": "n'importe quoi"},
    ]})
    assert r.status_code == 200
    posees = r.json()["projet"]["insertion"]["poses"][photo]["ombrieres"]
    assert posees[0]["pente_vers"] == "avant"
    assert posees[1]["pente_vers"] == "fond"


def test_prompt_interdit_les_annotations(tmp_path, monkeypatch):
    """Les mesures sont données SANS jamais titrer « cotes ».

    Constaté le 18/07/2026 : le bloc intitulé « COTES » faisait tracer de vraies
    lignes de cote chiffrées sur la photo. Le refus d'annotation est répété dans
    le dernier bloc, celui qui pèse le plus.
    """
    monkeypatch.setattr(config, "PROJETS_DIR", tmp_path)
    projet, _ = _projet_photo(tmp_path, bord_avant=[[0.2, 0.6], [0.8, 0.6]])
    p = insertion_ia.construire_prompt_pose(projet, pose=True)
    assert "COTES." not in p
    assert "ne doivent jamais apparaître dans l'image" in p
    dernier = p.rsplit("\n\n", 1)[-1]
    assert dernier.startswith("RENDU.")
    for interdit in ("aucun texte", "aucun chiffre", "aucune cote"):
        assert interdit in dernier


def test_pente_deduite_des_hauteurs(tmp_path, monkeypatch):
    """La pente annoncée découle des hauteurs et de la profondeur réelles."""
    monkeypatch.setattr(config, "PROJETS_DIR", tmp_path)
    projet, _ = _projet_photo(tmp_path, ombrieres=[
        {"bord_avant": [[0.2, 0.6], [0.8, 0.6]], "famille": "START PLAINE Bas",
         "longueur_m": 12.0, "profondeur_m": 4.3},
    ])
    projet["ombriere"].update({"garde_au_sol_m": 3.5, "hauteur_hors_tout_m": 4.5})
    p = insertion_ia.construire_prompt_pose(projet, pose=True)
    assert "13,1°" in p          # atan(1,0 / 4,3), et non les 5° du catalogue
    assert "5°" not in p


def test_hauteurs_suivent_le_type_de_chaque_ombriere(tmp_path, monkeypatch):
    """L'override projet ne s'applique qu'aux ombrières du même type."""
    monkeypatch.setattr(config, "PROJETS_DIR", tmp_path)
    projet, _ = _projet_photo(tmp_path, famille="START PLAINE Bas", ombrieres=[
        {"bord_avant": [[0.1, 0.6], [0.4, 0.6]], "famille": "START PLAINE Bas"},
        {"bord_avant": [[0.6, 0.6], [0.9, 0.6]], "famille": "START PLAINE Double"},
    ])
    projet["ombriere"].update({"garde_au_sol_m": 3.5, "hauteur_hors_tout_m": 4.5})
    p = insertion_ia.construire_prompt_pose(projet, pose=True)
    assert "3,5 m au point bas et 4,5 m" in p     # la mono reprend la saisie projet
    assert "2,99 m au point bas et 3,92 m" in p   # la double garde ses cotes catalogue


def test_prompt_multi_decrit_chaque_ombriere(tmp_path, monkeypatch):
    """Deux ombrières = deux descriptions cotées, dans l'ordre gauche->droite."""
    monkeypatch.setattr(config, "PROJETS_DIR", tmp_path)
    projet, _ = _projet_photo(tmp_path, ombrieres=[
        {"bord_avant": [[0.55, 0.6], [0.9, 0.6]], "famille": "START PLAINE Double",
         "longueur_m": 30.0, "profondeur_m": 10.0},
        {"bord_avant": [[0.1, 0.7], [0.4, 0.7]], "famille": "START PLAINE Bas",
         "longueur_m": 12.0, "profondeur_m": 5.0},
    ])
    p = insertion_ia.construire_prompt_pose(projet, pose=True)
    assert "2 ombrières" in p
    assert "Ombrière 1" in p and "Ombrière 2" in p
    assert "12 m de long" in p and "30 m de long" in p
    # la 1re (gauche) est la monopente, la 2e la double
    assert p.index("12 m de long") < p.index("30 m de long")


def test_preparer_requete_pose(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROJETS_DIR", tmp_path)
    projet, _ = _projet_photo(tmp_path, bord_avant=[[0.2, 0.6], [0.8, 0.6]])
    req = insertion_ia._preparer_requete_pose(projet)
    assert req["mode"] == "pose"
    assert req["roles"][0] == "photo"
    assert "coupe" in req["roles"] and "reference" in req["roles"]
    assert len(req["chemins"]) == len(req["roles"])

    # sans tracé : mode libre, la base est la photo propre
    projet["insertion"]["poses"] = {}
    req2 = insertion_ia._preparer_requete_pose(projet)
    assert req2["mode"] == "libre" and req2["roles"][0] == "photo"


def test_preparer_requete_sans_photo(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROJETS_DIR", tmp_path)
    with pytest.raises(insertion_ia.InsertionError):
        insertion_ia._preparer_requete_pose({"id": "p", "insertion": {}})


# ------------------------------------------------------------------ contrôle & effacement

def test_controle_pose_present_vs_absent(tmp_path, monkeypatch):
    """Structure sombre sur la bande d'emprise -> ok ; rien -> faible."""
    monkeypatch.setattr(config, "PROJETS_DIR", tmp_path)
    projet, clean = _projet_photo(tmp_path, bord_avant=[[0.2, 0.62], [0.8, 0.62]])
    W, H = Image.open(clean).size
    bandes = insertion_ia._bandes_pose(projet, W, H)
    assert len(bandes) == 1

    im = Image.open(clean).convert("RGB")
    dr = ImageDraw.Draw(im)
    for bande in bandes:
        dr.polygon([(x, y) for x, y in bande], fill=(20, 22, 26))
    ok = insertion_ia.controle_pose(projet, clean, _png(im))
    assert ok and ok["verdict"] in ("ok", "partiel") and ok["couverture"] > 0.3

    rien = insertion_ia.controle_pose(projet, clean, _png(Image.open(clean).convert("RGB")))
    assert rien and rien["verdict"] == "faible"


def test_controle_pose_sans_pose(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROJETS_DIR", tmp_path)
    projet, clean = _projet_photo(tmp_path)
    assert insertion_ia.controle_pose(projet, clean, _png(Image.open(clean))) is None


def test_effacer_marqueur_hors_bande_mais_epargne_les_rouges(tmp_path, monkeypatch):
    """Le repère est effacé même loin du tracé, sans toucher au rouge de la scène.

    Constaté sur Anse (18/07/2026) : le modèle redessine le trait déplacé et
    allongé — 27 477 pixels roses sur 27 480 tombaient HORS de la bande. Le
    discriminant retenu est l'équilibre R≈B du magenta (résidu mesuré
    R149 G109 B148), qui exclut un rouge d'enseigne (R220 G30 B60).
    """
    monkeypatch.setattr(config, "PROJETS_DIR", tmp_path)
    projet, clean = _projet_photo(tmp_path, bord_avant=[[0.2, 0.6], [0.3, 0.6]])
    W, H = Image.open(clean).size
    gen = Image.open(clean).convert("RGB")
    dr = ImageDraw.Draw(gen)
    # trait rose pâle très loin du tracé (comme le fait le modèle)
    dr.line([(0.55 * W, 0.30 * H), (0.95 * W, 0.30 * H)], fill=(149, 109, 148), width=9)
    # enseigne rouge de la scène, à préserver
    dr.rectangle([0.05 * W, 0.05 * H, 0.25 * W, 0.15 * H], fill=(220, 30, 60))

    out = insertion_ia.effacer_marqueur(projet, clean, _png(gen))
    arr = np.asarray(Image.open(io.BytesIO(out)).convert("RGB")).astype(int)
    r, g, b = arr[..., 0], arr[..., 1], arr[..., 2]

    magenta = (r - g > 25) & (b - g > 25) & (abs(r - b) < 45) & (r > 90)
    assert magenta.sum() == 0                      # repère efface, meme hors bande

    rouge = arr[int(0.10 * H), int(0.15 * W)]
    assert tuple(rouge) == (220, 30, 60)           # enseigne intacte


def test_effacer_marqueur_retire_le_repere(tmp_path, monkeypatch):
    """Un trait magenta dans la bande du repère est effacé ; ailleurs, intact."""
    monkeypatch.setattr(config, "PROJETS_DIR", tmp_path)
    projet, clean = _projet_photo(tmp_path, bord_avant=[[0.2, 0.6], [0.8, 0.6]])
    W, H = Image.open(clean).size
    gen = Image.open(clean).convert("RGB")
    dr = ImageDraw.Draw(gen)
    dr.line([(0.2 * W, 0.6 * H), (0.8 * W, 0.6 * H)], fill=(255, 0, 200), width=6)  # dans la bande
    dr.line([(0.1 * W, 0.15 * H), (0.3 * W, 0.15 * H)], fill=(255, 0, 200), width=6)  # hors bande
    out = insertion_ia.effacer_marqueur(projet, clean, _png(gen))
    arr = np.asarray(Image.open(io.BytesIO(out)).convert("RGB")).astype(int)
    r, g, b = arr[..., 0], arr[..., 1], arr[..., 2]
    magenta = (r - g > 12) & (b - g > 8) & (r > 80)
    bande = insertion_ia._bande_marqueur(projet, (W, H))
    assert (magenta & bande).sum() < 200          # repère effacé dans la bande
    assert (magenta & ~bande).sum() > 200         # trait hors bande préservé


# ------------------------------------------------------------------ routes

def _creer_projet_avec_photo(client) -> str:
    pid = client.post("/api/projets", json={"nom": "RIVE test"}).json()["projet"]["id"]
    img = _png(Image.new("RGB", (1200, 900), (150, 160, 170)))
    r = client.post(f"/api/projets/{pid}/insertion/photos",
                    files={"fichiers": ("site.png", img, "image/png")})
    assert r.status_code == 200
    return pid


def test_route_type_et_pose(client):
    pid = _creer_projet_avec_photo(client)
    # type
    r = client.put(f"/api/projets/{pid}/insertion/type", json={"famille": "START PLAINE Double"})
    assert r.status_code == 200
    assert r.json()["projet"]["ombriere"]["famille"] == "START PLAINE Double"
    r = client.put(f"/api/projets/{pid}/insertion/type", json={"famille": "n'importe quoi"})
    assert r.status_code == 400

    # pose : deux ombrières, chacune avec son type et ses cotes
    photo = client.get(f"/api/projets/{pid}/insertion/photos-disponibles").json()["active"]
    r = client.put(f"/api/projets/{pid}/insertion/pose", json={"photo": photo, "ombrieres": [
        {"bord_avant": [[0.3, 0.66], [0.72, 0.66]], "famille": "START PLAINE Bas",
         "longueur_m": 20, "profondeur_m": 5},
        {"bord_avant": [[0.05, 0.72], [0.25, 0.72]], "famille": "START PLAINE Double"},
    ]})
    assert r.status_code == 200
    posees = r.json()["projet"]["insertion"]["poses"][photo]["ombrieres"]
    assert len(posees) == 2
    assert posees[0]["longueur_m"] == 20.0 and posees[0]["famille"] == "START PLAINE Bas"
    assert posees[1]["famille"] == "START PLAINE Double"
    assert posees[1]["profondeur_m"] == 10.0        # défaut du type Double
    # effacer les tracés
    r = client.put(f"/api/projets/{pid}/insertion/pose", json={"photo": photo, "ombrieres": []})
    assert photo not in r.json()["projet"]["insertion"]["poses"]


def test_relance_auto_sur_faible(tmp_path, monkeypatch):
    """Un 1er rendu quasi vide (couverture < 0,15) déclenche une relance ;
    on garde le meilleur. Un simple « faible » à 0,2 ne relance plus (le
    contrôle est approximatif : relancer doublait la dépense pour rien)."""
    monkeypatch.setattr(config, "PROJETS_DIR", tmp_path)
    monkeypatch.setenv("GEMINI_API_KEY", "test")
    monkeypatch.setenv("GVDP_PRESERVER_SCENE", "0")
    projet, clean = _projet_photo(tmp_path, bord_avant=[[0.2, 0.6], [0.8, 0.6]])
    img_bytes = _png(Image.open(clean).convert("RGB"))
    appels = {"n": 0}

    def faux_appel(parts, aspect_ratio=None):
        appels["n"] += 1
        return img_bytes

    verdicts = iter([
        {"couverture": 0.05, "verdict": "faible"},   # 1er essai : rien construit
        {"couverture": 0.9, "verdict": "ok"},        # relance réussie
    ])
    monkeypatch.setattr(insertion_ia, "_appel_gemini", faux_appel)
    monkeypatch.setattr(insertion_ia, "controle_pose", lambda *a, **k: next(verdicts))

    res = insertion_ia.generer_image(projet)
    assert appels["n"] == 2 and res["essais"] == 2
    assert res["controle"]["verdict"] == "ok"        # la meilleure tentative gardée
    assert insertion_ia.compteur_global() == 2       # chaque appel facturé compté


def test_pas_de_relance_sur_faible_ambigu(tmp_path, monkeypatch):
    """Couverture 0,2 (« faible » mais pas quasi nulle) : pas de relance —
    le contrôle géométrique est approximatif, on ne double pas la dépense."""
    monkeypatch.setattr(config, "PROJETS_DIR", tmp_path)
    monkeypatch.setenv("GEMINI_API_KEY", "test")
    monkeypatch.setenv("GVDP_PRESERVER_SCENE", "0")
    projet, clean = _projet_photo(tmp_path, bord_avant=[[0.2, 0.6], [0.8, 0.6]])
    img_bytes = _png(Image.open(clean).convert("RGB"))
    appels = {"n": 0}

    def faux_appel(parts, aspect_ratio=None):
        appels["n"] += 1
        return img_bytes

    monkeypatch.setattr(insertion_ia, "_appel_gemini", faux_appel)
    monkeypatch.setattr(insertion_ia, "controle_pose",
                        lambda *a, **k: {"couverture": 0.2, "verdict": "faible"})
    res = insertion_ia.generer_image(projet)
    assert appels["n"] == 1 and res["essais"] == 1


def test_relance_en_echec_garde_la_premiere_image(tmp_path, monkeypatch):
    """Si la relance lève (quota…), on garde la 1re image déjà payée au lieu
    de tout perdre, et le compteur reflète le seul appel facturé."""
    monkeypatch.setattr(config, "PROJETS_DIR", tmp_path)
    monkeypatch.setenv("GEMINI_API_KEY", "test")
    monkeypatch.setenv("GVDP_PRESERVER_SCENE", "0")
    projet, clean = _projet_photo(tmp_path, bord_avant=[[0.2, 0.6], [0.8, 0.6]])
    img_bytes = _png(Image.open(clean).convert("RGB"))
    appels = {"n": 0}

    def faux_appel(parts, aspect_ratio=None):
        appels["n"] += 1
        if appels["n"] > 1:
            raise insertion_ia.InsertionError("Quota Gemini atteint")
        return img_bytes

    monkeypatch.setattr(insertion_ia, "_appel_gemini", faux_appel)
    monkeypatch.setattr(insertion_ia, "controle_pose",
                        lambda *a, **k: {"couverture": 0.05, "verdict": "faible"})
    res = insertion_ia.generer_image(projet)
    assert appels["n"] == 2 and res["essais"] == 1   # 2 appels, 1 seul facturé
    assert res["fichier"]                            # l'image du 1er appel est gardée
    assert insertion_ia.compteur_global() == 1


def test_pas_de_relance_si_premier_ok(tmp_path, monkeypatch):
    """Un 1er rendu correct ne relance pas (coût 1)."""
    monkeypatch.setattr(config, "PROJETS_DIR", tmp_path)
    monkeypatch.setenv("GEMINI_API_KEY", "test")
    monkeypatch.setenv("GVDP_PRESERVER_SCENE", "0")
    projet, clean = _projet_photo(tmp_path, bord_avant=[[0.2, 0.6], [0.8, 0.6]])
    img_bytes = _png(Image.open(clean).convert("RGB"))
    appels = {"n": 0}

    def faux_appel(parts, aspect_ratio=None):
        appels["n"] += 1
        return img_bytes

    monkeypatch.setattr(insertion_ia, "_appel_gemini", faux_appel)
    monkeypatch.setattr(insertion_ia, "controle_pose",
                        lambda *a, **k: {"couverture": 0.8, "verdict": "ok"})
    res = insertion_ia.generer_image(projet)
    assert appels["n"] == 1 and res["essais"] == 1


def test_route_reference_et_payload(client):
    pid = _creer_projet_avec_photo(client)
    r = client.get(f"/api/projets/{pid}/insertion/reference")
    assert r.status_code == 200 and r.headers["content-type"].startswith("image/")
    d = client.get(f"/api/projets/{pid}/insertion/apercu-payload").json()
    roles = [im["role"] for im in d["images"]]
    # photo à éditer + coupe technique + photo de référence
    assert roles[0] == "photo" and "coupe" in roles and "reference" in roles
    assert d["prompt"]
