"""Écritures simultanées sur un même projet : jamais de perte silencieuse.

Relevé à l'analyse du 20/11/2026. Seule la sauvegarde (PUT) tenait le verrou
du projet sur toute la séquence lire-modifier-écrire. Le dépôt d'une pièce,
la validation, la notice, le Cerfa… chargeaient le projet, travaillaient, puis
réécrivaient leur copie ENTIÈRE : une sauvegarde arrivée entre-temps était
écrasée sans un mot. Et le jeton du verrou optimiste (`date_modification`, à
la seconde) était identique pour deux écritures de la même seconde.

Les routes sont appelées directement depuis des fils d'exécution : c'est la
situation réelle (FastAPI exécute les routes synchrones dans un threadpool) et
cela évite de dépendre du comportement du TestClient en parallèle.
"""
from __future__ import annotations

import io
import threading

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from PIL import Image

from app import config
from app.api import routes_documents, routes_dossier, routes_projets
from app.main import app
from app.models import Projet

DELAI = 5  # secondes : garde-fou pour qu'un test raté ne fige pas la suite


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.delenv("GVDP_COMPTES", raising=False)
    monkeypatch.delenv("GVDP_AUTH_DELEGUEE", raising=False)
    monkeypatch.setattr(config, "PROJETS_DIR", tmp_path / "PROJETS")
    return TestClient(app)


def _png_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (60, 40), (120, 130, 140)).save(buf, "PNG")
    return buf.getvalue()


def _sauver_en_fond(pid: str, projet: Projet, resultats: dict) -> threading.Thread:
    def sauver():
        try:
            routes_projets.sauvegarder_projet(pid, projet)
            resultats["put"] = 200
        except HTTPException as exc:
            resultats["put"] = exc.status_code
    fil = threading.Thread(target=sauver, daemon=True)
    fil.start()
    return fil


def test_une_sauvegarde_pendant_un_depot_ne_l_efface_pas(client, monkeypatch):
    # Arrange : un projet, et l'état qu'en a le navigateur AVANT le dépôt
    pid = client.post("/api/projets", json={"nom": "Concurrence"}).json()["projet"]["id"]
    vu_par_le_navigateur = Projet.model_validate(
        client.get(f"/api/projets/{pid}").json()["projet"])

    # la lecture du plan de masse est retenue tant que le test ne la relâche pas
    entree, relache = threading.Event(), threading.Event()

    def lecture_lente(chemin):
        entree.set()
        relache.wait(DELAI)
        return None
    monkeypatch.setattr(routes_documents.lecture_plan, "lire_plan_masse", lecture_lente)

    resultats: dict = {}
    depot = threading.Thread(daemon=True, target=lambda: resultats.update(
        depot=routes_documents._enregistrer_depot(pid, "dp2", ".png", "plan.png", _png_bytes())))
    depot.start()
    assert entree.wait(DELAI), "le dépôt n'a pas atteint la lecture du plan"

    # Act : l'autosave part PENDANT le dépôt, depuis l'état d'avant
    vu_par_le_navigateur.ombriere.puissance_kwc = 250
    put = _sauver_en_fond(pid, vu_par_le_navigateur, resultats)
    put.join(0.5)
    attendait = put.is_alive()
    relache.set()
    depot.join(DELAI)
    put.join(DELAI)

    # Assert
    assert attendait, "la sauvegarde doit attendre la fin du dépôt, pas le doubler"
    relu = client.get(f"/api/projets/{pid}").json()["projet"]
    assert "dp2" in relu["documents"], "le dépôt a été écrasé par la sauvegarde"
    # elle partait d'un état antérieur au dépôt : refus explicite, pas d'écrasement
    assert resultats["put"] == 409


def test_deux_ecritures_dans_la_meme_seconde_changent_le_jeton(client):
    """Le jeton du verrou optimiste doit changer à CHAQUE écriture."""
    pid = client.post("/api/projets", json={"nom": "Jeton"}).json()["projet"]["id"]
    avant = client.get(f"/api/projets/{pid}").json()["projet"]

    apres = client.post(f"/api/projets/{pid}/documents/dp6",
                        files={"fichier": ("dp6.png", _png_bytes(), "image/png")}
                        ).json()["projet"]

    assert apres["date_modification"] > avant["date_modification"]
    # une sauvegarde partie de l'état d'avant le dépôt est donc refusée
    r = client.put(f"/api/projets/{pid}", json=avant)
    assert r.status_code == 409
    assert "dp6" in client.get(f"/api/projets/{pid}").json()["projet"]["documents"]


def test_une_route_en_echec_n_ecrit_rien(client):
    """projet_a_modifier n'écrit pas si le bloc lève une exception."""
    pid = client.post("/api/projets", json={"nom": "Echec"}).json()["projet"]["id"]
    avant = client.get(f"/api/projets/{pid}").json()["projet"]

    r = client.put(f"/api/projets/{pid}/ombriere/type", json={"famille": "inconnue"})

    assert r.status_code == 400
    assert client.get(f"/api/projets/{pid}").json()["projet"] == avant


def test_le_verrou_est_reentrant():
    """Un `_sauver` appelé sous le verrou ne doit pas figer le projet."""
    verrou = routes_projets.verrou_projet("reentrance-test")
    with verrou:
        assert verrou.acquire(timeout=1)
        verrou.release()


def test_les_routes_d_ecriture_passent_par_projet_a_modifier():
    """Garde-fou : aucune route ne doit revenir au couple _charger/_sauver."""
    import inspect

    for module in (routes_documents, routes_dossier):
        source = inspect.getsource(module)
        assert "_sauver(" not in source, f"{module.__name__} réécrit le projet hors verrou"
