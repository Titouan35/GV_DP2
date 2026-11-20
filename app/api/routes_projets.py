"""CRUD projets : persistance JSON dans PROJETS/ (1 fichier par projet).

Le frontend envoie l'état complet du projet à chaque sauvegarde ; la
réponse renvoie l'évaluation réglementaire (régime + complétude) pour
alimenter le panneau de droite.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import threading
import unicodedata
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta

from fastapi import APIRouter, Body, HTTPException, Request

from .. import config, regles, verrou
from ..models import Projet

router = APIRouter(prefix="/api/projets", tags=["projets"])

# les routes sync tournent dans un threadpool : deux sauvegardes concurrentes
# du même projet (autosave + clic « suivant », deux onglets) s'entrelaçaient.
# Un verrou par projet sérialise l'écriture ; l'écriture elle-même est atomique
# (fichier temporaire + os.replace) pour ne jamais laisser un JSON tronqué en
# cas de crash ou de verrou OneDrive en plein write.
#
# Réentrant depuis le 20/11/2026 : les routes tiennent désormais le verrou
# pendant tout leur travail (projet_a_modifier). Un `_sauver` appelé un jour
# à l'intérieur d'un tel bloc figerait sinon le projet pour de bon, sans
# erreur ni trace.
_VERROUS_PROJET: dict[str, threading.RLock] = {}
_VERROUS_GARDE = threading.Lock()


def verrou_projet(projet_id: str) -> threading.RLock:
    with _VERROUS_GARDE:
        return _VERROUS_PROJET.setdefault(projet_id, threading.RLock())


def _utilisateur(request: Request | None) -> str | None:
    """Identité légère de l'auteur d'une modification (multi-poste BE).

    En-tête X-Utilisateur si un proxy/déploiement le fournit, sinon le compte
    Windows/Unix du poste (pertinent en local mono-utilisateur). Purement
    informatif : sert au message du verrou optimiste, pas à des droits.
    """
    if request is not None:
        entete = request.headers.get("x-utilisateur")
        if entete:
            return entete[:80]
    return os.environ.get("USERNAME") or os.environ.get("USER")


def _slug(nom: str) -> str:
    s = unicodedata.normalize("NFKD", nom).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()
    return s[:40] or "projet"


def _chemin(projet_id: str):
    if not config.projet_id_valide(projet_id):
        raise HTTPException(status_code=400, detail="Identifiant de projet invalide.")
    return config.espace() / f"{projet_id}.json"


def exiger_projet_id(projet_id: str) -> None:
    """Dépendance de routeur : refuse un identifiant mal formé AVANT la route.

    Pourquoi (20/11/2026). Les routes de téléchargement (`cerfa.pdf`,
    `dossier.pptx`, `planches/...`) construisaient le dossier du projet avec
    `config.assets_dir(projet_id)` AVANT tout chargement, donc sans le contrôle
    de `_chemin`. Sous Windows, `\\` est un séparateur que l'URL laisse passer
    (`..%5C..%5Cx`) : le dossier visé sortait de l'espace, et `assets_dir` le
    créait au passage. Posée sur les routeurs dont TOUTES les routes portent
    un identifiant, cette dépendance couvre aussi les routes à venir.
    """
    _chemin(projet_id)


def _ecrire(projet: Projet) -> None:
    """Écriture atomique SANS verrou : réservée aux appelants qui tiennent
    déjà `verrou_projet`."""
    config.espace().mkdir(parents=True, exist_ok=True)
    chemin = _chemin(projet.id)
    tmp = chemin.with_suffix(".json.tmp")
    tmp.write_text(projet.model_dump_json(indent=2), encoding="utf-8")
    os.replace(tmp, chemin)  # atomique : jamais de JSON à moitié écrit


def _sauver(projet: Projet) -> None:
    with verrou_projet(projet.id):
        _ecrire(projet)


@contextmanager
def projet_a_modifier(projet_id: str):
    """Charge le projet, le laisse modifier, puis l'écrit : tout sous son verrou.

    Pourquoi (20/11/2026). Seule la sauvegarde (PUT) tenait le verrou sur
    toute la séquence lire-modifier-écrire. Les autres routes qui écrivent le
    projet (dépôt d'une pièce, validation, suppression, type d'ombrière,
    notice, Cerfa, notices ERP) le chargeaient, travaillaient (plusieurs
    secondes pour lire une FVE), puis réécrivaient leur copie ENTIÈRE. Une
    sauvegarde arrivée entre-temps était écrasée sans un mot : exactement le
    « dernier qui écrit gagne » que le verrou optimiste du PUT devait empêcher.

    Une sauvegarde concurrente attend désormais la fin du bloc. Elle passe
    ensuite si elle partait du bon état, ou reçoit le 409 habituel sinon :
    jamais de perte silencieuse. Si le bloc lève une exception, rien n'est
    écrit.
    """
    with verrou_projet(projet_id):
        projet = _charger(projet_id)
        avant = projet.date_modification
        yield projet
        projet.date_modification = _date_suivante(avant)
        _ecrire(projet)


def _date_suivante(precedente: str | None) -> str:
    """Horodatage d'une écriture, STRICTEMENT postérieur au précédent.

    `date_modification` sert de jeton au verrou optimiste du PUT, à la seconde
    près. Deux écritures dans la même seconde portaient donc le même jeton :
    une sauvegarde partie d'AVANT un dépôt passait le contrôle et effaçait la
    pièce (relevé le 20/11/2026). On avance d'une seconde au besoin ; toutes
    les écritures d'un projet passant sous son verrou, le jeton ne peut que
    croître.
    """
    maintenant = datetime.now().replace(microsecond=0)
    try:
        avant = datetime.fromisoformat(precedente) if precedente else None
        if avant is not None and maintenant <= avant:
            maintenant = avant + timedelta(seconds=1)
    except (ValueError, TypeError):
        pass          # date illisible ou avec fuseau : l'heure courante suffit
    return maintenant.isoformat(timespec="seconds")


def _charger(projet_id: str) -> Projet:
    chemin = _chemin(projet_id)
    if not chemin.exists():
        raise HTTPException(status_code=404, detail="Projet introuvable.")
    try:
        projet = Projet.model_validate(json.loads(chemin.read_text(encoding="utf-8")))
        # dossiers antérieurs au 16/09/2026 : le maître d'ouvrage y a été
        # enregistré champ par champ (ou laissé vide). On y remet les valeurs
        # Greenvolt manquantes plutôt que de laisser le BE les retaper.
        projet.mo.completer_defauts()
        return projet
    except (ValueError, OSError) as exc:
        # JSON tronqué, schéma incompatible ou fichier verrouillé (OneDrive) :
        # un message actionnable plutôt qu'un 500 brut qui bloque tout le projet
        raise HTTPException(
            status_code=422,
            detail=f"Projet illisible ({exc.__class__.__name__}) : fichier corrompu, "
                   "format obsolète ou verrouillé par la synchronisation. "
                   f"Voir PROJETS/{projet_id}.json.",
        ) from exc


def resume_projet(data) -> dict | None:
    """Ligne de la liste des projets, ou None si le contenu n'est pas un projet.

    Partagé avec la synchro du dossier lié (routes_synchro), qui calcule la
    liste à partir des fichiers du POSTE sans les écrire sur le serveur.
    """
    if not isinstance(data, dict):
        return None
    try:
        projet = Projet.model_validate(data)
    except ValueError:
        return None
    projet.mo.completer_defauts()  # même complétude que dans le projet ouvert
    c = regles.completude(projet)
    return {
        "id": data.get("id"),
        "nom": data.get("nom"),
        "statut": data.get("statut"),
        "regime": data.get("regime"),
        "commune": (data.get("localisation") or {}).get("commune"),
        "date_modification": data.get("date_modification"),
        "completude": {"pretes": c["pretes"], "total": c["total"]},
    }


@router.get("")
def lister_projets():
    config.espace().mkdir(parents=True, exist_ok=True)
    projets = []
    for f in sorted(config.espace().glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue
        resume = resume_projet(data)
        if resume is not None:
            projets.append(resume)
    projets.sort(key=lambda p: p.get("date_modification") or "", reverse=True)
    return {"projets": projets}


@router.post("")
def creer_projet(projet: Projet, request: Request = None):
    projet.id = f"{_slug(projet.nom)}-{uuid.uuid4().hex[:6]}"
    projet.date_creation = datetime.now().isoformat(timespec="seconds")
    projet.date_modification = projet.date_creation
    projet.modifie_par = _utilisateur(request)
    evaluation = regles.evaluer(projet)
    projet.regime = evaluation["regime"]["regime"]
    _sauver(projet)
    return {"projet": projet, "evaluation": evaluation}


@router.get("/{projet_id}")
def lire_projet(projet_id: str, request: Request = None):
    projet = _charger(projet_id)
    # présence d'un collègue sur ce projet (mode partagé) : informatif, le
    # client décide d'avertir ou non
    presence = verrou.lire(projet_id)
    moi = _utilisateur(request)
    return {"projet": projet, "evaluation": regles.evaluer(projet),
            "verrou": ({"detenteur": presence.get("utilisateur"),
                        "machine": presence.get("machine"),
                        "depuis": presence.get("depuis"),
                        "a_moi": presence.get("utilisateur") == moi}
                       if presence else None)}


@router.put("/{projet_id}")
def sauvegarder_projet(projet_id: str, projet: Projet, request: Request = None):
    # verrou optimiste multi-poste : si le fichier a changé depuis le
    # chargement côté client (autre onglet, autre poste OneDrive), on refuse
    # au lieu d'écraser silencieusement le travail de l'autre (last-write-wins).
    # TOUTE la séquence lire-comparer-écrire est sous le verrou du projet :
    # sinon deux PUT simultanés passaient tous les deux le contrôle (TOCTOU).
    with verrou_projet(projet_id):
        existant = _charger(projet_id)
        if (projet.date_modification and existant.date_modification
                and projet.date_modification != existant.date_modification):
            raise HTTPException(
                status_code=409,
                detail=f"Projet modifié entre-temps ({existant.date_modification}"
                       + (f", par {existant.modifie_par}" if existant.modifie_par else "")
                       + "). Rechargez la page pour repartir de la dernière version.",
            )
        projet.id = projet_id
        projet.date_modification = _date_suivante(existant.date_modification)
        projet.modifie_par = _utilisateur(request)
        evaluation = regles.evaluer(projet)
        projet.regime = evaluation["regime"]["regime"]
        _ecrire(projet)
    return {"projet": projet, "evaluation": evaluation}


# fichiers d'assets REGÉNÉRABLES : caches d'optimisation, planches, kits
# Gemini, pages PDF rendues, aperçus. Supprimables sans perte de donnée.
_MOTIFS_CACHE = ("embed_*.jpg", "kit_*.png", "apercu_*.png", "_gradient*.png",
                 "photo_reperee.png", "scaffold.png", "aerienne_*.png",
                 "dp1_*.png", "dp3_provisoire.png", "dp*_p*.jpg",
                 # copies réparées des images qu'PIL refuse d'ouvrir
                 # (app/images.py) : refaites au prochain assemblage
                 "repare_*.jpg",
                 # archive des pages découpées : refaite à la demande
                 "pages_*.zip")


@router.post("/{projet_id}/nettoyer")
def nettoyer_projet(projet_id: str):
    """Purge les fichiers régénérables + les insertions non référencées.

    Le dossier .assets grossit à chaque essai (caches, images non retenues) et
    tout part dans la synchro OneDrive : ce nettoyage ne touche ni les uploads
    BE, ni les photos du site, ni les images listées dans la galerie.
    """
    projet = _charger(projet_id)
    assets = config.assets_dir(projet_id)
    libere = 0
    fichiers = 0

    for motif in _MOTIFS_CACHE:
        for f in assets.glob(motif):
            libere += f.stat().st_size
            f.unlink(missing_ok=True)
            fichiers += 1

    # héritage du module Insertion retiré le 01/09/2026 : le dossier
    # insertion/ des projets antérieurs n'a plus aucun référent dans le
    # modèle. On le purge entièrement, c'est le seul moyen de récupérer la
    # place qu'il occupe dans OneDrive (48 Mo orphelins constatés).
    dossier_ins = assets / "insertion"
    if dossier_ins.exists():
        for f in dossier_ins.iterdir():
            if not f.is_file():
                continue
            libere += f.stat().st_size
            f.unlink(missing_ok=True)
            fichiers += 1

    return {"fichiers_supprimes": fichiers, "octets_liberes": libere,
            "mo_liberes": round(libere / 1_048_576, 1)}


@router.put("/{projet_id}/ombriere/type")
def choisir_type_ombriere(projet_id: str, corps: dict = Body(...)):
    """Type d'ombrière du projet : Mono Bas, Mono Haut ou Double.

    Cette route vivait dans le module Insertion (PUT /insertion/type). Elle en
    a été SORTIE lors du retrait de ce module (01/09/2026), parce qu'elle était
    le seul endroit du code écrivant `ombriere.famille`. Sans elle, le
    catalogue retombe silencieusement sur START PLAINE Bas et le Cerfa se met à
    affirmer des cotes qui n'ont jamais été saisies. Elle n'avait donc rien à
    faire dans l'insertion : le type d'ombrière est une caractéristique du
    projet, pas un paramètre de visuel.

    Les hauteurs ne sont initialisées depuis le catalogue que si elles sont
    VIDES : une cote relevée sur la coupe du BE n'est jamais écrasée.
    """
    from ..catalogue import CATALOGUE

    with projet_a_modifier(projet_id) as projet:
        famille = corps.get("famille")
        if famille not in CATALOGUE:
            raise HTTPException(status_code=400, detail="Type d'ombrière inconnu.")
        projet.ombriere.famille = famille
        entree = CATALOGUE[famille]
        if projet.ombriere.garde_au_sol_m is None:
            projet.ombriere.garde_au_sol_m = entree["h_bas_m"]
        if projet.ombriere.hauteur_hors_tout_m is None:
            projet.ombriere.hauteur_hors_tout_m = entree["h_haut_m"]
        projet.date_modification = datetime.now().isoformat(timespec="seconds")
    return {"projet": projet, "evaluation": regles.evaluer(projet)}


@router.post("/{projet_id}/verrou")
def prendre_verrou(projet_id: str, request: Request = None, forcer: int = 0):
    """Signale que ce poste ouvre le projet (mode partagé entre collègues).

    Rafraîchi périodiquement par le client. `forcer=1` reprend la main sur un
    collègue, à sa demande explicite.
    """
    if not _chemin(projet_id).exists():
        # sans ce contrôle, un identifiant fantaisiste créait un .lock orphelin
        raise HTTPException(status_code=404, detail="Projet introuvable.")
    qui = _utilisateur(request)
    etat = (verrou.forcer(projet_id, qui) if forcer
            else verrou.poser(projet_id, qui))
    return etat


@router.delete("/{projet_id}/verrou")
def liberer_verrou(projet_id: str, request: Request = None):
    """Libère la présence en quittant le projet."""
    _chemin(projet_id)
    verrou.liberer(projet_id, _utilisateur(request))
    return {"ok": True}


@router.delete("/{projet_id}")
def supprimer_projet(projet_id: str):
    chemin = _chemin(projet_id)
    if not chemin.exists():
        raise HTTPException(status_code=404, detail="Projet introuvable.")
    chemin.unlink()
    # le dossier .assets (uploads, exports, caches) part avec le projet :
    # avant, il restait orphelin sur le disque et dans la synchro OneDrive
    shutil.rmtree(config.espace() / f"{projet_id}.assets", ignore_errors=True)
    (config.espace() / f"{projet_id}.lock").unlink(missing_ok=True)
    return {"ok": True}
