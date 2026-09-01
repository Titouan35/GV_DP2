"""Authentification, et refus de démarrer exposé sans protection.

Pourquoi ce module (01/09/2026). L'audit a montré que l'application n'avait
AUCUNE authentification : 52 routes ouvertes, dont un DELETE qui efface un
dossier client complet, sur des données qui contiennent le nom, l'adresse
électronique, le téléphone et le SIRET du maître d'ouvrage. Tant que le
serveur n'écoutait que sur 127.0.0.1, cela ne portait pas à conséquence. Dès
qu'il est hébergé pour le bureau d'études, cela en porte.

La règle appliquée ici est plus forte qu'un simple mot de passe : l'application
REFUSE DE DÉMARRER si elle est joignable au-delà de la machine locale sans
authentification configurée. Une protection qu'on peut oublier d'activer n'en
est pas une, et c'est exactement ce genre d'oubli qui publie un dossier client.

Trois modes :

  local      le serveur écoute sur 127.0.0.1 et aucun compte n'est configuré.
             Rien ne change pour l'usage au poste. C'est le cas par défaut.

  comptes    des comptes sont définis dans GVDP_COMPTES. Toute requête doit
             porter une authentification HTTP Basic valide. L'utilisateur
             authentifié devient l'auteur des modifications (en-tête
             X-Utilisateur déjà lu par routes_projets).

  delegue    GVDP_AUTH_DELEGUEE=1 : l'authentification est assurée EN AMONT
             par l'hébergeur (Azure Container Apps EasyAuth / Entra ID, ou un
             reverse proxy d'entreprise). L'application fait confiance à
             l'en-tête d'identité qu'il injecte. C'est la cible pour Greenvolt,
             parce qu'elle évite de gérer des mots de passe.

Format de GVDP_COMPTES : `utilisateur:empreinte` séparés par des virgules,
l'empreinte étant un SHA-256 salé produit par `python -m app.securite`.
Les mots de passe en clair ne sont jamais stockés ni journalisés.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import os
import secrets
import sys

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

# Routes joignables sans authentification : la sonde de santé de l'hébergeur
# doit répondre avant qu'un utilisateur ne soit connecté.
CHEMINS_LIBRES = frozenset({"/api/sante"})

ITERATIONS = 200_000


class ConfigurationDangereuse(RuntimeError):
    """Levée au démarrage plutôt que d'exposer des données sans protection."""


# ------------------------------------------------------------- empreintes

def empreinte(mot_de_passe: str, sel: str | None = None) -> str:
    """Empreinte salée d'un mot de passe, au format `sel$empreinte`."""
    sel = sel or secrets.token_hex(16)
    brut = hashlib.pbkdf2_hmac(
        "sha256", mot_de_passe.encode("utf-8"), sel.encode("utf-8"), ITERATIONS)
    return f"{sel}${binascii.hexlify(brut).decode()}"


def _verifier(mot_de_passe: str, attendu: str) -> bool:
    try:
        sel, _ = attendu.split("$", 1)
    except ValueError:
        return False
    # comparaison à temps constant : ne pas révéler le mot de passe par la durée
    return hmac.compare_digest(empreinte(mot_de_passe, sel), attendu)


def comptes() -> dict[str, str]:
    """Comptes configurés, lus dans GVDP_COMPTES. Vide si aucun."""
    brut = (os.environ.get("GVDP_COMPTES") or "").strip()
    resultat: dict[str, str] = {}
    for entree in brut.split(","):
        entree = entree.strip()
        if not entree or ":" not in entree:
            continue
        nom, _, empreinte_attendue = entree.partition(":")
        if nom.strip() and empreinte_attendue.strip():
            resultat[nom.strip()] = empreinte_attendue.strip()
    return resultat


def auth_deleguee() -> bool:
    return (os.environ.get("GVDP_AUTH_DELEGUEE") or "").strip() in ("1", "true", "oui")


LOCAUX = frozenset({"127.0.0.1", "localhost", "::1"})


