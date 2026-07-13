"""Configuration GV_DP : chemins, couches IGN, constantes réglementaires.

Le repo vit dans OneDrive (Innovation/OUTILS/DP/GV_DP), synchronisé
Windows + Mac : tous les chemins de données sont relatifs au repo.
PROJETS/ est gitignoré (données client, jamais versionnées).
"""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

PROJETS_DIR = REPO_ROOT / "PROJETS"          # 1 dossier JSON par projet
DP_DIR = REPO_ROOT.parent                     # Innovation/OUTILS/DP
COUPES_DIR = DP_DIR / "COUPES"                # coupes types Solstyce START PLAINE

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
