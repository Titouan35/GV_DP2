# GV_DP — image de déploiement (Azure Container Apps, tenant Microsoft GV).
# Python 3.13 + LibreOffice headless (conversion PPTX -> PDF côté Linux) +
# polices vectorielles pour le rendu des planches (Pillow).
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8420

# LibreOffice Impress (PPTX -> PDF) + polices (DejaVu/Liberation) pour Pillow
RUN apt-get update && apt-get install -y --no-install-recommends \
        libreoffice-impress \
        fonts-dejavu \
        fonts-liberation \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 1) dépendances Python (couche cachée tant que requirements ne bouge pas)
COPY requirements.txt ./
RUN pip install -r requirements.txt

# 2) code applicatif
COPY app ./app

# Données projet (montées sur un volume Azure Files en production, voir la doc)
RUN mkdir -p /app/PROJETS

EXPOSE 8420

# Healthcheck simple sur l'endpoint /api/sante
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:${PORT}/api/sante || exit 1

# uvicorn respecte $PORT (Azure Container Apps injecte le port d'ingress).
# ATTENTION : l'ecoute sur 0.0.0.0 declenche le controle de securite de
# app/securite.py. Le conteneur REFUSE de demarrer tant que GVDP_AUTH_DELEGUEE=1
# ou GVDP_COMPTES n'est pas fourni. Ce n'est pas une panne, c'est le garde-fou
# qui empeche de publier des donnees clients sans protection.
# Voir DEPLOIEMENT_AZURE.md, section "1 bis. Authentification".
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
