"""Filet de sécurité du socle : la chaîne HTTP de livraison du dossier.

Pourquoi ce fichier existe. La vérification par mutation du 01/09/2026 a
montré que le dernier mètre du livrable n'était couvert nulle part dans les
fichiers socle : les tests appelaient `assemblage.generer_dossier()` en direct
et ne passaient jamais par les routes. Les deux règles les plus importantes de
cette chaîne ne tenaient que sur `tests/test_routes_fiabilite.py`, fichier qui
importe le module Insertion et qui ne survivra pas à son retrait.

Deux règles portantes sont donc reprises ici, dans un fichier qui, lui,
ne dépend pas de l'insertion :

1. Le contrôle bloquant du dépôt (`?depot=1` → 409 tant qu'une pièce manque).
   C'est le garde-fou qui empêche un dossier incomplet de partir en mairie.
2. L'ordre imposé de la chaîne : assembler avant de télécharger, assembler
   avant de convertir en PDF. Chaque maillon manquant doit répondre 404,
   jamais une trace technique.

Conventions reprises de tests/test_socle_api_documents.py : TestClient +
redirection de config.PROJETS_DIR vers tmp_path. Aucun appel réseau, aucune
écriture dans le vrai dossier PROJETS/.
"""
from __future__ import annotations

import io
import re

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import config, export_pdf
from app.main import app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROJETS_DIR", tmp_path / "PROJETS")
    return TestClient(app)


def _jpeg_bytes(w=60, h=40) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (200, 180, 60)).save(buf, "JPEG")
    return buf.getvalue()


def _pdf_bytes(w=200, h=150) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (255, 255, 255)).save(buf, "PDF")
    return buf.getvalue()


def _creer_projet(client, nom="Socle dossier") -> str:
    r = client.post("/api/projets", json={"nom": nom})
    assert r.status_code == 200
    return r.json()["projet"]["id"]


def _deposer(client, projet_id: str, code: str, contenu: bytes, nom: str):
    return client.post(
        f"/api/projets/{projet_id}/documents/{code}",
        files={"fichier": (nom, contenu)},
    )


# --------------------------------------------------------- contrôle du dépôt

def test_depot_bloque_tant_qu_une_piece_manque(client):
    """Règle métier majeure : `depot=1` refuse un dossier incomplet.

    Reprise ici depuis test_routes_fiabilite.py, qui ne survivra pas au
    retrait du module Insertion.
    """
    # Arrange : un projet vide, donc forcément incomplet
    projet_id = _creer_projet(client)

    # Act
    r = client.post(f"/api/projets/{projet_id}/dossier?depot=1")

    # Assert : refus explicite et motivé, pas une erreur technique
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert "incomplet" in detail.lower()
    assert detail.strip() != "Dossier incomplet pour un dépôt :"


def test_mode_brouillon_assemble_malgre_les_manques(client):
    """Sans `depot`, on assemble quand même et on signale les manques.

    C'est la doctrine retenue avec Florent le 01/09/2026 : produire un
    brouillon avec des trous signalés plutôt que refuser de produire.
    """
    # Arrange
    projet_id = _creer_projet(client)

    # Act
    r = client.post(f"/api/projets/{projet_id}/dossier")

    # Assert
    assert r.status_code == 200
    corps = r.json()
    assert corps["depot"] is False
    assert corps["fichier"].endswith(".pptx")
    assert corps["avertissements"], "les pièces manquantes doivent être signalées"


def test_depot_bloquant_et_brouillon_signalent_les_memes_pieces(client):
    """Le refus de dépôt et les avertissements du brouillon doivent parler
    des mêmes pièces : deux messages divergents dérouteraient le BE."""
    # Arrange
    projet_id = _creer_projet(client)

    # Act
    refus = client.post(f"/api/projets/{projet_id}/dossier?depot=1").json()["detail"]
    avertissements = client.post(f"/api/projets/{projet_id}/dossier").json()["avertissements"]

    # Assert : chaque pièce citée dans les avertissements du BE se retrouve
    # dans le motif de refus (on compare sur les codes DP, stables).
    # Le filtre est strict : les avertissements techniques des planches
    # (« dp1_situation : ... ») ne sont pas des libellés de pièce.
    codes_avertis = {
        m.split()[0] for m in avertissements if re.fullmatch(r"DP\d+", m.split()[0])
    }
    assert codes_avertis, "aucun code de pièce reconnu dans les avertissements"
    for code in codes_avertis:
        assert code in refus, f"{code} signalé au brouillon mais absent du motif de refus"


