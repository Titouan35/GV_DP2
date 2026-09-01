"""Filet de sécurité du socle : pièces déposées (routes_documents) + cycle de
vie projet (routes_projets), vus depuis l'API.

Pourquoi ce fichier : le module Insertion va être retiré. La DP6 (insertion
paysagère) deviendra alors une simple image DÉPOSÉE par le BE, exactement
comme dp2, dp3, dp7 et dp8. Le mécanisme existe déjà
(CODES_UPLOAD, routes_documents.py) mais il n'était éprouvé que sur dp7/dp2 :
on caractérise ici son comportement complet SUR dp6 avant de s'appuyer
dessus, pour que toute régression du retrait se voie immédiatement.

Conventions reprises de tests/test_api_projets.py et test_routes_fiabilite.py :
TestClient FastAPI + redirection de config.PROJETS_DIR vers tmp_path (jamais
d'écriture dans le vrai dossier PROJETS/). Aucun appel réseau.
"""
from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import config
from app.main import app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROJETS_DIR", tmp_path / "PROJETS")
    return TestClient(app)


# ------------------------------------------------------------------ fabriques

def _png_bytes(w=60, h=40, couleur=(120, 130, 140)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), couleur).save(buf, "PNG")
    return buf.getvalue()


def _jpeg_bytes(w=60, h=40, orientation: int | None = None) -> bytes:
    """JPEG en mémoire, éventuellement porteur d'un tag EXIF Orientation."""
    image = Image.new("RGB", (w, h), (200, 180, 60))
    exif = image.getexif()
    if orientation is not None:
        exif[274] = orientation
    buf = io.BytesIO()
    image.save(buf, "JPEG", exif=exif)
    return buf.getvalue()


def _pdf_bytes(w=200, h=150) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (255, 255, 255)).save(buf, "PDF")
    return buf.getvalue()


def _creer_projet(client, nom="Socle documents") -> str:
    r = client.post("/api/projets", json={"nom": nom})
    assert r.status_code == 200
    return r.json()["projet"]["id"]


def _uploads_dir(projet_id: str) -> Path:
    """Chemin des uploads SANS passer par config.assets_dir(), qui crée le
    dossier au passage : indispensable pour tester une non-existence."""
    return config.PROJETS_DIR / f"{projet_id}.assets" / "uploads"


def _deposer(client, projet_id, code="dp6", nom="insertion_be.png",
             contenu=None, mime="image/png"):
    return client.post(f"/api/projets/{projet_id}/documents/{code}",
                       files={"fichier": (nom, contenu if contenu is not None
                                          else _png_bytes(), mime)})


def _piece(evaluation: dict, code: str) -> dict:
    return next(p for p in evaluation["completude"]["pieces"] if p["code"] == code)


# ------------------------------------------------------------------ dépôt DP6

def test_depot_dp6_renvoie_projet_evaluation_et_champs_plan(client):
    """Contrat de réponse de l'upload : le front lit ces trois clés.

    `plan_champs_proposes` doit rester présent et VIDE pour dp6 : seule la
    dp2 (plan de masse) déclenche la lecture du cartouche."""
    # Arrange
    pid = _creer_projet(client)

    # Act
    r = _deposer(client, pid)

    # Assert
    assert r.status_code == 200
    corps = r.json()
    assert set(corps) >= {"projet", "evaluation", "plan_champs_proposes"}
    assert corps["plan_champs_proposes"] == []
    doc = corps["projet"]["documents"]["dp6"]
    assert doc["nom_fichier"] == "insertion_be.png"
    assert doc["taille"] == len(_png_bytes())
    assert doc["date"]


def test_depot_dp6_ecrit_le_fichier_a_l_emplacement_attendu(client):
    """Le chemin enregistré est relatif à PROJETS/ et en slashs : l'assemblage
    PPTX et les aperçus le recollent tel quel (Windows + Mac)."""
    # Arrange
    pid = _creer_projet(client)
    contenu = _png_bytes(80, 50)

    # Act
    r = _deposer(client, pid, contenu=contenu)

    # Assert
    assert r.json()["projet"]["documents"]["dp6"]["fichier"] == \
        f"{pid}.assets/uploads/dp6.png"
    sur_disque = _uploads_dir(pid) / "dp6.png"
    assert sur_disque.exists()
    assert sur_disque.read_bytes() == contenu


