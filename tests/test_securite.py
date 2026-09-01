"""Authentification et refus de démarrer exposé sans protection.

Le test qui compte est test_refus_de_demarrer_expose_sans_authentification :
il vérifie qu'on ne PEUT PAS publier l'outil sans l'avoir protégé. Les données
des dossiers contiennent le nom, l'adresse électronique, le téléphone et le
SIRET du maître d'ouvrage, et une route DELETE efface un dossier client
complet. Une protection qu'on peut oublier d'activer n'en est pas une.
"""
from __future__ import annotations

import base64

import pytest
from fastapi.testclient import TestClient

from app import config, securite
from app.main import app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROJETS_DIR", tmp_path / "PROJETS")
    return TestClient(app)


@pytest.fixture(autouse=True)
def environnement_propre(monkeypatch):
    """Aucun compte ni délégation hérités d'un autre test ou du poste."""
    monkeypatch.delenv("GVDP_COMPTES", raising=False)
    monkeypatch.delenv("GVDP_AUTH_DELEGUEE", raising=False)
    monkeypatch.delenv("GVDP_HOTE", raising=False)


def entete_basic(nom: str, mot_de_passe: str) -> dict:
    jeton = base64.b64encode(f"{nom}:{mot_de_passe}".encode("utf-8")).decode()
    return {"Authorization": f"Basic {jeton}"}


# ------------------------------------------------------ contrôle de démarrage

def test_refus_de_demarrer_expose_sans_authentification():
    """LA garantie de ce module : pas d'exposition accidentelle.

    Écouter sur 0.0.0.0 rend l'outil joignable depuis le réseau. Sans compte
    ni authentification déléguée, le démarrage doit échouer, pas réussir
    discrètement.
    """
    # Act / Assert
    with pytest.raises(securite.ConfigurationDangereuse) as erreur:
        securite.verifier_configuration("0.0.0.0")

    # le message doit expliquer QUOI faire, pas seulement refuser
    message = str(erreur.value)
    assert "GVDP_COMPTES" in message
    assert "GVDP_AUTH_DELEGUEE" in message
    assert "données personnelles" in message


def test_ecoute_locale_sans_compte_est_autorisee():
    """L'usage au poste ne doit pas être compliqué par la sécurité."""
    assert securite.verifier_configuration("127.0.0.1") == "local"
    assert securite.verifier_configuration("localhost") == "local"


def test_exposition_autorisee_avec_des_comptes(monkeypatch):
    monkeypatch.setenv("GVDP_COMPTES", f"florent:{securite.empreinte('secret')}")
    assert securite.verifier_configuration("0.0.0.0") == "comptes"


def test_exposition_autorisee_si_l_hebergeur_authentifie(monkeypatch):
    """Azure Container Apps EasyAuth / Entra ID, ou un proxy d'entreprise."""
    monkeypatch.setenv("GVDP_AUTH_DELEGUEE", "1")
    assert securite.verifier_configuration("0.0.0.0") == "delegue"


# --------------------------------------------------------------- empreintes

def test_empreinte_verifiee_correctement():
    reference = securite.empreinte("Bon mot de passe 42")
    assert securite._verifier("Bon mot de passe 42", reference)
    assert not securite._verifier("Mauvais", reference)


def test_deux_empreintes_du_meme_mot_de_passe_different():
    """Sel aléatoire : deux comptes au même mot de passe ne se voient pas."""
    assert securite.empreinte("identique") != securite.empreinte("identique")


def test_le_mot_de_passe_n_apparait_pas_dans_l_empreinte():
    reference = securite.empreinte("MotDePasseTresParticulier")
    assert "MotDePasseTresParticulier" not in reference


def test_empreinte_malformee_refusee():
    """Une valeur tronquée dans GVDP_COMPTES ne doit jamais laisser passer."""
    assert not securite._verifier("peu importe", "sans-separateur")
    assert not securite._verifier("peu importe", "")


# ---------------------------------------------------------------- middleware

def test_sans_compte_configure_tout_passe(client):
    """Mode local : rien ne change pour Florent à son poste."""
    assert client.get("/api/projets").status_code == 200


def test_avec_comptes_une_requete_nue_est_refusee(client, monkeypatch):
    # Arrange
    monkeypatch.setenv("GVDP_COMPTES", f"florent:{securite.empreinte('secret')}")

    # Act
    r = client.get("/api/projets")

    # Assert
    assert r.status_code == 401
    assert r.headers["www-authenticate"].startswith("Basic")


def test_avec_comptes_les_bons_identifiants_passent(client, monkeypatch):
    # Arrange
    monkeypatch.setenv("GVDP_COMPTES", f"florent:{securite.empreinte('secret')}")

    # Act / Assert
    assert client.get("/api/projets", headers=entete_basic("florent", "secret")).status_code == 200


