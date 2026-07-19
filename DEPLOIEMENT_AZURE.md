# Déploiement GV_DP sur Azure Container Apps

Cible : héberger l'outil pour toute l'équipe BE (tenant Microsoft Greenvolt).
Même code qu'en local ; l'image conteneur ajoute **LibreOffice** pour la
conversion PPTX → PDF côté Linux (le poste Windows utilise PowerPoint).

> **Secret à provisionner : `GEMINI_API_KEY`** — l'étape Insertion IA appelle
> l'API Gemini côté serveur (flux 17/07/2026). Sans la clé, l'outil fonctionne
> mais l'atelier de génération reste désactivé (message clair dans l'UI).
> Optionnels : `GVDP_GEMINI_MODEL` (défaut `gemini-3-pro-image`),
> `GVDP_COUT_IMAGE_EUR` (défaut 0,13), `GVDP_AUTO_RETRY=0` pour couper la
> relance automatique.

## 0. Prérequis

- Azure CLI (`az`) + extension Container Apps : `az extension add -n containerapp`
- Docker (build local) **ou** build managé par Azure (`az acr build`, ci-dessous).
- Droits sur un abonnement / resource group Greenvolt.

## 1. Variables

```bash
RG=rg-gvdp
LOC=francecentral
ACR=gvdpregistry          # nom global unique, minuscules
ENVI=gvdp-env
APP=gvdp
IMG=$ACR.azurecr.io/gvdp:1.0
```

## 2. Build de l'image (managé par Azure, pas besoin de Docker local)

```bash
az group create -n $RG -l $LOC
az acr create -n $ACR -g $RG --sku Basic --admin-enabled true
# build dans le cloud à partir du Dockerfile du repo (lancer depuis GV_DP/)
az acr build -r $ACR -t gvdp:1.0 .
```

En local avec Docker : `docker build -t $IMG .` puis `az acr login -n $ACR && docker push $IMG`.

## 3. Environnement + application

```bash
az containerapp env create -n $ENVI -g $RG -l $LOC

az containerapp create \
  -n $APP -g $RG --environment $ENVI \
  --image $IMG \
  --registry-server $ACR.azurecr.io \
  --target-port 8420 --ingress internal \
  --min-replicas 1 --max-replicas 1 \
  --cpu 1.0 --memory 2.0Gi \
  --secrets gemini-key=<clé Gemini> \
  --env-vars GEMINI_API_KEY=secretref:gemini-key
```

- `--ingress internal` : accessible uniquement depuis le réseau de l'entreprise
  (VNet). Mettre `external` seulement si un accès public est voulu (déconseillé,
  l'outil manipule des données projet). Ajouter une **authentification** (Easy
  Auth / Entra ID) avant toute exposition externe.
- `--memory 2.0Gi` : LibreOffice a besoin de RAM pour la conversion PDF.
- `--max-replicas 1` : les verrous (projets, génération, pdfium) sont des
  verrous **de process** ; plusieurs répliques les contourneraient. Une seule
  réplique suffit largement pour une équipe BE.
- L'identité de l'auteur des modifications peut être fournie par l'en-tête
  HTTP `X-Utilisateur` (posé par un proxy/Easy Auth) ; à défaut, le compte
  du conteneur est utilisé (peu parlant en prod).

## 4. Persistance des projets (recommandé)

Par défaut, `PROJETS/` est **éphémère** (perdu au redémarrage du conteneur).
Ce dossier contient les projets JSON + leurs assets, mais aussi le **compteur
de dépense IA** (`_compteur_ia.json`) et le **journal des générations**
(`_journal_ia.jsonl`) : sans persistance, la comptabilité IA repart de zéro.
Pour conserver le tout, monter un partage **Azure Files** sur `/app/PROJETS` :

```bash
# compte de stockage + partage
az storage account create -n gvdpstorage -g $RG -l $LOC --sku Standard_LRS
az storage share-rw create --account-name gvdpstorage --name projets

# déclarer le stockage dans l'environnement Container Apps
az containerapp env storage set -g $RG -n $ENVI \
  --storage-name projets \
  --azure-file-account-name gvdpstorage \
  --azure-file-account-key <clé> \
  --azure-file-share-name projets \
  --access-mode ReadWrite
```

Puis ajouter le volume + montage dans la définition de l'app (YAML `az containerapp update --yaml`, section `template.volumes` + `volumeMounts` sur `/app/PROJETS`).

## 5. Vérification

```bash
az containerapp show -n $APP -g $RG --query properties.configuration.ingress.fqdn -o tsv
# ouvrir https://<fqdn>/api/sante  → doit renvoyer {"app":"GV_DP", ...}
```

## Notes

- **Fonts** : DejaVu + Liberation sont installées dans l'image (planches Pillow
  nettes). La charte PPTX référence « Poppins » ; si Poppins n'est pas installée
  côté visionneuse, LibreOffice/PowerPoint substitue une police proche (couleurs
  et mise en page préservées).
- **APIs open data** (IGN, cadastre, Géorisques) : appelées en sortie HTTPS ;
  s'assurer que l'egress du Container App les autorise (par défaut ouvert).
- **Coupes types** : embarquées dans l'image (`app/gabarits/coupes/`), pas de
  dépendance au dossier `../COUPES` en production.
- **Mise à jour** : `az acr build -r $ACR -t gvdp:1.1 .` puis
  `az containerapp update -n $APP -g $RG --image $ACR.azurecr.io/gvdp:1.1`.