def test_depot_dp6_est_persiste_dans_le_json_du_projet(client):
    """La réponse ne suffit pas : le dépôt doit survivre au rechargement
    (l'entrée est écrite dans PROJETS/<id>.json, pas seulement renvoyée)."""
    # Arrange
    pid = _creer_projet(client)
    avant = client.get(f"/api/projets/{pid}").json()["projet"]["date_modification"]

    # Act
    _deposer(client, pid)

    # Assert
    sur_disque = json.loads((config.PROJETS_DIR / f"{pid}.json").read_text(encoding="utf-8"))
    assert sur_disque["documents"]["dp6"]["fichier"] == f"{pid}.assets/uploads/dp6.png"
    assert sur_disque["date_modification"] >= avant
    relu = client.get(f"/api/projets/{pid}").json()["projet"]
    assert "dp6" in relu["documents"]


def test_depot_dp6_marque_la_piece_prete_dans_la_completude(client):
    """Après le retrait de l'insertion, c'est CE mécanisme qui pilotera la
    complétude de la DP6 : pièce attendue du BE tant qu'elle n'est pas déposée,
    « prête » dès qu'un fichier est présent dans projet.documents."""
    # Arrange
    pid = _creer_projet(client)
    evaluation_initiale = client.get(f"/api/projets/{pid}").json()["evaluation"]
    assert _piece(evaluation_initiale, "dp6")["statut"] == "en_attente"
    pretes_avant = evaluation_initiale["completude"]["pretes"]

    # Act
    evaluation = _deposer(client, pid).json()["evaluation"]

    # Assert
    assert _piece(evaluation, "dp6")["statut"] == "prete"
    assert _piece(evaluation, "dp6")["detail"] == "fournie"
    assert evaluation["completude"]["pretes"] == pretes_avant + 1


def test_le_projet_ne_porte_plus_de_bloc_insertion(client):
    """Le module Insertion a été retiré le 01/09/2026.

    Ce test remplace test_depot_dp6_ne_modifie_pas_le_bloc_insertion, écrit la
    veille pour prouver que l'upload de la DP6 ne dépendait pas du bloc
    insertion. Il vérifie maintenant que ce bloc a bien disparu du modèle, et
    surtout qu'il ne réapparaît pas par un chemin détourné : une clé fantôme
    resurgirait silencieusement dans tous les JSON de projet.
    """
    # Arrange
    pid = _creer_projet(client)

    # Act : la DP6 est désormais une pièce déposée comme les autres
    projet = _deposer(client, pid).json()["projet"]

    # Assert
    assert "insertion" not in projet
    assert projet["documents"]["dp6"]["nom_fichier"]


def test_photo_deposee_est_redressee_selon_son_tag_exif(client):
    """Une photo prise en portrait porte un tag EXIF Orientation : le navigateur
    la redresse, PIL non. La normalisation à l'upload garde tout le pipeline
    (planches, assemblage) cohérent avec ce que voit l'utilisateur."""
    # Arrange
    pid = _creer_projet(client)

    # Act : image 60x40 déclarée pivotée d'un quart de tour
    r = _deposer(client, pid, nom="photo.jpg",
                 contenu=_jpeg_bytes(60, 40, orientation=6), mime="image/jpeg")

    # Assert : le fichier stocké est réellement redressé (dimensions inversées)
    assert r.status_code == 200
    with Image.open(_uploads_dir(pid) / "dp6.jpg") as im:
        assert im.size == (40, 60)


# ------------------------------------------------------------------ relecture

def test_relecture_dp6_telechargement_et_apercu_image(client):
    """Les deux routes de lecture servies au front : téléchargement de la pièce
    d'origine (avec son nom réel) et aperçu image."""
    # Arrange
    pid = _creer_projet(client)
    contenu = _png_bytes(70, 45)
    _deposer(client, pid, nom="insertion_paysagere.png", contenu=contenu)

    # Act
    telechargement = client.get(f"/api/projets/{pid}/documents/dp6")
    apercu = client.get(f"/api/projets/{pid}/documents/dp6/image")

    # Assert
    assert telechargement.status_code == 200
    assert telechargement.content == contenu
    assert "insertion_paysagere.png" in telechargement.headers["content-disposition"]
    assert apercu.status_code == 200
    assert apercu.headers["content-type"] == "image/png"
    assert apercu.content == contenu  # image déjà bitmap : servie telle quelle


