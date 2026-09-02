"""Page de connexion et sessions, pour l'usage partagé en ligne.

Ajoutées le 02/09/2026. HTTP Basic convenait à un script, pas à une équipe :
fenêtre grise du navigateur, aucune déconnexion, identifiants renvoyés à chaque
requête. Ces tests vérifient que la session ne remplace pas la sécurité mais
s'ajoute à elle : un jeton falsifié, expiré ou visant un compte supprimé ne
doit jamais ouvrir l'application.
"""
from __future__ import annotations

import base64

import pytest
from fastapi.testclient import TestClient

from app import config, securite
from app.main import app


@pytest.fixture(autouse=True)
def environnement_propre(monkeypatch):
    monkeypatch.delenv("GVDP_COMPTES", raising=False)
    monkeypatch.delenv("GVDP_AUTH_DELEGUEE", raising=False)


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROJETS_DIR", tmp_path / "PROJETS")
    return TestClient(app)


@pytest.fixture()
def protege(monkeypatch):
    """Instance protégée par un compte, comme en hébergement."""
    monkeypatch.setenv("GVDP_COMPTES", f"agent-be:{securite.empreinte('bon-mot-de-passe')}")


# ------------------------------------------------------------------ formulaire

def test_sans_compte_la_page_de_connexion_renvoie_a_l_outil(client):
    """En local, il n'y a rien à quoi se connecter."""
    r = client.get("/connexion", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/"


def test_une_page_demandee_sans_session_mene_au_formulaire(client, protege):
    """Un humain doit voir un formulaire, pas la fenêtre grise du navigateur."""
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/connexion"


def test_une_requete_d_api_recoit_un_401_exploitable(client, protege):
    """Une redirection HTML casserait le fetch de l'interface."""
    r = client.get("/api/projets", follow_redirects=False)
    assert r.status_code == 401
    assert r.headers["www-authenticate"].startswith("Basic")


def test_le_formulaire_est_joignable_sans_etre_connecte(client, protege):
    r = client.get("/connexion")
    assert r.status_code == 200
    assert "Se connecter" in r.text


# ------------------------------------------------------------------ connexion

def test_de_bons_identifiants_ouvrent_une_session(client, protege):
    # Act
    r = client.post("/connexion",
                    data={"utilisateur": "agent-be", "motdepasse": "bon-mot-de-passe"},
                    follow_redirects=False)

    # Assert
    assert r.status_code == 303 and r.headers["location"] == "/"
    cookie = r.cookies.get(securite.COOKIE_SESSION)
    assert cookie and securite.verifier_session(cookie) == "agent-be"


def test_la_session_donne_acces_a_l_outil(client, protege):
    client.post("/connexion", data={"utilisateur": "agent-be", "motdepasse": "bon-mot-de-passe"})
    assert client.get("/api/projets").status_code == 200


def test_un_mauvais_mot_de_passe_est_refuse_sans_session(client, protege):
    r = client.post("/connexion",
                    data={"utilisateur": "agent-be", "motdepasse": "faux"},
                    follow_redirects=False)
    assert r.status_code == 401
    assert securite.COOKIE_SESSION not in r.cookies
    assert "incorrect" in r.text


def test_le_formulaire_ne_renvoie_pas_le_mot_de_passe_saisi(client, protege):
    """Un mot de passe réaffiché finirait dans le cache et l'historique."""
    r = client.post("/connexion",
                    data={"utilisateur": "agent-be", "motdepasse": "secret-a-ne-pas-fuiter"})
    assert "secret-a-ne-pas-fuiter" not in r.text


def test_le_cookie_est_httponly_et_samesite(client, protege):
    """HttpOnly : inaccessible au JavaScript, donc non volable par XSS."""
    r = client.post("/connexion",
                    data={"utilisateur": "agent-be", "motdepasse": "bon-mot-de-passe"},
                    follow_redirects=False)
    entete = r.headers["set-cookie"].lower()
    assert "httponly" in entete
    assert "samesite=lax" in entete


def test_le_cookie_est_secure_derriere_un_proxy_https(client, protege):
    """En hébergement, le TLS est terminé en amont : sans lire l'en-tête du
    proxy, le cookie partirait en clair."""
    r = client.post("/connexion",
                    data={"utilisateur": "agent-be", "motdepasse": "bon-mot-de-passe"},
                    headers={"X-Forwarded-Proto": "https"}, follow_redirects=False)
    assert "secure" in r.headers["set-cookie"].lower()


# ---------------------------------------------------------------- déconnexion

def test_la_deconnexion_efface_la_session(client, protege):
    # Arrange
    client.post("/connexion", data={"utilisateur": "agent-be", "motdepasse": "bon-mot-de-passe"})
    assert client.get("/api/projets").status_code == 200

    # Act
    client.post("/deconnexion")

    # Assert
    assert client.get("/api/projets").status_code == 401


# ------------------------------------------------------------------- jetons

def test_un_jeton_falsifie_est_rejete(protege):
    jeton = securite.creer_session("agent-be")
    assert securite.verifier_session(jeton[:-4] + "0000") is None


def test_un_jeton_expire_est_rejete(protege, monkeypatch):
    monkeypatch.setattr(securite, "DUREE_SESSION_H", -1)
    assert securite.verifier_session(securite.creer_session("agent-be")) is None


def test_un_jeton_visant_un_compte_supprime_est_rejete(monkeypatch):
    """Retirer un compte de GVDP_COMPTES doit couper l'accès immédiatement,
    sans attendre l'expiration de sa session."""
    # Arrange
    monkeypatch.setenv("GVDP_COMPTES", f"parti:{securite.empreinte('x')}")
    jeton = securite.creer_session("parti")
    assert securite.verifier_session(jeton) == "parti"

    # Act : le compte disparaît
    monkeypatch.setenv("GVDP_COMPTES", f"reste:{securite.empreinte('x')}")

    # Assert
    assert securite.verifier_session(jeton) is None


def test_un_jeton_d_une_autre_instance_est_rejete(protege, monkeypatch):
    """Le secret de signature est propre au serveur : un jeton fabriqué
    ailleurs ne doit pas ouvrir celui-ci."""
    # Arrange
    jeton = securite.creer_session("agent-be")

    # Act : autre secret, donc autre serveur
    monkeypatch.setattr(securite, "_SECRET", b"un-autre-secret-entierement")

    # Assert
    assert securite.verifier_session(jeton) is None


def test_un_jeton_vide_ou_malforme_ne_leve_pas(protege):
    for mauvais in (None, "", "sans-point", "a.b", "===.==="):
        assert securite.verifier_session(mauvais) is None


# --------------------------------------------------------------------- moi

def test_moi_donne_l_utilisateur_connecte(client, protege):
    client.post("/connexion", data={"utilisateur": "agent-be", "motdepasse": "bon-mot-de-passe"})
    corps = client.get("/api/moi").json()
    assert corps["utilisateur"] == "agent-be"
    assert corps["mode"] == "comptes"


def test_moi_ne_nomme_personne_en_local(client):
    corps = client.get("/api/moi").json()
    assert corps["utilisateur"] is None
    assert corps["mode"] == "local"


# --------------------------------------------------- HTTP Basic conservé

def test_http_basic_fonctionne_toujours_pour_les_scripts(client, protege):
    """Les sondes et les appels en ligne de commande ne passent pas par un
    formulaire : Basic reste accepté en parallèle."""
    jeton = base64.b64encode(b"agent-be:bon-mot-de-passe").decode()
    r = client.get("/api/projets", headers={"Authorization": f"Basic {jeton}"})
    assert r.status_code == 200
