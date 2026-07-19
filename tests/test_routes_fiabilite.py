"""Routes documents / insertion (job de fond) / projets (verrou, purge) + export PDF.

Trous de couverture comblés le 19/07/2026 : jusqu'ici aucune route d'upload,
de génération ni l'export PDF n'était testée.
"""
from __future__ import annotations

import io
import subprocess
import threading
import time

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import config, export_pdf, insertion_ia
from app.main import app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROJETS_DIR", tmp_path / "PROJETS")
    return TestClient(app)


def _png_bytes(w=60, h=40, couleur=(120, 130, 140)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), couleur).save(buf, "PNG")
    return buf.getvalue()


def _creer_projet(client, nom="Test routes") -> str:
    r = client.post("/api/projets", json={"nom": nom})
    assert r.status_code == 200
    return r.json()["projet"]["id"]


# ------------------------------------------------------------------ documents

def test_upload_document_valide(client):
    pid = _creer_projet(client)
    r = client.post(f"/api/projets/{pid}/documents/dp7",
                    files={"fichier": ("photo.png", _png_bytes(), "image/png")})
    assert r.status_code == 200
    assert "dp7" in r.json()["projet"]["documents"]


def test_upload_signature_invalide_rejete(client):
    """Un fichier renommé .pdf qui n'en est pas un est refusé (magic bytes)."""
    pid = _creer_projet(client)
    r = client.post(f"/api/projets/{pid}/documents/dp2",
                    files={"fichier": ("plan.pdf", b"pas un pdf du tout", "application/pdf")})
    assert r.status_code == 400
    assert "correspond pas" in r.json()["detail"]


def test_upload_trop_gros_rejete(client, monkeypatch):
    from app.api import routes_documents
    monkeypatch.setattr(routes_documents, "TAILLE_MAX", 100)
    pid = _creer_projet(client)
    r = client.post(f"/api/projets/{pid}/documents/dp7",
                    files={"fichier": ("photo.png", _png_bytes(400, 300), "image/png")})
    assert r.status_code == 400
    assert "volumineux" in r.json()["detail"]


def test_upload_extension_inconnue_rejete(client):
    pid = _creer_projet(client)
    r = client.post(f"/api/projets/{pid}/documents/dp7",
                    files={"fichier": ("piece.docx", b"x", "application/octet-stream")})
    assert r.status_code == 400


# ------------------------------------------------------------------ projets

def test_verrou_optimiste_409(client):
    """Une sauvegarde partie d'un état périmé est refusée (multi-poste)."""
    pid = _creer_projet(client)
    p1 = client.get(f"/api/projets/{pid}").json()["projet"]
    # 1re sauvegarde : passe, et change date_modification sur le disque
    time.sleep(1.1)  # timespec="seconds" : la date doit réellement changer
    r1 = client.put(f"/api/projets/{pid}", json={**p1, "nom": "Version A"})
    assert r1.status_code == 200
    # 2e sauvegarde repartie de l'ANCIEN état : refusée
    r2 = client.put(f"/api/projets/{pid}", json={**p1, "nom": "Version B"})
    assert r2.status_code == 409
    assert "Rechargez" in r2.json()["detail"]
    # l'état sur disque est bien la version A
    assert client.get(f"/api/projets/{pid}").json()["projet"]["nom"] == "Version A"


def test_modifie_par_renseigne(client):
    pid = _creer_projet(client)
    assert client.get(f"/api/projets/{pid}").json()["projet"]["modifie_par"]


def test_nettoyer_purge_les_caches(client):
    pid = _creer_projet(client)
    assets = config.assets_dir(pid)
    (assets / "embed_photo.jpg").write_bytes(b"x" * 1000)
    (assets / "kit_coupe.png").write_bytes(b"x" * 500)
    dossier_ins = assets / "insertion"
    dossier_ins.mkdir()
    (dossier_ins / "insertion_orpheline.png").write_bytes(b"x" * 800)
    r = client.post(f"/api/projets/{pid}/nettoyer")
    assert r.status_code == 200
    d = r.json()
    assert d["fichiers_supprimes"] == 3
    assert d["octets_liberes"] == 2300
    assert not (assets / "embed_photo.jpg").exists()


def test_nettoyer_garde_les_references(client):
    """Les images de la galerie et les photos du site ne sont pas purgées."""
    pid = _creer_projet(client)
    assets = config.assets_dir(pid)
    dossier_ins = assets / "insertion"
    dossier_ins.mkdir()
    (dossier_ins / "insertion_gardee.png").write_bytes(_png_bytes())
    (dossier_ins / "site_photo.jpg").write_bytes(_png_bytes())
    p = client.get(f"/api/projets/{pid}").json()["projet"]
    p["insertion"]["images"] = [{"fichier": f"{pid}.assets/insertion/insertion_gardee.png"}]
    p["insertion"]["photos"] = [f"{pid}.assets/insertion/site_photo.jpg"]
    assert client.put(f"/api/projets/{pid}", json=p).status_code == 200
    client.post(f"/api/projets/{pid}/nettoyer")
    assert (dossier_ins / "insertion_gardee.png").exists()
    assert (dossier_ins / "site_photo.jpg").exists()


def test_suppression_projet_emporte_les_assets(client):
    pid = _creer_projet(client)
    assets = config.assets_dir(pid)
    (assets / "reste.png").write_bytes(b"x")
    assert client.delete(f"/api/projets/{pid}").status_code == 200
    assert not assets.exists()


def test_projet_corrompu_renvoie_422(client):
    pid = _creer_projet(client)
    (config.PROJETS_DIR / f"{pid}.json").write_text("{ tronqué", encoding="utf-8")
    r = client.get(f"/api/projets/{pid}")
    assert r.status_code == 422
    assert "illisible" in r.json()["detail"]


