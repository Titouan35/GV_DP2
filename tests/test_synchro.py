"""Dossier lié : le poste garde les dossiers, le serveur une copie de travail.

Ce que ces tests protègent, par ordre d'importance :

1. Le confinement. Les chemins arrivent du navigateur : aucun ne doit sortir
   des fichiers d'UN projet dans l'espace du visiteur.
2. L'isolation. Une copie envoyée par un visiteur reste invisible des autres.
3. Le poste. Ces routes n'existent pas au poste, où l'espace est le vrai
   dossier PROJETS : une suppression « miroir » y effacerait des fichiers réels.
4. L'aller-retour. Ce qui est envoyé se relit, et une écriture du serveur se
   voit dans l'état, sinon le navigateur ne saurait pas quoi rapatrier.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app import session_web
from app.main import app

PID = "parking-nord-abc123"


@pytest.fixture()
def web(tmp_path, monkeypatch):
    monkeypatch.setenv("GVDP_MODE", "web")
    monkeypatch.setenv("GVDP_WEB_DIR", str(tmp_path / "espaces"))
    monkeypatch.setattr(session_web, "_derniere_purge", 0.0)
    return tmp_path / "espaces"


def fiche(pid=PID, nom="Parking nord"):
    return json.dumps({"id": pid, "nom": nom, "date_modification": "2026-11-20T10:00:00"}).encode()


def envoyer(client, chemin, contenu, mtime=None, pid=PID):
    params = {"chemin": chemin}
    if mtime is not None:
        params["mtime"] = mtime
    return client.put(f"/api/synchro/{pid}/fichier", params=params, content=contenu)


# ----------------------------------------------------------------- confinement

@pytest.mark.parametrize("chemin", [
    "../evil.json",
    f"{PID}.assets/../../evil.txt",
    f"{PID}.assets/./x.png",
    "/etc/passwd",
    "C:/Windows/win.ini",
    f"{PID}.assets\\..\\x",
    "autre-projet.json",
    "autre-projet.assets/x.png",
    f"{PID}.json/x",
    f"{PID}.assets/",
    f"{PID}.assets//x",
    f"{PID}.assets/x.tmp",
    "",
])
def test_un_chemin_hors_du_projet_est_refuse(web, chemin):
    client = TestClient(app)
    assert envoyer(client, chemin, b"x").status_code in (400, 422)
    assert client.get(f"/api/synchro/{PID}/fichier", params={"chemin": chemin}).status_code in (400, 422)
    assert client.delete(f"/api/synchro/{PID}/fichier", params={"chemin": chemin}).status_code in (400, 422)


def test_un_identifiant_de_projet_fantaisiste_est_refuse(web):
    client = TestClient(app)
    assert client.get("/api/synchro/..%2F..").status_code in (400, 404)
    assert client.get("/api/synchro/ABC").status_code == 400


def test_une_fiche_d_un_autre_projet_n_entre_pas(web):
    """Une copie de conflit renommée ferait échouer tout le projet ensuite."""
    client = TestClient(app)
    r = envoyer(client, f"{PID}.json", fiche(pid="autre-projet-000000"))
    assert r.status_code == 422
    assert client.get(f"/api/projets/{PID}").status_code == 404


def test_une_fiche_illisible_n_entre_pas(web):
    client = TestClient(app)
    assert envoyer(client, f"{PID}.json", b"{pas du json").status_code == 422
    assert client.get(f"/api/synchro/{PID}").json()["fichiers"] == []


# ------------------------------------------------------------------- isolation

def test_la_copie_d_un_visiteur_est_invisible_des_autres(web):
    a, b = TestClient(app), TestClient(app)
    assert envoyer(a, f"{PID}.json", fiche()).status_code == 200
    assert envoyer(a, f"{PID}.assets/uploads/plan.pdf", b"%PDF").status_code == 200

    assert b.get(f"/api/synchro/{PID}").json()["fichiers"] == []
    assert b.get(f"/api/synchro/{PID}/fichier",
                 params={"chemin": f"{PID}.assets/uploads/plan.pdf"}).status_code == 404
    assert b.get(f"/api/projets/{PID}").status_code == 404


# ------------------------------------------------------------------------ poste

def test_au_poste_la_synchro_n_existe_pas(monkeypatch):
    # Le refus doit précéder tout accès disque : au poste, l'espace est le
    # vrai dossier PROJETS.
    monkeypatch.delenv("GVDP_MODE", raising=False)
    client = TestClient(app)
    assert client.get(f"/api/synchro/{PID}").status_code == 404
    assert envoyer(client, f"{PID}.json", fiche()).status_code == 404
    assert client.delete(f"/api/synchro/{PID}/fichier",
                         params={"chemin": f"{PID}.json"}).status_code == 404
    assert client.post("/api/synchro/resumes", json={"projets": []}).status_code == 404


# ------------------------------------------------------------------ aller-retour

def test_un_projet_envoye_s_ouvre_et_se_relit(web):
    client = TestClient(app)
    assert envoyer(client, f"{PID}.json", fiche(), mtime=1_700_000_000_123).status_code == 200
    r = envoyer(client, f"{PID}.assets/uploads/photo.jpg", b"JPEGDATA", mtime=1_700_000_000_456)
    assert r.json() == {"chemin": f"{PID}.assets/uploads/photo.jpg", "taille": 8,
                        "mtime": 1_700_000_000_456}

    assert client.get(f"/api/projets/{PID}").json()["projet"]["nom"] == "Parking nord"
    fichiers = {f["chemin"]: f for f in client.get(f"/api/synchro/{PID}").json()["fichiers"]}
    assert fichiers[f"{PID}.json"]["mtime"] == 1_700_000_000_123
    assert fichiers[f"{PID}.assets/uploads/photo.jpg"]["taille"] == 8
    relu = client.get(f"/api/synchro/{PID}/fichier",
                      params={"chemin": f"{PID}.assets/uploads/photo.jpg"})
    assert relu.content == b"JPEGDATA"


def test_une_ecriture_du_serveur_se_voit_dans_l_etat(web):
    """Sans cela, le navigateur ne saurait pas qu'il doit rapatrier la fiche."""
    client = TestClient(app)
    envoyer(client, f"{PID}.json", fiche(), mtime=1_000)
    projet = client.get(f"/api/projets/{PID}").json()["projet"]

    projet["nom"] = "Parking nord, tranche 2"
    assert client.put(f"/api/projets/{PID}", json=projet).status_code == 200

    entree = next(f for f in client.get(f"/api/synchro/{PID}").json()["fichiers"]
                  if f["chemin"] == f"{PID}.json")
    assert entree["mtime"] != 1_000
    relu = client.get(f"/api/synchro/{PID}/fichier", params={"chemin": f"{PID}.json"})
    assert json.loads(relu.content)["nom"] == "Parking nord, tranche 2"