def test_apercu_dp6_pdf_est_rendu_en_png(client):
    """Une pièce PDF est convertie (1re page) pour l'affichage : c'est la seule
    façon pour le front de montrer un PDF déposé."""
    # Arrange
    pid = _creer_projet(client)
    r = _deposer(client, pid, nom="insertion.pdf", contenu=_pdf_bytes(),
                 mime="application/pdf")
    assert r.status_code == 200

    # Act
    apercu = client.get(f"/api/projets/{pid}/documents/dp6/image")

    # Assert
    assert apercu.status_code == 200
    assert apercu.headers["content-type"] == "image/png"
    assert apercu.content.startswith(b"\x89PNG")
    # le rendu est mis en cache dans les assets (motif purgé par /nettoyer)
    assert (config.PROJETS_DIR / f"{pid}.assets" / "apercu_dp6.png").exists()


def test_lecture_dp6_absente_renvoie_404(client):
    """Pièce jamais déposée : 404 explicite, pas une 500."""
    # Arrange
    pid = _creer_projet(client)

    # Act / Assert
    r = client.get(f"/api/projets/{pid}/documents/dp6")
    assert r.status_code == 404
    assert "non fournie" in r.json()["detail"]
    assert client.get(f"/api/projets/{pid}/documents/dp6/image").status_code == 404


# ------------------------------------------------- remplacement / suppression

def test_remplacement_dp6_purge_l_ancien_fichier(client):
    """Une seule DP6 par projet : redéposer dans un AUTRE format ne doit pas
    laisser un dp6.png orphelin à côté du dp6.jpg (fichier fantôme repris par
    erreur dans les planches, et poids inutile dans la synchro OneDrive)."""
    # Arrange
    pid = _creer_projet(client)
    _deposer(client, pid, nom="v1.png", contenu=_png_bytes())

    # Act
    r = _deposer(client, pid, nom="v2.jpg", contenu=_jpeg_bytes(120, 90),
                 mime="image/jpeg")

    # Assert
    assert r.status_code == 200
    doc = r.json()["projet"]["documents"]["dp6"]
    assert doc["nom_fichier"] == "v2.jpg"
    assert doc["fichier"] == f"{pid}.assets/uploads/dp6.jpg"
    assert sorted(f.name for f in _uploads_dir(pid).glob("dp6.*")) == ["dp6.jpg"]


def test_suppression_dp6_efface_entree_et_fichier(client):
    """Le BE se trompe de pièce : la suppression doit rendre le projet à son
    état initial (entrée JSON, fichier disque et complétude)."""
    # Arrange
    pid = _creer_projet(client)
    _deposer(client, pid)
    chemin = _uploads_dir(pid) / "dp6.png"
    assert chemin.exists()

    # Act
    r = client.delete(f"/api/projets/{pid}/documents/dp6")

    # Assert
    assert r.status_code == 200
    assert "dp6" not in r.json()["projet"]["documents"]
    assert _piece(r.json()["evaluation"], "dp6")["statut"] == "en_attente"
    assert not chemin.exists()
    assert client.get(f"/api/projets/{pid}/documents/dp6").status_code == 404


def test_suppression_dp6_tolere_un_fichier_deja_disparu(client):
    """Fichier effacé à la main (ou perdu par la synchro) : la suppression doit
    quand même nettoyer l'entrée JSON au lieu de planter."""
    # Arrange
    pid = _creer_projet(client)
    _deposer(client, pid)
    (_uploads_dir(pid) / "dp6.png").unlink()

    # Act
    r = client.delete(f"/api/projets/{pid}/documents/dp6")

    # Assert
    assert r.status_code == 200
    assert "dp6" not in r.json()["projet"]["documents"]