# ------------------------------------------------------------------ dossier (dépôt)

def test_depot_refuse_dossier_incomplet(client):
    pid = _creer_projet(client)
    r = client.post(f"/api/projets/{pid}/dossier?depot=1")
    assert r.status_code == 409
    assert "incomplet" in r.json()["detail"]


# ------------------------------------------------------------------ génération en fond

def _projet_avec_photo(client) -> str:
    pid = _creer_projet(client)
    r = client.post(f"/api/projets/{pid}/insertion/photos",
                    files=[("fichiers", ("site.png", _png_bytes(400, 300), "image/png"))])
    assert r.status_code == 200
    return pid


def test_generation_fond_bout_en_bout(client, monkeypatch, tmp_path):
    monkeypatch.setenv("GEMINI_API_KEY", "test")

    def faux_generer(projet, affinage="", prompt_override=""):
        return {"fichier": "x.png", "date": "2026-07-19T10:00:00",
                "etiquette": "visuel IA", "modele": "test", "prompt": "p",
                "controle": {"couverture": 0.9, "verdict": "ok"}, "essais": 1}

    monkeypatch.setattr(insertion_ia, "generer_image", faux_generer)
    pid = _projet_avec_photo(client)
    r = client.post(f"/api/projets/{pid}/insertion/generer", json={})
    assert r.status_code == 200 and r.json()["etat"] == "en_cours"

    for _ in range(100):   # le job tourne dans un thread : on attend la fin
        s = client.get(f"/api/projets/{pid}/insertion/generer/statut").json()
        if s["etat"] != "en_cours":
            break
        time.sleep(0.05)
    assert s["etat"] == "prete"
    assert s["image"]["fichier"] == "x.png"
    projet = client.get(f"/api/projets/{pid}").json()["projet"]
    assert projet["insertion"]["images"][0]["fichier"] == "x.png"
    assert projet["insertion"]["retenue"] == "x.png"
    assert projet["insertion"]["nb_images_generees"] == 1


def test_generation_fond_409_si_deja_en_cours(client, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test")
    feu_vert = threading.Event()

    def generer_bloquant(projet, affinage="", prompt_override=""):
        feu_vert.wait(timeout=10)
        return {"fichier": "y.png", "date": "d", "etiquette": "visuel IA",
                "modele": "test", "prompt": "p", "controle": None, "essais": 1}

    monkeypatch.setattr(insertion_ia, "generer_image", generer_bloquant)
    pid = _projet_avec_photo(client)
    assert client.post(f"/api/projets/{pid}/insertion/generer", json={}).status_code == 200
    # pendant que le job est bloqué : un 2e lancement est refusé (double dépense)
    r2 = client.post(f"/api/projets/{pid}/insertion/generer", json={})
    assert r2.status_code == 409
    feu_vert.set()
    for _ in range(100):
        if client.get(f"/api/projets/{pid}/insertion/generer/statut").json()["etat"] != "en_cours":
            break
        time.sleep(0.05)


def test_generation_fond_erreur_visible(client, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test")

    def generer_rate(projet, affinage="", prompt_override=""):
        raise insertion_ia.InsertionError("Quota Gemini atteint")

    monkeypatch.setattr(insertion_ia, "generer_image", generer_rate)
    pid = _projet_avec_photo(client)
    client.post(f"/api/projets/{pid}/insertion/generer", json={})
    for _ in range(100):
        s = client.get(f"/api/projets/{pid}/insertion/generer/statut").json()
        if s["etat"] != "en_cours":
            break
        time.sleep(0.05)
    assert s["etat"] == "erreur"
    assert "Quota" in s["erreur"]


def test_generation_sans_cle_refusee(client, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    pid = _projet_avec_photo(client)
    r = client.post(f"/api/projets/{pid}/insertion/generer", json={})
    assert r.status_code == 400
    assert "GEMINI_API_KEY" in r.json()["detail"]


# ------------------------------------------------------------------ export PDF

def test_export_pdf_remonte_stderr(tmp_path, monkeypatch):
    """Un échec de conversion remonte le stderr au lieu d'un message muet."""
    pptx = tmp_path / "d.pptx"
    pptx.write_bytes(b"x")

    def faux_run(*args, **kwargs):
        return subprocess.CompletedProcess(args, returncode=1, stdout="",
                                           stderr="COM indisponible : licence")

    monkeypatch.setattr(export_pdf.subprocess, "run", faux_run)
    monkeypatch.setattr(export_pdf.sys, "platform", "win32")
    with pytest.raises(RuntimeError, match="COM indisponible"):
        export_pdf.exporter_pdf(pptx)


def test_export_pdf_timeout_message_clair(tmp_path, monkeypatch):
    pptx = tmp_path / "d.pptx"
    pptx.write_bytes(b"x")

    def run_timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="powershell", timeout=240)

    monkeypatch.setattr(export_pdf.subprocess, "run", run_timeout)
    monkeypatch.setattr(export_pdf.sys, "platform", "win32")
    with pytest.raises(RuntimeError, match="pas répondu"):
        export_pdf.exporter_pdf(pptx)


def test_export_pdf_succes(tmp_path, monkeypatch):
    pptx = tmp_path / "d.pptx"
    pptx.write_bytes(b"x")

    def run_ok(*args, **kwargs):
        pptx.with_suffix(".pdf").write_bytes(b"%PDF-fake")
        return subprocess.CompletedProcess(args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(export_pdf.subprocess, "run", run_ok)
    monkeypatch.setattr(export_pdf.sys, "platform", "win32")
    assert export_pdf.exporter_pdf(pptx) == pptx.with_suffix(".pdf")
