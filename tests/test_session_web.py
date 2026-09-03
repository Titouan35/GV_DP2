"""Mode web : un espace de travail éphémère par visiteur.

Le blocage de l'hébergement n'était ni Python, ni LibreOffice, ni
l'authentification : c'était le disque persistant. Le mode web supprime le
besoin d'en avoir un, en ne conservant RIEN côté serveur.

Ce que ces tests protègent, par ordre d'importance :

1. L'isolation. Deux visiteurs ne doivent jamais se voir. Un défaut ici
   exposerait le dossier d'un client à quelqu'un d'autre.
2. La signature du cookie. Sans elle, il suffirait d'essayer des identifiants
   pour tomber sur l'espace d'un autre.
3. La purge. Sans elle, « rien n'est conservé » serait faux.
4. L'absence d'effet au poste. Le mode local ne doit rien changer.
"""
from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from app import config, session_web
from app.main import app


@pytest.fixture()
def web(tmp_path, monkeypatch):
    """Application en mode web, espaces dans un dossier temporaire."""
    monkeypatch.setenv("GVDP_MODE", "web")
    monkeypatch.setenv("GVDP_WEB_DIR", str(tmp_path / "espaces"))
    monkeypatch.setattr(session_web, "_derniere_purge", 0.0)
    return tmp_path / "espaces"


def creer(client, nom) -> str:
    r = client.post("/api/projets", json={"nom": nom})
    assert r.status_code == 200
    return r.json()["projet"]["id"]


# ------------------------------------------------------------------ isolation

def test_deux_visiteurs_ne_voient_pas_les_memes_dossiers(web):
    """LE test de ce fichier. Un défaut ici exposerait le dossier d'un client."""
    # Arrange : deux clients distincts, donc deux cookies distincts
    a, b = TestClient(app), TestClient(app)

    # Act
    creer(a, "Dossier de A")
    creer(b, "Dossier de B")

    # Assert
    noms_a = [p["nom"] for p in a.get("/api/projets").json()["projets"]]
    noms_b = [p["nom"] for p in b.get("/api/projets").json()["projets"]]
    assert noms_a == ["Dossier de A"]
    assert noms_b == ["Dossier de B"]


def test_un_visiteur_ne_peut_pas_ouvrir_le_dossier_d_un_autre(web):
    # Arrange
    a, b = TestClient(app), TestClient(app)
    id_b = creer(b, "Dossier de B")

    # Act / Assert : même en connaissant l'identifiant, A ne l'atteint pas
    assert a.get(f"/api/projets/{id_b}").status_code == 404


def test_chaque_visiteur_a_son_dossier_sur_le_disque(web):
    a, b = TestClient(app), TestClient(app)
    creer(a, "A")
    creer(b, "B")
    assert len([d for d in web.iterdir() if d.is_dir()]) == 2


def test_le_meme_visiteur_retrouve_ses_dossiers(web):
    """Le cookie doit rendre la session stable d'une requête à l'autre."""
    client = TestClient(app)
    creer(client, "Mon dossier")
    assert [p["nom"] for p in client.get("/api/projets").json()["projets"]] == ["Mon dossier"]


# ------------------------------------------------------------------ signature

def test_un_cookie_falsifie_ne_donne_pas_acces_a_un_autre_espace(web):
    """Sans signature, essayer des identifiants au hasard suffirait."""
    # Arrange
    victime = TestClient(app)
    creer(victime, "Dossier confidentiel")
    identifiant = [d.name for d in web.iterdir() if d.is_dir()][0]

    # Act : l'attaquant connaît l'identifiant mais pas le secret de signature
    attaquant = TestClient(app)
    attaquant.cookies.set(session_web.COOKIE_ESPACE, f"{identifiant}.signature-inventee")
    projets = attaquant.get("/api/projets").json()["projets"]

    # Assert : il obtient un espace neuf, pas celui de la victime
    assert projets == []