def hote_effectif() -> str:
    """Adresse d'écoute réelle du serveur.

    Attention au piège : se fier à la seule variable GVDP_HOTE rendrait la
    garantie décorative, puisque personne ne la renseigne en tapant
    `uvicorn --host 0.0.0.0`. On lit donc la ligne de commande en premier,
    puis les variables d'environnement d'uvicorn, puis la nôtre.
    """
    argv = sys.argv
    for i, arg in enumerate(argv):
        if arg == "--host" and i + 1 < len(argv):
            return argv[i + 1].strip()
        if arg.startswith("--host="):
            return arg.split("=", 1)[1].strip()
    for cle in ("UVICORN_HOST", "GVDP_HOTE"):
        valeur = (os.environ.get(cle) or "").strip()
        if valeur:
            return valeur
    return "127.0.0.1"


def ecoute_locale(hote: str | None = None) -> bool:
    """Le serveur n'est-il joignable que depuis cette machine ?"""
    return (hote if hote is not None else hote_effectif()).strip() in LOCAUX


def mode() -> str:
    if auth_deleguee():
        return "delegue"
    if comptes():
        return "comptes"
    return "local"


def verifier_configuration(hote: str | None = None) -> str:
    """Contrôle de démarrage. Renvoie le mode, ou refuse de démarrer.

    C'est la garantie centrale de ce module : on ne peut pas exposer
    l'application sans l'avoir protégée, même par distraction.
    """
    actuel = mode()
    if actuel == "local" and not ecoute_locale(hote):
        raise ConfigurationDangereuse(
            "GV_DP refuse de démarrer : le serveur écoute sur "
            f"« {hote or hote_effectif()} », donc au-delà de cette "
            "machine, alors qu'aucune authentification n'est configurée. Les "
            "dossiers contiennent des données personnelles de maîtres d'ouvrage "
            "(nom, adresse électronique, téléphone, SIRET).\n"
            "Configurez GVDP_COMPTES (voir python -m app.securite) ou "
            "GVDP_AUTH_DELEGUEE=1 si l'hébergeur authentifie en amont."
        )
    return actuel


# ------------------------------------------------------------- middleware

class Authentification(BaseHTTPMiddleware):
    """HTTP Basic quand des comptes sont configurés ; sinon, laisse passer.

    Le mode est relu à chaque requête plutôt que figé au démarrage : les tests
    peuvent ainsi configurer des comptes sans reconstruire l'application.
    """

    async def dispatch(self, request, call_next):
        if request.url.path in CHEMINS_LIBRES:
            return await call_next(request)

        definis = comptes()
        if not definis:
            # mode local ou délégué : verifier_configuration a déjà tranché
            return await call_next(request)

        utilisateur = self._authentifier(request, definis)
        if utilisateur is None:
            return Response(
                status_code=401,
                headers={"WWW-Authenticate": 'Basic realm="GV_DP", charset="UTF-8"'},
                content="Authentification requise.",
            )

        # l'identité authentifiée devient l'auteur des modifications
        entetes = request.scope["headers"] = [
            (cle, valeur) for cle, valeur in request.scope["headers"]
            if cle != b"x-utilisateur"
        ]
        entetes.append((b"x-utilisateur", utilisateur.encode("utf-8")))
        return await call_next(request)

    @staticmethod
    def _authentifier(request, definis: dict[str, str]) -> str | None:
        entete = request.headers.get("authorization") or ""
        schema, _, valeur = entete.partition(" ")
        if schema.lower() != "basic" or not valeur:
            return None
        try:
            decode = base64.b64decode(valeur, validate=True).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError):
            return None
        nom, _, mot_de_passe = decode.partition(":")
        attendu = definis.get(nom)
        if not attendu or not _verifier(mot_de_passe, attendu):
            return None
        return nom


# --------------------------------------------------------- outil en ligne de commande

def _principal() -> None:  # pragma: no cover - utilitaire interactif
    import getpass
    print("Génération d'une empreinte de mot de passe pour GVDP_COMPTES.")
    nom = input("Identifiant : ").strip()
    mot_de_passe = getpass.getpass("Mot de passe : ")
    if not nom or not mot_de_passe:
        print("Identifiant et mot de passe sont requis.")
        return
    print("\nÀ placer dans la variable d'environnement GVDP_COMPTES :")
    print(f"  {nom}:{empreinte(mot_de_passe)}")
    print("\nPlusieurs comptes se séparent par des virgules.")


if __name__ == "__main__":  # pragma: no cover
    _principal()
