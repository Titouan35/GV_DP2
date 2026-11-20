"""Non-régression des deux failles relevées à l'analyse du 20/11/2026.

1. Page de connexion : l'identifiant saisi était renvoyé dans le formulaire
   sans échappement. /connexion est ouverte dans tous les modes, y compris au
   poste : un site tiers pouvait y poster `"><script>…` et exécuter du script
   sur l'origine de l'outil, donc lire ou supprimer les dossiers.

2. Chemins des pièces : `documents` arrive tel quel du navigateur à chaque
   sauvegarde, et `fichier` servait à lire et à SUPPRIMER sans contrôle. Le
   téléchargement était déjà confiné (test_socle_api_documents) ; la
   suppression, l'assemblage, les notices ERP, la relecture de la FVE et le
   contrôle de cohérence ne l'étaient pas.

3. Identifiant de projet dans les URLs : les routes de téléchargement
   construisaient (et créaient) le dossier du projet sans valider
   l'identifiant ; `%5C` y glissait un séparateur Windows.
"""
from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import assemblage, coherence, config, notices_erp
from app.main import app


@pytest.fixture(autouse=True)
def environnement_propre(monkeypatch):
    monkeypatch.delenv("GVDP_COMPTES", raising=False)
    monkeypatch.delenv("GVDP_AUTH_DELEGUEE", raising=False)


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROJETS_DIR", tmp_path / "PROJETS")
    return TestClient(app)


def _png_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (60, 40), (120, 130, 140)).save(buf, "PNG")
    return buf.getvalue()


def _projet_avec_piece(client, fichier: str, code: str = "dp6") -> str:
    """Projet dont la pièce `code` pointe vers `fichier`, posé par un PUT
    comme le ferait un navigateur malveillant."""
    pid = client.post("/api/projets", json={"nom": "Faille"}).json()["projet"]["id"]
    projet = client.get(f"/api/projets/{pid}").json()["projet"]
    projet["documents"] = {code: {"nom_fichier": "x.png", "fichier": fichier,
                                  "date": "2026-11-20T10:00:00", "taille": 1}}
    assert client.put(f"/api/projets/{pid}", json=projet).status_code == 200
    return pid


# ------------------------------------------------------- 1. page de connexion

CHARGE = '"><script>alert(1)</script>'


@pytest.mark.parametrize("protege", [False, True])
def test_l_identifiant_saisi_est_echappe(client, monkeypatch, protege):
    """Avec ou sans compte : la route répond dans les deux cas."""
    if protege:
        from app import securite
        monkeypatch.setenv("GVDP_COMPTES", f"agent-be:{securite.empreinte('mdp')}")

    r = client.post("/connexion", data={"utilisateur": CHARGE, "motdepasse": "faux"})

    assert r.status_code == 401
    assert "<script>" not in r.text
    assert 'value="&quot;&gt;&lt;script&gt;' in r.text


def test_un_identifiant_ordinaire_reste_lisible(client, monkeypatch):
    """L'échappement ne doit pas défigurer un identifiant normal."""
    from app import securite
    monkeypatch.setenv("GVDP_COMPTES", f"agent-be:{securite.empreinte('mdp')}")

    r = client.post("/connexion", data={"utilisateur": "agent-be", "motdepasse": "faux"})

    assert 'value="agent-be"' in r.text


# ---------------------------------------------------- 2. confinement : unitaire

