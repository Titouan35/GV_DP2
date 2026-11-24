"""Mémoire du serveur : une opération lourde à la fois, et la mémoire rendue après.

Pourquoi (24/11/2026). Le service Render (offre gratuite, 512 Mo) s'arrête
encore par moments (« exceeded its memory limit »), malgré le plafonnement du
rendu des plans du 20/11/2026 (images.rendre_page_pdf). Deux angles morts
restaient :

1. Rien n'empêchait deux opérations lourdes de tourner EN MÊME TEMPS. Le
   verrou d'assemblage est par projet : deux collègues qui assemblent deux
   projets différents font deux fois le pic mesuré (293 Mo), soit plus que les
   512 Mo. Pareil pour un assemblage qui croise des notices ERP ou une planche.
2. La mémoire libérée par Python et par les images n'est pas toujours rendue
   au système : le service démarre à ~150 Mo et peut rester bien au-dessus
   après un assemblage. L'opération suivante part alors de plus haut.

Ce module y répond par trois mesures :

- `operation_lourde` : une file d'attente commune à tout le serveur. Une
  seule opération lourde à la fois (réglable : GVDP_OPERATIONS_LOURDES) ; les
  autres attendent leur tour au lieu de faire tomber le service. Au-delà de
  GVDP_ATTENTE_MAX_S secondes d'attente (300 par défaut), la requête reçoit
  un 503 « serveur occupé, réessayez » plutôt que d'attendre sans fin.
- `liberer` : à la fin de chaque opération lourde, ramasse-miettes puis
  `malloc_trim`, qui rend au système la mémoire que la bibliothèque C garde
  en réserve. Complété par MALLOC_ARENA_MAX=2 dans Dockerfile.web, qui évite
  qu'une réserve se constitue par fil d'exécution.
- deux lignes de journal par opération lourde : au début, puis à la fin
  (durée, attente, mémoire avant, pic, après libération). Au prochain arrêt,
  la ligne « début » restée sans fin, juste avant l'heure de l'onglet Events
  de Render, désigne l'opération en cause.

Ordre des verrous. La place dans la file se prend AVANT tout verrou de projet
(décorateur posé sur la route elle-même) : une requête qui tiendrait le
verrou d'un projet en attendant la file pourrait sinon bloquer, pour
toujours, l'opération en cours qui attendrait ce même verrou. Une opération
lourde qui en appelle une autre (notices ERP en PDF → notices ERP) ne
reprend pas de place : la file est réentrante pour un même fil.
"""
from __future__ import annotations

import contextlib
import ctypes
import ctypes.util
import functools
import gc
import logging
import os
import sys
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)
# Sous uvicorn, seuls ses propres journaux s'affichent : sans gestionnaire,
# les lignes INFO de ce module n'apparaîtraient pas dans les logs Render.
logger.setLevel(logging.INFO)
if not logger.handlers:
    _sortie = logging.StreamHandler(sys.stderr)
    _sortie.setFormatter(logging.Formatter("%(levelname)s:     %(message)s"))
    logger.addHandler(_sortie)


def _entier_env(nom: str, defaut: int) -> int:
    try:
        return max(1, int(os.environ.get(nom, defaut)))
    except ValueError:
        return defaut


SIMULTANEES = _entier_env("GVDP_OPERATIONS_LOURDES", 1)
ATTENTE_MAX_S = _entier_env("GVDP_ATTENTE_MAX_S", 300)
# Au-delà, la ligne de journal passe en avertissement : on approche des 512 Mo.
ALERTE_MO = _entier_env("GVDP_MEMOIRE_ALERTE_MO", 400)

_PLACES = threading.BoundedSemaphore(SIMULTANEES)
_fil = threading.local()


class ServeurOccupe(RuntimeError):
    """La file des opérations lourdes n'a pas libéré de place à temps (503)."""


# ------------------------------------------------------------------ mesure

_STATUS = Path("/proc/self/status")