def test_mauvais_mot_de_passe_refuse(client, monkeypatch):
    monkeypatch.setenv("GVDP_COMPTES", f"florent:{securite.empreinte('secret')}")
    assert client.get("/api/projets",
                      headers=entete_basic("florent", "autre")).status_code == 401


def test_utilisateur_inconnu_refuse(client, monkeypatch):
    monkeypatch.setenv("GVDP_COMPTES", f"florent:{securite.empreinte('secret')}")
    assert client.get("/api/projets",
                      headers=entete_basic("inconnu", "secret")).status_code == 401


def test_entete_malformee_refusee(client, monkeypatch):
    monkeypatch.setenv("GVDP_COMPTES", f"florent:{securite.empreinte('secret')}")
    for mauvaise in ("Basic pas-du-base64!", "Bearer jeton", "Basic", ""):
        r = client.get("/api/projets", headers={"Authorization": mauvaise})
        assert r.status_code == 401, mauvaise


def test_la_route_de_sante_reste_libre(client, monkeypatch):
    """La sonde de l'hébergeur doit répondre sans compte, sinon le conteneur
    est déclaré mort en permanence."""
    monkeypatch.setenv("GVDP_COMPTES", f"florent:{securite.empreinte('secret')}")
    r = client.get("/api/sante")
    assert r.status_code == 200
    assert r.json()["authentification"] in ("local", "comptes", "delegue")


def test_la_suppression_d_un_projet_est_protegee(client, monkeypatch):
    """Route la plus destructrice de l'application : elle efface un dossier
    client complet. Elle ne doit jamais être joignable sans authentification."""
    # Arrange : projet créé avant l'activation des comptes
    pid = client.post("/api/projets", json={"nom": "Client"}).json()["projet"]["id"]
    monkeypatch.setenv("GVDP_COMPTES", f"florent:{securite.empreinte('secret')}")

    # Act
    r = client.delete(f"/api/projets/{pid}")

    # Assert
    assert r.status_code == 401
    assert (config.PROJETS_DIR / f"{pid}.json").exists()


# ------------------------------------------------------------- traçabilité

def test_l_utilisateur_authentifie_devient_l_auteur(client, monkeypatch):
    """La traçabilité des modifications suit l'identité réelle, pas le compte
    Windows du serveur : en hébergé, tout le monde partagerait le même."""
    # Arrange
    monkeypatch.setenv("GVDP_COMPTES", f"hajar:{securite.empreinte('secret')}")

    # Act
    r = client.post("/api/projets", json={"nom": "Projet BE"},
                    headers=entete_basic("hajar", "secret"))

    # Assert
    assert r.json()["projet"]["modifie_par"] == "hajar"


def test_un_entete_utilisateur_falsifie_est_ecrase(client, monkeypatch):
    """Un client ne doit pas pouvoir s'attribuer une autre identité en
    envoyant lui-même X-Utilisateur."""
    # Arrange
    monkeypatch.setenv("GVDP_COMPTES", f"hajar:{securite.empreinte('secret')}")

    # Act
    r = client.post("/api/projets", json={"nom": "Projet BE"},
                    headers={**entete_basic("hajar", "secret"),
                             "X-Utilisateur": "florent"})

    # Assert
    assert r.json()["projet"]["modifie_par"] == "hajar"


# ------------------------------------------- détection de l'adresse d'écoute

def test_l_hote_est_lu_sur_la_ligne_de_commande(monkeypatch):
    """Se fier à la seule variable GVDP_HOTE rendrait la garantie décorative :
    personne ne la renseigne en tapant `uvicorn --host 0.0.0.0`."""
    # Arrange
    monkeypatch.setattr(
        securite.sys, "argv",
        ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8420"])

    # Act / Assert
    assert securite.hote_effectif() == "0.0.0.0"
    assert not securite.ecoute_locale()
    with pytest.raises(securite.ConfigurationDangereuse):
        securite.verifier_configuration()


def test_l_hote_est_lu_dans_la_forme_accolee(monkeypatch):
    monkeypatch.setattr(securite.sys, "argv", ["uvicorn", "--host=0.0.0.0"])
    assert securite.hote_effectif() == "0.0.0.0"


def test_la_variable_uvicorn_est_prise_en_compte(monkeypatch):
    """uvicorn lit lui-même UVICORN_HOST : on doit voir la même chose que lui."""
    monkeypatch.setattr(securite.sys, "argv", ["uvicorn"])
    monkeypatch.setenv("UVICORN_HOST", "0.0.0.0")
    assert securite.hote_effectif() == "0.0.0.0"


def test_par_defaut_l_ecoute_est_locale(monkeypatch):
    monkeypatch.setattr(securite.sys, "argv", ["pytest"])
    assert securite.hote_effectif() == "127.0.0.1"
    assert securite.ecoute_locale()