def test_oublier_ne_retire_que_la_copie_serveur(web):
    client = TestClient(app)
    envoyer(client, f"{PID}.json", fiche())
    envoyer(client, f"{PID}.assets/a.png", b"a")
    assert client.delete(f"/api/synchro/{PID}/fichier",
                         params={"chemin": f"{PID}.assets/a.png"}).json() == {"ok": True}
    chemins = [f["chemin"] for f in client.get(f"/api/synchro/{PID}").json()["fichiers"]]
    assert chemins == [f"{PID}.json"]


def test_horodatage_aligne_la_date_de_la_copie(web):
    client = TestClient(app)
    envoyer(client, f"{PID}.json", fiche(), mtime=1_000)
    lue = {"taille": len(fiche()), "mtime": 1_000, "vers": 1_650_000_000_000}
    r = client.post(f"/api/synchro/{PID}/horodatage",
                    json={"dates": {f"{PID}.json": lue, f"{PID}.lock": lue}})
    assert r.json()["fichiers"] == [{"chemin": f"{PID}.json", "taille": len(fiche()),
                                     "mtime": 1_650_000_000_000}]


def test_horodatage_ne_masque_pas_une_version_plus_recente(web):
    """Le serveur a réécrit la fiche après la lecture du navigateur : lui
    donner l'ancienne date ferait croire au navigateur qu'il l'a déjà."""
    client = TestClient(app)
    envoyer(client, f"{PID}.json", fiche(), mtime=1_000)
    envoyer(client, f"{PID}.json", fiche(nom="Parking nord bis"), mtime=2_000)

    r = client.post(f"/api/synchro/{PID}/horodatage", json={"dates": {
        f"{PID}.json": {"taille": len(fiche()), "mtime": 1_000, "vers": 9_000}}})

    assert r.json()["fichiers"] == []
    entree = client.get(f"/api/synchro/{PID}").json()["fichiers"][0]
    assert entree["mtime"] == 2_000


def test_les_fichiers_temporaires_ne_figurent_pas_dans_l_etat(web):
    client = TestClient(app)
    envoyer(client, f"{PID}.json", fiche())
    dossier = next(d for d in web.iterdir() if d.is_dir())
    (dossier / f"{PID}.assets").mkdir(exist_ok=True)
    (dossier / f"{PID}.assets" / "x.png.ab12.tmp").write_bytes(b"x")
    (dossier / f"{PID}.assets" / "~$dossier.pptx").write_bytes(b"x")
    chemins = [f["chemin"] for f in client.get(f"/api/synchro/{PID}").json()["fichiers"]]
    assert chemins == [f"{PID}.json"]


def test_un_fichier_trop_gros_est_refuse(web, monkeypatch):
    from app.api import routes_synchro
    monkeypatch.setattr(routes_synchro, "TAILLE_MAX", 10)
    client = TestClient(app)
    assert envoyer(client, f"{PID}.assets/gros.bin", b"x" * 11).status_code == 413
    assert client.get(f"/api/synchro/{PID}").json()["fichiers"] == []


# ---------------------------------------------------------------------- liste

def test_la_liste_se_calcule_sans_rien_ecrire(web):
    client = TestClient(app)
    r = client.post("/api/synchro/resumes", json={"projets": [
        json.loads(fiche()), {"pas": "un projet", "parcelles": 3}, "texte",
    ]})
    resumes = r.json()["resumes"]
    assert resumes[0]["id"] == PID and resumes[0]["nom"] == "Parking nord"
    assert resumes[0]["completude"]["total"] > 0
    assert resumes[2] is None
    # rien n'a été déposé dans l'espace
    assert client.get("/api/projets").json()["projets"] == []