def test_suppression_dp6_jamais_deposee_reste_sans_effet(client):
    """Suppression idempotente : le front l'appelle sans savoir si la pièce
    existe (double clic, reprise de session)."""
    # Arrange
    pid = _creer_projet(client)

    # Act
    r = client.delete(f"/api/projets/{pid}/documents/dp6")

    # Assert
    assert r.status_code == 200
    assert r.json()["projet"]["documents"] == {}


# --------------------------------------------------------------------- refus

def test_depot_dp6_format_non_accepte_refuse(client):
    """Seuls PDF/PNG/JPG sont acceptés. Le .webp est piégeux : sa signature est
    connue du module, mais l'extension reste hors liste (PowerPoint et pypdfium
    ne le reprennent pas de façon fiable)."""
    # Arrange
    pid = _creer_projet(client)

    # Act
    r = _deposer(client, pid, nom="vue.webp",
                 contenu=b"RIFF\x00\x00\x00\x00WEBPblabla", mime="image/webp")

    # Assert
    assert r.status_code == 400
    assert "Format non accepté" in r.json()["detail"]
    assert not _uploads_dir(pid).exists()


def test_depot_dp6_faux_fichier_refuse_sur_les_magic_bytes(client):
    """Un JPEG renommé .png (ou pire, un .pdf qui n'en est pas un) plantait plus
    loin dans le rendu avec une erreur obscure : il doit être refusé à l'entrée,
    et RIEN ne doit être écrit ni enregistré."""
    # Arrange
    pid = _creer_projet(client)

    # Act
    r = _deposer(client, pid, nom="insertion.png",
                 contenu=b"GIF89a" + b"\x00" * 32, mime="image/png")

    # Assert
    assert r.status_code == 400
    assert "correspond pas" in r.json()["detail"]
    assert not _uploads_dir(pid).exists()
    assert client.get(f"/api/projets/{pid}").json()["projet"]["documents"] == {}


def test_depot_dp6_trop_volumineux_refuse(client, monkeypatch):
    """Garde-fou de taille (40 Mo en production) : la lecture est coupée dès le
    dépassement, sans bufferiser tout le fichier."""
    # Arrange
    from app.api import routes_documents
    monkeypatch.setattr(routes_documents, "TAILLE_MAX", 200)
    pid = _creer_projet(client)

    # Act
    r = _deposer(client, pid, contenu=_png_bytes(400, 300))

    # Assert
    assert r.status_code == 400
    assert "volumineux" in r.json()["detail"]
    assert not _uploads_dir(pid).exists()


def test_code_de_piece_non_uploadable_refuse(client):
    """Seuls les codes de CODES_UPLOAD sont déposables : une pièce générée
    (plan de situation, Cerfa) ne doit pas pouvoir être écrasée ni supprimée
    par la route d'upload."""
    # Arrange
    pid = _creer_projet(client)

    # Act / Assert
    depot = _deposer(client, pid, code="dp1_situation")
    assert depot.status_code == 404
    assert "inconnue" in depot.json()["detail"]
    assert client.delete(f"/api/projets/{pid}/documents/cerfa").status_code == 404


def test_depot_sur_projet_inexistant_refuse(client):
    """Le projet est chargé APRÈS validation du fichier : un identifiant mort
    doit renvoyer 404, pas créer un dossier d'assets orphelin."""
    # Act
    r = _deposer(client, "projet-fantome-000000")

    # Assert
    assert r.status_code == 404
    assert not (config.PROJETS_DIR / "projet-fantome-000000.assets").exists()


# ------------------------------------------------- incohérences JSON / disque

def test_dp6_declaree_mais_absente_du_disque_renvoie_404(client):
    """Cas réel : le JSON référence la pièce, le fichier a disparu (nettoyage
    manuel, conflit OneDrive). Les deux routes de lecture doivent répondre 404
    avec un message actionnable, jamais une 500."""
    # Arrange
    pid = _creer_projet(client)
    _deposer(client, pid)
    (_uploads_dir(pid) / "dp6.png").unlink()

    # Act
    telechargement = client.get(f"/api/projets/{pid}/documents/dp6")
    apercu = client.get(f"/api/projets/{pid}/documents/dp6/image")

    # Assert
    assert telechargement.status_code == 404
    assert "manquant sur le disque" in telechargement.json()["detail"]
    assert apercu.status_code == 404
    # l'entrée reste dans le projet : c'est au BE de redéposer la pièce
    assert "dp6" in client.get(f"/api/projets/{pid}").json()["projet"]["documents"]


