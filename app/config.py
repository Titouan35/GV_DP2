"""Configuration GV_DP : chemins, couches IGN, constantes réglementaires.

Le repo vit dans OneDrive (Innovation/OUTILS/DP/GV_DP), synchronisé
Windows + Mac : tous les chemins de données sont relatifs au repo.
PROJETS/ est gitignoré (données client, jamais versionnées).
"""
from __future__ import annotations

import os
import threading
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# pypdfium2/libpdfium n'est PAS thread-safe (pas de verrou interne côté lib).
# FastAPI exécute les routes sync dans un threadpool : deux requêtes qui
# ouvrent un PDF en même temps peuvent corrompre l'état global de la lib et
# planter tout le process avec une "access violation" (constaté 17/07/2026,
# route apercu-payload qui ouvre plan+coupe pendant qu'une autre requête
# ouvrait aussi un PDF). Tout usage de pdfium.PdfDocument DOIT être protégé
# par `with config.PDFIUM_LOCK:`.
PDFIUM_LOCK = threading.Lock()


def _env_candidates():
    """Emplacements de .env testés dans l'ordre (Windows + Mac).

    Florent range ses secrets dans le workspace CLAUDE (Innovation/CLAUDE/.env),
    partagé par tous ses outils ; on accepte aussi un .env local au repo.
    """
    override = os.environ.get("GVDP_ENV_FILE")
    if override:
        yield Path(override)
    yield REPO_ROOT / ".env"
    # remonte jusqu'à trouver un dossier CLAUDE/.env (marche Win + Mac,
    # la structure Innovation/CLAUDE étant identique sur les deux postes)
    for ancetre in REPO_ROOT.parents:
        yield ancetre / "CLAUDE" / ".env"


def _charger_env() -> Path | None:
    """Charge le premier .env trouvé dans os.environ (sans écraser l'existant)."""
    for chemin in _env_candidates():
        try:
            if not chemin.is_file():
                continue
        except OSError:
            continue
        for ligne in chemin.read_text(encoding="utf-8").splitlines():
            ligne = ligne.strip()
            if not ligne or ligne.startswith("#"):
                continue
            if ligne.startswith("export "):
                ligne = ligne[len("export "):]
            if "=" not in ligne:
                continue
            cle, _, val = ligne.partition("=")
            cle = cle.strip()
            val = val.strip().strip('"').strip("'")
            if cle and cle not in os.environ:  # l'env système garde la priorité
                os.environ[cle] = val
        return chemin
    return None


# Chargé une fois à l'import : les modules lisant os.environ voient les clés.
ENV_FILE_CHARGE = _charger_env()

def _projets_dir() -> Path:
    """Dossier des projets : local au repo par défaut, PARTAGEABLE en équipe.

    Ordre de priorité (20/07/2026, mode multi-poste) :
      1. variable d'environnement GVDP_PROJETS_DIR
      2. clé "projets_dir" de gvdp.config.json, à la racine du repo
      3. PROJETS/ dans le repo (comportement historique)

    Le mode partagé pointe vers un dossier OneDrive/SharePoint commun : chaque
    poste exécute son propre serveur mais tous lisent et écrivent les mêmes
    dossiers. Le compteur de dépense IA et le journal des générations vivent
    dans ce dossier, donc ils deviennent communs eux aussi.
    """
    env = os.environ.get("GVDP_PROJETS_DIR")
    if env:
        return Path(env).expanduser()
    fichier = REPO_ROOT / "gvdp.config.json"
    try:
        if fichier.is_file():
            import json
            valeur = json.loads(fichier.read_text(encoding="utf-8")).get("projets_dir")
            if valeur:
                return Path(valeur).expanduser()
    except (OSError, ValueError):
        pass          # config illisible : on retombe sur le dossier local
    return REPO_ROOT / "PROJETS"


PROJETS_DIR = _projets_dir()                 # 1 dossier JSON par projet


def assets_dir(projet_id: str):
    """Dossier des fichiers du projet (planches générées, uploads, exports)."""
    d = PROJETS_DIR / f"{projet_id}.assets"
    d.mkdir(parents=True, exist_ok=True)
    return d
DP_DIR = REPO_ROOT.parent                     # Innovation/OUTILS/DP
# Coupes types Solstyce START PLAINE : dossier externe ../COUPES en dev local,
# repli sur la copie embarquée dans le package (indispensable en conteneur, où
# le dossier externe n'est pas copié dans l'image).
_COUPES_EXTERNE = DP_DIR / "COUPES"
_COUPES_BUNDLE = REPO_ROOT / "app" / "gabarits" / "coupes"
COUPES_DIR = _COUPES_EXTERNE if _COUPES_EXTERNE.exists() else _COUPES_BUNDLE

APP_NAME = "GV_DP"
APP_TITLE = "Déclaration Préalable · Ombrières"
VERSION = "0.1.0"
PORT = 8420

# --- APIs open data (validées par test réel, cf. PLAN_OUTIL_DP.md §10) ---
GEOCODAGE_URL = "https://data.geopf.fr/geocodage/search"
APICARTO_CADASTRE_URL = "https://apicarto.ign.fr/api/cadastre/parcelle"
APICARTO_GPU_ZONE_URL = "https://apicarto.ign.fr/api/gpu/zone-urba"
APICARTO_GPU_MUNICIPALITY_URL = "https://apicarto.ign.fr/api/gpu/municipality"
APICARTO_GPU_SUP_S_URL = "https://apicarto.ign.fr/api/gpu/assiette-sup-s"
GEORISQUES_URL = "https://georisques.gouv.fr/api/v1"

# WMTS Géoplateforme (couches libres, sans clé)
WMTS_URL = "https://data.geopf.fr/wmts"
LAYER_ORTHO = "ORTHOIMAGERY.ORTHOPHOTOS"
LAYER_PLAN = "GEOGRAPHICALGRIDSYSTEMS.PLANIGNV2"
LAYER_PARCELLES = "CADASTRALPARCELS.PARCELLAIRE_EXPRESS"

# CRS : WGS84 (EPSG:4326) pour les APIs (ordre [lon, lat]), Lambert-93
# (EPSG:2154) obligatoire pour toute mesure de surface ou de distance
# (jamais d'aire en degrés). Les contenances viennent du cadastre (m²).
CRS_WGS84 = 4326
CRS_LAMBERT93 = 2154

# --- Réglementaire (décret 2024-1023, demandes >= 01/12/2024) ---
SEUIL_PC_KWC = 3000.0   # >= 3 MWc => Permis de Construire

# APIs sans SLA : timeout court + erreurs propres remontées à l'UI
HTTP_TIMEOUT = 20.0