def test_un_cookie_sans_signature_est_ignore(web):
    client = TestClient(app)
    client.cookies.set(session_web.COOKIE_ESPACE, "aaaaaaaaaaaaaaaa")
    assert client.get("/api/projets").json()["projets"] == []


def test_un_identifiant_de_traversee_est_refuse(web):
    """« ../ » dans le cookie ne doit pas sortir du dossier des espaces."""
    client = TestClient(app)
    client.cookies.set(session_web.COOKIE_ESPACE, "../../etc.signature")
    assert client.get("/api/projets").status_code == 200
    assert client.get("/api/projets").json()["projets"] == []


# ---------------------------------------------------------------------- purge

def test_un_espace_inactif_est_purge(web, monkeypatch):
    # Arrange
    client = TestClient(app)
    creer(client, "Vieux dossier")
    dossier = [d for d in web.iterdir() if d.is_dir()][0]
    (dossier / ".vivant").write_text("0", encoding="utf-8")   # 1970

    # Act
    supprimes = session_web.purger(force=True)

    # Assert
    assert supprimes == 1
    assert not dossier.exists()


def test_un_espace_actif_survit_a_la_purge(web):
    client = TestClient(app)
    creer(client, "Dossier en cours")
    assert session_web.purger(force=True) == 0
    assert len([d for d in web.iterdir() if d.is_dir()]) == 1


def test_la_purge_ne_tourne_pas_a_chaque_requete(web, monkeypatch):
    """Parcourir le disque à chaque appel coûterait cher pour rien."""
    session_web.purger(force=True)
    assert session_web.purger() == 0          # trop tôt, elle s'abstient


# ----------------------------------------------------- aucun effet au poste

def test_sans_le_mode_web_rien_ne_change(tmp_path, monkeypatch):
    """L'usage au poste ne doit pas être affecté : même dossier permanent."""
    # Arrange
    monkeypatch.delenv("GVDP_MODE", raising=False)
    monkeypatch.setattr(config, "PROJETS_DIR", tmp_path / "PROJETS")

    # Act / Assert
    assert not session_web.actif()
    assert config.espace() == tmp_path / "PROJETS"
    client = TestClient(app)
    creer(client, "Dossier du poste")
    assert (tmp_path / "PROJETS").exists()


def test_l_espace_revient_a_sa_valeur_apres_la_requete(web):
    """La variable de contexte doit être remise, sinon une requête web
    contaminerait le reste du processus."""
    # Arrange
    avant = config.espace()

    # Act
    TestClient(app).get("/api/projets")

    # Assert
    assert config.espace() == avant


# --------------------------------------------------------------- annonce

def test_l_interface_est_prevenue_du_caractere_ephemere(web):
    """Sans cet avertissement, quelqu'un monterait un dossier complet puis
    fermerait son onglet en croyant l'avoir enregistré."""
    corps = TestClient(app).get("/api/moi").json()
    assert corps["ephemere"] is True
    assert corps["duree_vie_h"] == session_web.DUREE_VIE_H


def test_au_poste_l_interface_n_annonce_rien(tmp_path, monkeypatch):
    monkeypatch.delenv("GVDP_MODE", raising=False)
    monkeypatch.setattr(config, "PROJETS_DIR", tmp_path / "PROJETS")
    assert TestClient(app).get("/api/moi").json()["ephemere"] is False


# ------------------------------------------------ le dossier reste produit

def test_un_dossier_complet_se_produit_dans_l_espace_ephemere(web):
    """Le mode web ne doit rien retirer à ce que l'outil sait faire."""
    # Arrange
    client = TestClient(app)
    pid = creer(client, "Dossier web")

    # Act
    r = client.post(f"/api/projets/{pid}/dossier")

    # Assert
    assert r.status_code == 200
    assert r.json()["fichier"].endswith(".pptx")
    telechargement = client.get(f"/api/projets/{pid}/dossier.pptx")
    assert telechargement.status_code == 200
    assert telechargement.content[:2] == b"PK"