def test_chemin_de_document_hors_de_projets_refuse(client, tmp_path):
    """Confinement : un chemin relatif remontant hors de PROJETS/ (JSON bricolé
    à la main, projet importé) ne doit servir aucun fichier du poste."""
    # Arrange : un fichier bien réel, mais à côté du dossier des projets
    intrus = tmp_path / "secret.png"
    intrus.write_bytes(_png_bytes())
    pid = _creer_projet(client)
    projet = client.get(f"/api/projets/{pid}").json()["projet"]
    projet["documents"] = {"dp6": {"nom_fichier": "secret.png",
                                   "fichier": "../secret.png",
                                   "date": "2026-08-31T10:00:00", "taille": 1}}
    assert client.put(f"/api/projets/{pid}", json=projet).status_code == 200

    # Act / Assert
    r = client.get(f"/api/projets/{pid}/documents/dp6")
    assert r.status_code == 404
    assert "introuvable" in r.json()["detail"]
    assert client.get(f"/api/projets/{pid}/documents/dp6/image").status_code == 404
    assert intrus.exists()  # non servi, et non modifié


# --------------------------------------------------------- cycle de vie projet

def test_cycle_de_vie_projet_preserve_les_documents(client):
    """Le front renvoie l'état COMPLET du projet à chaque sauvegarde : les
    pièces déposées doivent traverser l'aller-retour sans être perdues, sinon
    une simple autosave après un upload effacerait la DP6 du dossier."""
    # Arrange
    pid = _creer_projet(client, "Ombrières du golf")
    _deposer(client, pid)
    projet = client.get(f"/api/projets/{pid}").json()["projet"]
    assert projet["id"].startswith("ombrieres-du-golf-")

    # Act : sauvegarde d'une modification métier, documents inchangés
    projet["ombriere"]["puissance_kwc"] = 250
    r = client.put(f"/api/projets/{pid}", json=projet)

    # Assert
    assert r.status_code == 200
    assert r.json()["projet"]["documents"]["dp6"]["fichier"] == \
        f"{pid}.assets/uploads/dp6.png"
    relu = client.get(f"/api/projets/{pid}").json()["projet"]
    assert relu["ombriere"]["puissance_kwc"] == 250
    assert "dp6" in relu["documents"]
    assert (_uploads_dir(pid) / "dp6.png").exists()
    # la pièce déposée reste comptée « prête » après sauvegarde
    assert _piece(r.json()["evaluation"], "dp6")["statut"] == "prete"


def test_sauvegarde_perimee_refusee_par_le_verrou_optimiste(client):
    """Multi-poste (dossier projets partagé) : une sauvegarde partie d'un état
    plus ancien que celui du disque est refusée au lieu d'écraser le travail du
    collègue. Ici la péremption est simulée par une date_modification ancienne,
    pour ne pas dépendre d'une attente d'une seconde."""
    # Arrange
    pid = _creer_projet(client)
    projet = client.get(f"/api/projets/{pid}").json()["projet"]
    perime = {**projet, "nom": "Version périmée",
              "date_modification": "2020-01-01T00:00:00"}

    # Act
    r = client.put(f"/api/projets/{pid}", json=perime)

    # Assert
    assert r.status_code == 409
    assert "Rechargez" in r.json()["detail"]
    # rien n'a été écrit : le disque garde l'état d'origine
    assert client.get(f"/api/projets/{pid}").json()["projet"]["nom"] == projet["nom"]
    # repartir de l'état à jour passe
    assert client.put(f"/api/projets/{pid}", json={**projet, "nom": "Version à jour"}).status_code == 200
    assert client.get(f"/api/projets/{pid}").json()["projet"]["nom"] == "Version à jour"