def _statut_mo(cle: str) -> float | None:
    """Valeur de /proc/self/status en Mo (Linux seulement, sinon None)."""
    try:
        for ligne in _STATUS.read_text().splitlines():
            if ligne.startswith(cle + ":"):
                return int(ligne.split()[1]) / 1024        # en kB dans le fichier
    except (OSError, ValueError, IndexError):
        pass
    return None


def rss_mo() -> float | None:
    """Mémoire occupée par le processus en ce moment (c'est ce que Render compte)."""
    return _statut_mo("VmRSS")


def pic_mo() -> float | None:
    """Plus haut niveau atteint depuis la dernière remise à zéro."""
    return _statut_mo("VmHWM")


def _remettre_pic_a_zero() -> None:
    """Le pic redescend au niveau actuel (Linux >= 4.0). Sans effet ailleurs :
    le pic lu est alors celui depuis le démarrage, toujours un majorant."""
    try:
        Path("/proc/self/clear_refs").write_text("5")
    except OSError:
        pass


# ------------------------------------------------------------------ libération

def _charger_malloc_trim():
    if not sys.platform.startswith("linux"):
        return None
    try:
        libc = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6")
        return libc.malloc_trim                              # glibc seulement
    except (OSError, AttributeError):
        return None


_MALLOC_TRIM = _charger_malloc_trim()


def liberer() -> None:
    """Rend au système ce que Python et la bibliothèque C gardent en réserve."""
    gc.collect()
    if _MALLOC_TRIM is not None:
        _MALLOC_TRIM(0)


# ------------------------------------------------------------------ la file

def _fmt(mo: float | None) -> str:
    return "?" if mo is None else f"{mo:.0f}"


@contextlib.contextmanager
def operation_lourde(nom: str):
    """Exécute le bloc quand la file a une place ; mesure et libère à la fin."""
    if getattr(_fil, "profondeur", 0):
        # déjà dans une opération lourde sur ce fil : pas de seconde place
        _fil.profondeur += 1
        try:
            yield
        finally:
            _fil.profondeur -= 1
        return

    debut = time.monotonic()
    if not _PLACES.acquire(timeout=ATTENTE_MAX_S):
        logger.warning("Opération lourde %s refusée : file occupée depuis %d s",
                       nom, ATTENTE_MAX_S)
        raise ServeurOccupe(
            "Le serveur termine une autre génération (dossier, notices ou planches). "
            "Réessayez dans une minute.")
    attente = time.monotonic() - debut
    _fil.profondeur = 1
    if SIMULTANEES == 1:
        _remettre_pic_a_zero()             # seule en cours : le pic est le sien
    avant = rss_mo()
    # ligne de DÉBUT : si le service tombe pendant l'opération, la ligne de
    # fin n'est jamais écrite ; un début sans fin désigne alors le coupable
    logger.info("GV_DP mémoire | début %s (attente %.1f s, %s Mo)",
                nom, attente, _fmt(avant))
    debut_calcul = time.monotonic()
    try:
        yield
    finally:
        duree = time.monotonic() - debut_calcul
        pic = pic_mo()
        liberer()
        apres = rss_mo()
        _fil.profondeur = 0
        _PLACES.release()
        niveau = logging.WARNING if pic is not None and pic >= ALERTE_MO else logging.INFO
        logger.log(niveau,
                   "GV_DP mémoire | %s : %.1f s (attente %.1f s) | %s Mo avant, "
                   "pic %s Mo, %s Mo après libération",
                   nom, duree, attente, _fmt(avant), _fmt(pic), _fmt(apres))


def lourde(nom: str):
    """Décorateur de route : la route entière passe par la file.

    Le projet visé figure dans le journal (paramètre `projet_id` de la route),
    pour relier un arrêt mémoire à un dossier précis.
    """
    def decorer(fonction):
        @functools.wraps(fonction)
        def enveloppe(*args, **kwargs):
            projet_id = kwargs.get("projet_id") or (args[0] if args else None)
            libelle = f"{nom} [{projet_id}]" if projet_id else nom
            with operation_lourde(libelle):
                return fonction(*args, **kwargs)
        return enveloppe
    return decorer