# ------------------------------------------------------- ordre de la chaîne

def test_telechargement_avant_assemblage_renvoie_404(client):
    # Arrange
    projet_id = _creer_projet(client)

    # Act / Assert
    r = client.get(f"/api/projets/{projet_id}/dossier.pptx")
    assert r.status_code == 404
    assert "assemblé" in r.json()["detail"].lower()


def test_pdf_avant_assemblage_renvoie_404(client):
    # Arrange
    projet_id = _creer_projet(client)

    # Act / Assert
    r = client.post(f"/api/projets/{projet_id}/dossier/pdf")
    assert r.status_code == 404
    assert "assemblez" in r.json()["detail"].lower()


def test_assemblage_puis_telechargement_rend_un_pptx(client):
    """Le chemin nominal complet, celui que l'utilisateur emprunte."""
    # Arrange
    projet_id = _creer_projet(client)
    _deposer(client, projet_id, "dp2", _pdf_bytes(), "masse.pdf")
    _deposer(client, projet_id, "dp6", _jpeg_bytes(), "insertion.jpg")

    # Act
    assemblage = client.post(f"/api/projets/{projet_id}/dossier")
    telechargement = client.get(f"/api/projets/{projet_id}/dossier.pptx")

    # Assert
    assert assemblage.status_code == 200
    assert telechargement.status_code == 200
    # signature ZIP : un .pptx est un conteneur OOXML
    assert telechargement.content[:2] == b"PK"
    assert projet_id in assemblage.json()["telechargement"]


def test_cerfa_avant_generation_renvoie_404(client):
    # Arrange
    projet_id = _creer_projet(client)

    # Act / Assert
    r = client.get(f"/api/projets/{projet_id}/cerfa.pdf")
    assert r.status_code == 404


# ------------------------------------------------------------- export PDF

def test_export_pdf_indisponible_repond_501_et_non_500(client, monkeypatch):
    """Sans PowerPoint ni LibreOffice, l'outil doit le DIRE, pas planter.

    L'export PDF est le seul module dépendant de la plateforme : c'est aussi
    le premier à casser lors d'un passage en conteneur Linux. Un 501 explicite
    est ce qui permettra de diagnostiquer l'hébergement.
    """
    # Arrange
    projet_id = _creer_projet(client)
    client.post(f"/api/projets/{projet_id}/dossier")

    def _indisponible(_chemin):
        raise RuntimeError("Aucun convertisseur PDF disponible sur ce poste.")

    monkeypatch.setattr(export_pdf, "exporter_pdf", _indisponible)
    # la route importe la fonction par valeur : on patche aussi le module appelant
    from app.api import routes_dossier
    monkeypatch.setattr(routes_dossier, "exporter_pdf", _indisponible)

    # Act
    r = client.post(f"/api/projets/{projet_id}/dossier/pdf")

    # Assert
    assert r.status_code == 501
    assert "convertisseur" in r.json()["detail"].lower()


# ------------------------------------------- écriture atomique du livrable

def test_le_pptx_final_est_ecrit_atomiquement(client, monkeypatch):
    """Le dossier ne doit JAMAIS exister à moitié écrit à son chemin final.

    Trou relevé par la mutation E6 : le test existant se contentait de
    constater l'absence de .tmp résiduel, ce qui reste vrai si l'on supprime
    purement et simplement le couple tmp + os.replace. On éprouve donc la
    propriété elle-même : si l'écriture échoue en cours de route, aucun
    fichier final ne doit apparaître.
    """
    # Arrange
    # pptx.Presentation est une FABRIQUE ; la classe porteuse de save() est
    # pptx.presentation.Presentation.
    from pptx.presentation import Presentation

    projet_id = _creer_projet(client)
    client.post(f"/api/projets/{projet_id}/dossier")
    final = config.assets_dir(projet_id) / f"Dossier_DP_{projet_id}.pptx"
    assert final.exists()
    final.unlink()

    sauver_reel = Presentation.save

    def _sauver_qui_echoue(self, chemin):
        # écrit un fichier tronqué puis échoue, comme un disque plein
        sauver_reel(self, chemin)
        raise OSError("disque plein simulé")

    monkeypatch.setattr(Presentation, "save", _sauver_qui_echoue)

    # Act
    with pytest.raises(OSError):
        client.post(f"/api/projets/{projet_id}/dossier")

    # Assert : l'échec ne laisse pas un dossier corrompu à sa place définitive
    assert not final.exists(), (
        "un PPTX incomplet est apparu au chemin final : l'écriture n'est plus atomique"
    )