def test_sauvegarde_ignore_l_identifiant_du_corps(client):
    """L'identifiant fait foi côté URL : un corps portant un autre id ne doit
    pas déplacer ni dupliquer le projet."""
    # Arrange
    pid = _creer_projet(client)
    projet = client.get(f"/api/projets/{pid}").json()["projet"]

    # Act
    r = client.put(f"/api/projets/{pid}", json={**projet, "id": "autre-projet-999999"})

    # Assert
    assert r.status_code == 200
    assert r.json()["projet"]["id"] == pid
    assert not (config.PROJETS_DIR / "autre-projet-999999.json").exists()


def test_suppression_projet_emporte_les_pieces_deposees(client):
    """Suppression d'un projet : les uploads du BE partent avec, sinon le
    dossier .assets reste orphelin dans la synchro OneDrive."""
    # Arrange
    pid = _creer_projet(client)
    _deposer(client, pid)
    uploads = _uploads_dir(pid)
    assert uploads.exists()

    # Act
    r = client.delete(f"/api/projets/{pid}")

    # Assert
    assert r.status_code == 200
    assert not uploads.exists()
    assert client.get(f"/api/projets/{pid}").status_code == 404


# ------------------------------------ type d'ombrière (route relocalisée)

def test_choisir_le_type_ecrit_la_famille_et_les_cotes_du_catalogue(client):
    """PUT /ombriere/type est le SEUL écrivain de `ombriere.famille`.

    Cette route vivait dans le module Insertion (PUT /insertion/type) et en a
    été sortie le 01/09/2026 avec son retrait. Sans elle, plus rien n'écrit la
    famille : le catalogue retomberait silencieusement sur START PLAINE Bas et
    le Cerfa affirmerait des cotes jamais saisies. D'où ce test.
    """
    # Arrange
    pid = _creer_projet(client)

    # Act
    r = client.put(f"/api/projets/{pid}/ombriere/type",
                   json={"famille": "START PLAINE Double"})

    # Assert
    assert r.status_code == 200
    omb = r.json()["projet"]["ombriere"]
    assert omb["famille"] == "START PLAINE Double"
    # les hauteurs vides sont initialisées depuis le catalogue
    assert omb["garde_au_sol_m"] is not None
    assert omb["hauteur_hors_tout_m"] is not None
    # la route rend aussi l'évaluation, dont dépend le panneau de complétude
    assert "evaluation" in r.json()


def test_choisir_le_type_n_ecrase_pas_une_cote_relevee_sur_la_coupe(client):
    """Une hauteur saisie par le BE prime toujours sur le catalogue.

    C'est la garantie qui permet de faire confiance au Cerfa : une cote lue sur
    la coupe du constructeur ne doit jamais être remplacée par une valeur
    théorique au détour d'un changement de type.
    """
    # Arrange : projet dont la hauteur hors tout a été saisie
    pid = _creer_projet(client)
    projet = client.get(f"/api/projets/{pid}").json()["projet"]
    projet["ombriere"]["hauteur_hors_tout_m"] = 4.5
    assert client.put(f"/api/projets/{pid}", json=projet).status_code == 200

    # Act
    r = client.put(f"/api/projets/{pid}/ombriere/type",
                   json={"famille": "START PLAINE Double"})

    # Assert
    assert r.json()["projet"]["ombriere"]["hauteur_hors_tout_m"] == 4.5


def test_type_inconnu_refuse(client):
    # Arrange
    pid = _creer_projet(client)

    # Act
    r = client.put(f"/api/projets/{pid}/ombriere/type", json={"famille": "PERGOLA XXL"})

    # Assert
    assert r.status_code == 400
    assert "inconnu" in r.json()["detail"].lower()


def test_aucun_type_par_defaut_a_la_creation(client):
    """Un projet neuf n'a PAS de type d'ombrière.

    Doctrine retenue avec Florent le 01/09/2026 : brouillon avec trous
    signalés. Un type présélectionné ferait entrer des cotes catalogue dans un
    formulaire officiel sans que personne ne les ait choisies.
    """
    # Arrange / Act
    pid = _creer_projet(client)

    # Assert
    assert not client.get(f"/api/projets/{pid}").json()["projet"]["ombriere"]["famille"]