def test_chemin_dans_espace_accepte_une_piece_du_projet(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROJETS_DIR", tmp_path)
    rel = "p.assets/uploads/dp6.png"
    assert config.chemin_dans_espace(rel) == tmp_path / rel


@pytest.mark.parametrize("relatif", [
    "../secret.png",
    "p.assets/../../secret.png",
    ".",
    "",
    None,
    42,
])
def test_chemin_dans_espace_refuse_ce_qui_sort(tmp_path, monkeypatch, relatif):
    monkeypatch.setattr(config, "PROJETS_DIR", tmp_path / "PROJETS")
    assert config.chemin_dans_espace(relatif) is None


def test_chemin_dans_espace_refuse_un_chemin_absolu(tmp_path, monkeypatch):
    """`Path("a") / "/x"` vaut `/x` : la base disparaît sans prévenir."""
    monkeypatch.setattr(config, "PROJETS_DIR", tmp_path / "PROJETS")
    intrus = tmp_path / "secret.png"
    intrus.write_bytes(b"x")
    assert config.chemin_dans_espace(str(intrus)) is None


# ------------------------------------------------- 2. confinement : par la route

@pytest.mark.parametrize("forme", ["relatif", "absolu"])
def test_supprimer_une_piece_ne_touche_pas_un_fichier_hors_espace(client, tmp_path, forme):
    # Arrange : un fichier bien réel, à côté du dossier des projets
    intrus = tmp_path / "secret.png"
    intrus.write_bytes(_png_bytes())
    fichier = "../secret.png" if forme == "relatif" else str(intrus)
    pid = _projet_avec_piece(client, fichier)

    # Act
    r = client.delete(f"/api/projets/{pid}/documents/dp6")

    # Assert : l'entrée part du projet, le fichier reste
    assert r.status_code == 200
    assert "dp6" not in r.json()["projet"]["documents"]
    assert intrus.exists()


def test_supprimer_une_piece_legitime_efface_toujours_son_fichier(client):
    """Le confinement ne doit pas casser l'usage normal."""
    pid = client.post("/api/projets", json={"nom": "Normal"}).json()["projet"]["id"]
    client.post(f"/api/projets/{pid}/documents/dp6",
                files={"fichier": ("dp6.png", _png_bytes(), "image/png")})
    chemin = config.PROJETS_DIR / f"{pid}.assets" / "uploads" / "dp6.png"
    assert chemin.exists()

    assert client.delete(f"/api/projets/{pid}/documents/dp6").status_code == 200
    assert not chemin.exists()


# ------------------------------------------- 2. confinement : lecteurs de pièces

def test_l_assemblage_ignore_une_piece_hors_espace(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROJETS_DIR", tmp_path / "PROJETS")
    intrus = tmp_path / "secret.png"
    intrus.write_bytes(_png_bytes())
    for fichier in ("../secret.png", str(intrus)):
        projet = {"id": "p", "documents": {"dp6": {"fichier": fichier}}}
        assert assemblage._fichier_document(projet, "dp6") is None


def test_les_notices_erp_ignorent_un_plan_hors_espace(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROJETS_DIR", tmp_path / "PROJETS")
    intrus = tmp_path / "secret.png"
    intrus.write_bytes(_png_bytes())
    projet = {"id": "p", "documents": {"dp2": {"fichier": str(intrus)}}}
    assert notices_erp._images_plan_masse(projet) == []


# ------------------------------------- 3. identifiant de projet dans les URLs

ROUTES_PAR_PROJET = [
    ("get", "/api/projets/{id}/cerfa.pdf"),
    ("get", "/api/projets/{id}/cerfa-erp.pdf"),
    ("get", "/api/projets/{id}/notice-securite.pdf"),
    ("get", "/api/projets/{id}/dossier.pptx"),
    ("get", "/api/projets/{id}/dossier.pdf"),
    ("get", "/api/projets/{id}/dossier-pages.zip"),
    ("post", "/api/projets/{id}/dossier/pdf"),
    ("post", "/api/projets/{id}/dossier/pages"),
    ("get", "/api/projets/{id}/planches"),
    ("get", "/api/projets/{id}/planches/dp1_situation.png"),
    ("get", "/api/projets/{id}/documents/dp6"),
]


@pytest.mark.parametrize("methode, route", ROUTES_PAR_PROJET)
def test_un_identifiant_qui_remonte_les_dossiers_est_refuse(client, tmp_path, methode, route):
    """`%5C` devient `\\` une fois décodé : un séparateur sous Windows.

    Ces routes construisaient le dossier du projet AVANT tout chargement, donc
    sans contrôle de l'identifiant, et le créaient au passage hors de l'espace
    (relevé le 20/11/2026).
    """
    intrus = "..%5C..%5Cfaille-hors-espace"

    r = getattr(client, methode)(route.format(id=intrus))

    assert r.status_code == 400
    assert "Identifiant de projet invalide" in r.json()["detail"]
    assert not (tmp_path.parent / "faille-hors-espace.assets").exists()
    assert not (tmp_path / "faille-hors-espace.assets").exists()


@pytest.mark.parametrize("identifiant", ["Majuscules", "a.b", "a_b"])
def test_un_identifiant_mal_forme_est_refuse_avant_la_route(client, identifiant):
    r = client.get(f"/api/projets/{identifiant}/cerfa.pdf")
    assert r.status_code == 400


def test_un_identifiant_valide_atteint_toujours_la_route(client):
    """Le contrôle ne doit pas casser l'usage normal : projet inconnu = 404."""
    r = client.get("/api/projets/projet-inconnu-abc123/cerfa.pdf")
    assert r.status_code == 404


@pytest.mark.parametrize("identifiant", ["..\\x", "../x", "a.b", "", None])
def test_assets_dir_refuse_un_identifiant_mal_forme(tmp_path, monkeypatch, identifiant):
    """Dernier rempart, pour tout appelant qui ne passe pas par une route."""
    monkeypatch.setattr(config, "PROJETS_DIR", tmp_path / "PROJETS")
    with pytest.raises(config.IdentifiantProjetInvalide):
        config.assets_dir(identifiant)
    assert not any(tmp_path.rglob("*.assets"))


def test_la_coherence_signale_une_piece_hors_espace(tmp_path, monkeypatch):
    """Hors de l'espace, la pièce ne sortira pas au dossier : la checklist ne
    doit pas la compter comme fournie sans le dire."""
    monkeypatch.setattr(config, "PROJETS_DIR", tmp_path / "PROJETS")
    intrus = tmp_path / "secret.png"
    intrus.write_bytes(b"x")
    projet = {"id": "p", "documents": {"dp6": {"fichier": str(intrus),
                                               "nom_fichier": "secret.png"}}}

    codes = [a["code"] for a in coherence._controler_fichiers(projet)]

    assert "fichier_manquant_dp6" in codes
