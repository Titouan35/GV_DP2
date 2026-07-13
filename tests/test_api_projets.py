"""CRUD projets via l'API FastAPI (persistance JSON dans un dossier temporaire)."""
import pytest
from fastapi.testclient import TestClient

from app import config
from app.main import app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROJETS_DIR", tmp_path / "PROJETS")
    return TestClient(app)


def test_sante(client):
    r = client.get("/api/sante")
    assert r.status_code == 200
    assert r.json()["app"] == "GV_DP"


def test_cycle_de_vie_projet(client):
    # create
    r = client.post("/api/projets", json={"nom": "Soufflenheim — golf"})
    assert r.status_code == 200
    corps = r.json()
    projet = corps["projet"]
    assert projet["id"].startswith("soufflenheim-golf-")
    assert corps["evaluation"]["regime"]["regime"] == "DP"

    # list
    r = client.get("/api/projets")
    assert len(r.json()["projets"]) == 1

    # update : la puissance bascule le régime en PC
    projet["ombriere"]["puissance_kwc"] = 3500
    r = client.put(f"/api/projets/{projet['id']}", json=projet)
    assert r.status_code == 200
    assert r.json()["evaluation"]["regime"]["regime"] == "PC"
    assert r.json()["projet"]["regime"] == "PC"

    # get
    r = client.get(f"/api/projets/{projet['id']}")
    assert r.status_code == 200
    assert r.json()["projet"]["ombriere"]["puissance_kwc"] == 3500

    # delete
    r = client.delete(f"/api/projets/{projet['id']}")
    assert r.status_code == 200
    assert client.get(f"/api/projets/{projet['id']}").status_code == 404


def test_id_invalide_rejete(client):
    r = client.get("/api/projets/../../etc/passwd")
    assert r.status_code in (400, 404)


def test_index_sert_html(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "GV_DP" in r.text
