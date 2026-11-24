"""File des opérations lourdes (app/memoire.py) : tenir dans les 512 Mo.

Ce que ces tests protègent :

1. Deux opérations lourdes ne tournent jamais en même temps, même sur deux
   projets différents : c'est ce qui doublait le pic mémoire.
2. Une opération lourde qui en appelle une autre ne se bloque pas elle-même
   (notices ERP en PDF → notices ERP).
3. Une erreur pendant l'opération rend sa place : la file ne se fige pas.
4. Une attente trop longue répond 503 avec un message lisible, jamais une
   requête suspendue sans fin.
5. Deux assemblages lancés en même temps par HTTP passent l'un après l'autre.
6. Chaque opération laisse une ligne de journal exploitable.
7. L'aperçu d'un plan PDF est plafonné comme les autres rendus.
"""
from __future__ import annotations

import io
import logging
import sys
import threading
import time

import pypdfium2 as pdfium
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import assemblage, config, memoire
from app.main import app

MM = 72 / 25.4


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROJETS_DIR", tmp_path / "PROJETS")
    return TestClient(app)


def _en_parallele(fonction, n=2):
    erreurs = []

    def lancer():
        try:
            fonction()
        except Exception as exc:          # remonté au test plus bas
            erreurs.append(exc)

    fils = [threading.Thread(target=lancer) for _ in range(n)]
    for f in fils:
        f.start()
    for f in fils:
        f.join(timeout=30)
    assert not any(f.is_alive() for f in fils), "opération bloquée"
    return erreurs


def test_une_seule_operation_lourde_a_la_fois():
    en_cours = 0
    maximum = 0
    garde = threading.Lock()

    def operation():
        nonlocal en_cours, maximum
        with memoire.operation_lourde("essai"):
            with garde:
                en_cours += 1
                maximum = max(maximum, en_cours)
            time.sleep(0.05)
            with garde:
                en_cours -= 1

    assert not _en_parallele(operation, n=4)
    assert maximum == 1


def test_reentrante_pour_un_meme_fil():
    termine = []

    def operation():
        with memoire.operation_lourde("externe"):
            with memoire.operation_lourde("interne"):
                termine.append(True)

    assert not _en_parallele(operation, n=1)
    assert termine == [True]


def test_une_erreur_rend_la_place():
    with pytest.raises(ValueError):
        with memoire.operation_lourde("en erreur"):
            raise ValueError("boum")
    # la place est libre : une nouvelle opération passe sans attendre
    debut = time.monotonic()
    with memoire.operation_lourde("suivante"):
        pass
    assert time.monotonic() - debut < 1


def test_attente_trop_longue_repond_503(client, monkeypatch):
    monkeypatch.setattr(memoire, "ATTENTE_MAX_S", 0.2)
    pid = client.post("/api/projets", json={"nom": "File pleine"}).json()["projet"]["id"]
    occupee = threading.Event()
    liberer = threading.Event()

    def tenir_la_place():
        with memoire.operation_lourde("longue"):
            occupee.set()
            liberer.wait(10)

    fil = threading.Thread(target=tenir_la_place)
    fil.start()
    try:
        assert occupee.wait(5)
        r = client.post(f"/api/projets/{pid}/dossier")
        assert r.status_code == 503
        assert "Réessayez" in r.json()["detail"]
        assert r.headers["Retry-After"]
    finally:
        liberer.set()
        fil.join(10)
    # place rendue : l'assemblage passe de nouveau
    assert client.post(f"/api/projets/{pid}/dossier").status_code == 200


def test_deux_assemblages_simultanes_passent_l_un_apres_l_autre(client, monkeypatch):
    """Deux projets DIFFÉRENTS : leur verrou de dossier ne les sépare pas,
    seule la file commune le fait."""
    pids = [client.post("/api/projets", json={"nom": f"Projet {i}"}).json()["projet"]["id"]
            for i in range(2)]
    en_cours = 0
    maximum = 0
    garde = threading.Lock()
    original = assemblage._generer_dossier_verrouille

    def espion(projet, projet_id):
        nonlocal en_cours, maximum
        with garde:
            en_cours += 1
            maximum = max(maximum, en_cours)
        try:
            time.sleep(0.1)
            return original(projet, projet_id)
        finally:
            with garde:
                en_cours -= 1

    monkeypatch.setattr(assemblage, "_generer_dossier_verrouille", espion)
    statuts = []
    a_assembler = iter(pids)
    verrou_iter = threading.Lock()

    def assembler():
        with verrou_iter:
            pid = next(a_assembler)
        statuts.append(client.post(f"/api/projets/{pid}/dossier").status_code)

    assert not _en_parallele(assembler, n=2)
    assert statuts == [200, 200]
    assert maximum == 1


def test_journal_d_une_operation(caplog):
    with caplog.at_level(logging.INFO, logger="app.memoire"):
        with memoire.operation_lourde("assemblage [P1]"):
            pass
    lignes = [r.getMessage() for r in caplog.records if r.name == "app.memoire"]
    assert any("assemblage [P1]" in m and "Mo" in m and "pic" in m for m in lignes)


def test_mesures_lisibles_sous_linux():
    if not sys.platform.startswith("linux"):
        pytest.skip("mesure /proc propre à Linux")
    rss = memoire.rss_mo()
    if rss is None:
        pytest.skip("/proc/self/status indisponible")
    assert rss > 0
    assert memoire.pic_mo() >= rss * 0.9


def _pdf_a0(chemin):
    doc = pdfium.PdfDocument.new()
    doc.new_page(841 * MM, 1189 * MM)
    doc.save(str(chemin))
    doc.close()
    return chemin.read_bytes()


def test_apercu_d_un_plan_a0_plafonne(client, tmp_path):
    pid = client.post("/api/projets", json={"nom": "Aperçu A0"}).json()["projet"]["id"]
    contenu = _pdf_a0(tmp_path / "a0.pdf")
    r = client.post(f"/api/projets/{pid}/documents/dp2",
                    files={"fichier": ("plan.pdf", contenu, "application/pdf")})
    assert r.status_code == 200, r.text
    r = client.get(f"/api/projets/{pid}/documents/dp2/image")
    assert r.status_code == 200
    with Image.open(io.BytesIO(r.content)) as im:
        assert max(im.size) <= 3400        # au lieu de 9 400 px à 200 dpi
