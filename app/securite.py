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
import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import RedirectResponse, Response

# Routes joignables sans authentification : la sonde de santé de l'hébergeur
# doit répondre avant qu'un utilisateur ne soit connecté.
CHEMINS_LIBRES = frozenset({"/api/sante", "/connexion", "/static/css/app.css"})

# En-tetes d'identite injectes par les hebergeurs en mode delegue, par ordre de
# preference. Azure Container Apps (EasyAuth) utilise les deux premiers ; les
# reverse proxies d'entreprise le troisieme.
ENTETES_IDENTITE_AMONT = (
    "x-ms-client-principal-name",
    "x-ms-client-principal-id",
    "x-forwarded-user",
)

ITERATIONS = 200_000


# Empreinte d'un mot de passe qui n'existe pas : sert a faire durer une
# tentative sur un identifiant inconnu aussi longtemps que sur un identifiant
# connu (voir _authentifier).
LEURRE = ("0" * 32) + "$" + ("0" * 64)
_LEURRE = LEURRE      # ancien nom, conserve pour les appels internes


class ConfigurationDangereuse(RuntimeError):
    """Levée au démarrage plutôt que d'exposer des données sans protection."""


# ------------------------------------------------------------- empreintes

def empreinte(mot_de_passe: str, sel: str | None = None) -> str:
    """Empreinte salée d'un mot de passe, au format `sel$empreinte`."""
    sel = sel or secrets.token_hex(16)
    brut = hashlib.pbkdf2_hmac(
        "sha256", mot_de_passe.encode("utf-8"), sel.encode("utf-8"), ITERATIONS)
    return f"{sel}${binascii.hexlify(brut).decode()}"


def verifier_mot_de_passe(mot_de_passe: str, attendu: str) -> bool:
    try:
        sel, _ = attendu.split("$", 1)
    except ValueError:
        return False
    # comparaison à temps constant : ne pas révéler le mot de passe par la durée
    return hmac.compare_digest(empreinte(mot_de_passe, sel), attendu)


_verifier = verifier_mot_de_passe      # ancien nom, utilise par les tests


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


# --------------------------------------------------------------- sessions

# Nom du cookie de session et sa duree.
COOKIE_SESSION = "gvdp_session"
DUREE_SESSION_H = 12

# Secret de signature des sessions. Fourni par GVDP_SECRET en hebergement pour
# que les sessions survivent a un redemarrage ; tire au hasard sinon, ce qui
# est sur mais oblige a se reconnecter apres chaque relance.
_SECRET = (os.environ.get("GVDP_SECRET") or "").strip().encode("utf-8") or secrets.token_bytes(32)


def _signer(charge: str) -> str:
    empreinte = hmac.new(_SECRET, charge.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{charge}.{empreinte}"


def creer_session(utilisateur: str) -> str:
    """Jeton de session : identifiant + expiration, signes.

    Le separateur est « ~ » et le remplissage base64 est retire : Python met la
    valeur d'un cookie entre guillemets des qu'elle contient « : » ou « = »,
    et ces guillemets se retrouvent dans la valeur relue. Le jeton ne doit donc
    contenir que des caracteres consideres comme legaux par http.cookies.
    """
    expire = int(time.time()) + DUREE_SESSION_H * 3600
    nom = base64.urlsafe_b64encode(utilisateur.encode()).decode().rstrip("=")
    return _signer(f"{nom}~{expire}")


def verifier_session(jeton: str | None) -> str | None:
    """Identifiant porte par un jeton valide et non expire, sinon None."""
    if not jeton or "." not in jeton:
        return None
    charge, _, empreinte = jeton.rpartition(".")
    if not hmac.compare_digest(_signer(charge), f"{charge}.{empreinte}"):
        return None
    nom_encode, _, expire = charge.partition("~")
    try:
        if int(expire) < time.time():
            return None
        # remplissage base64 retire a l'encodage : on le restitue
        rembourre = nom_encode + "=" * (-len(nom_encode) % 4)
        nom = base64.urlsafe_b64decode(rembourre.encode()).decode("utf-8")
    except (ValueError, UnicodeDecodeError, binascii.Error):
        return None
    return nom if nom in comptes() else None


def cookie_securise(request) -> bool:
    """Le cookie ne doit voyager qu'en HTTPS dès que la connexion l'est.

    Derriere un hebergeur, le TLS est termine en amont : on lit l'en-tete
    standard que pose le proxy, sinon on retombe sur le schema de la requete.
    """
    transfere = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip()
    return (transfere or request.url.scheme) == "https"


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
            if auth_deleguee():
                # L'hebergeur authentifie en amont. Il injecte l'identite dans
                # SES en-tetes, que l'application ne lisait pas : tout le monde
                # etait donc anonyme, et n'importe qui pouvait s'attribuer
                # l'identite d'un collegue via X-Utilisateur.
                return await self._suivre_identite_amont(request, call_next)
            # mode local : verifier_configuration a deja tranche
            return await call_next(request)

        # 1. session posee par la page de connexion (usage humain)
        utilisateur = verifier_session(request.cookies.get(COOKIE_SESSION))
        # 2. sinon HTTP Basic, conserve pour les scripts et les sondes
        if utilisateur is None:
            utilisateur = self._authentifier(request, definis)
        if utilisateur is None:
            # Une requete d'API recoit un 401 exploitable ; un humain qui ouvre
            # une page est envoye sur le formulaire de connexion, au lieu de la
            # fenetre grise du navigateur, sans deconnexion possible.
            if request.url.path.startswith("/api/"):
                return Response(
                    status_code=401,
                    headers={"WWW-Authenticate": 'Basic realm="GV_DP", charset="UTF-8"'},
                    content="Authentification requise.",
                )
            return RedirectResponse("/connexion", status_code=303)

        # l'identité authentifiée devient l'auteur des modifications
        entetes = request.scope["headers"] = [
            (cle, valeur) for cle, valeur in request.scope["headers"]
            if cle != b"x-utilisateur"
        ]
        entetes.append((b"x-utilisateur", utilisateur.encode("utf-8")))
        return await call_next(request)

    async def _suivre_identite_amont(self, request, call_next):
        """Reprend l'identite injectee par l'hebergeur, et interdit l'usurpation."""
        identite = None
        for entete in ENTETES_IDENTITE_AMONT:
            valeur = (request.headers.get(entete) or "").strip()
            if valeur:
                identite = valeur[:80]
                break
        request.scope["headers"] = [
            (cle, valeur) for cle, valeur in request.scope["headers"]
            if cle != b"x-utilisateur"
        ]
        if identite:
            request.scope["headers"].append((b"x-utilisateur", identite.encode("utf-8")))
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
        # Sortir tot sur un identifiant inconnu creait un oracle : 24 ms pour un
        # inconnu contre 97 ms pour un compte existant (mesure de la relecture
        # du 01/09/2026). On deroule donc toujours un calcul de meme cout, avec
        # une empreinte leurre. Cela ferme aussi le levier de saturation CPU par
        # identifiant devine.
        attendu = definis.get(nom) or _LEURRE
        valide = _verifier(mot_de_passe, attendu)
        return nom if (valide and nom in definis) else None


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
