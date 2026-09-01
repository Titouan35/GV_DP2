"""Mode partagé entre postes : dossier commun, présence, traçabilité.

Chaque poste exécute son propre serveur sur un dossier OneDrive commun : les
verrous internes ne se voient pas d'un poste à l'autre, d'où le fichier de
présence (même principe que Word sur SharePoint).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app import config, verrou
from app.main import app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROJETS_DIR", tmp_path / "PROJETS")
    return TestClient(app)


def _creer(client, nom="Projet partage") -> str:
    return client.post("/api/projets", json={"nom": nom}).json()["projet"]["id"]


# ------------------------------------------------------------------ dossier

def test_dossier_projets_surchargeable_par_env(tmp_path, monkeypatch):
    """GVDP_PROJETS_DIR déplace les projets vers le dossier d'équipe."""
    monkeypatch.setenv("GVDP_PROJETS_DIR", str(tmp_path / "equipe"))
    assert config._projets_dir() == tmp_path / "equipe"


def test_dossier_projets_par_fichier_de_config(tmp_path, monkeypatch):
    monkeypatch.delenv("GVDP_PROJETS_DIR", raising=False)
    monkeypatch.setattr(config, "REPO_ROOT", tmp_path)
    (tmp_path / "gvdp.config.json").write_text(
        json.dumps({"projets_dir": str(tmp_path / "commun")}), encoding="utf-8")
    assert config._projets_dir() == tmp_path / "commun"


def test_config_illisible_retombe_sur_le_dossier_local(tmp_path, monkeypatch):
    monkeypatch.delenv("GVDP_PROJETS_DIR", raising=False)
    monkeypatch.setattr(config, "REPO_ROOT", tmp_path)
    (tmp_path / "gvdp.config.json").write_text("{ casse", encoding="utf-8")
    assert config._projets_dir() == tmp_path / "PROJETS"


# ------------------------------------------------------------------ présence

def test_presence_posee_puis_liberee(client):
    pid = _creer(client)
    etat = client.post(f"/api/projets/{pid}/verrou").json()
    assert etat["a_moi"] and etat["detenteur"]
    assert (config.PROJETS_DIR / f"{pid}.lock").exists()

    client.delete(f"/api/projets/{pid}/verrou")
    assert not (config.PROJETS_DIR / f"{pid}.lock").exists()


def test_presence_d_un_collegue_signalee(client):
    """Un autre poste détient le dossier : on le signale au lieu de voler."""
    pid = _creer(client)
    verrou.poser(pid, "sophie")
    etat = client.post(f"/api/projets/{pid}/verrou",
                       headers={"X-Utilisateur": "marc"}).json()
    assert not etat["a_moi"] and etat["detenteur"] == "sophie"
    # lire le projet le signale aussi, sans rien modifier
    lu = client.get(f"/api/projets/{pid}", headers={"X-Utilisateur": "marc"}).json()
    assert lu["verrou"]["detenteur"] == "sophie" and not lu["verrou"]["a_moi"]


def test_prendre_la_main_explicitement(client):
    pid = _creer(client)
    verrou.poser(pid, "sophie")
    etat = client.post(f"/api/projets/{pid}/verrou?forcer=1",
                       headers={"X-Utilisateur": "marc"}).json()
    assert etat["a_moi"] and etat["detenteur"] == "marc"


def test_presence_perimee_liberee_automatiquement(client, monkeypatch):
    """Poste éteint ou onglet fermé brutalement : la présence expire."""
    pid = _creer(client)
    verrou.poser(pid, "sophie")
    vieux = (datetime.now() - timedelta(minutes=30)).isoformat(timespec="seconds")
    chemin = config.PROJETS_DIR / f"{pid}.lock"
    d = json.loads(chemin.read_text(encoding="utf-8"))
    d["date"] = vieux
    chemin.write_text(json.dumps(d), encoding="utf-8")

    assert verrou.lire(pid) is None                    # périmée
    etat = client.post(f"/api/projets/{pid}/verrou",
                       headers={"X-Utilisateur": "marc"}).json()
    assert etat["a_moi"]                               # reprise sans forcer


def test_liberer_ne_touche_pas_la_presence_d_autrui(client):
    pid = _creer(client)
    verrou.poser(pid, "sophie")
    client.delete(f"/api/projets/{pid}/verrou", headers={"X-Utilisateur": "marc"})
    assert (verrou.lire(pid) or {}).get("utilisateur") == "sophie"


def test_supprimer_un_projet_emporte_sa_presence(client):
    pid = _creer(client)
    client.post(f"/api/projets/{pid}/verrou")
    client.delete(f"/api/projets/{pid}")
    assert not (config.PROJETS_DIR / f"{pid}.lock").exists()


def test_verrou_refuse_sur_projet_inexistant(client):
    """Un identifiant fantaisiste ne doit pas créer de fichier de présence
    orphelin dans le dossier partagé."""
    r = client.post("/api/projets/nexiste-pas/verrou")
    assert r.status_code == 404
    assert not (config.PROJETS_DIR / "nexiste-pas.lock").exists()


def test_lock_ne_pollue_pas_la_liste_des_projets(client):
    pid = _creer(client)
    client.post(f"/api/projets/{pid}/verrou")
    projets = client.get("/api/projets").json()["projets"]
    assert len(projets) == 1 and projets[0]["id"] == pid
