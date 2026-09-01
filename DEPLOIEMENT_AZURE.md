# Déploiement GV_DP sur Azure Container Apps

Cible : héberger l'outil pour toute l'équipe BE (tenant Microsoft Greenvolt).
Même code qu'en local ; l'image conteneur ajoute **LibreOffice** pour la
conversion PPTX → PDF côté Linux (le poste Windows utilise PowerPoint).

> **L'image REFUSE de démarrer sans authentification.** Ce n'est pas une
> panne : c'est voulu. Le conteneur écoute sur `0.0.0.0`, donc au-delà de la
> machine, et les dossiers contiennent des données personnelles de maîtres
> d'ouvrage (nom, adresse électronique, téléphone, SIRET). Une des routes
> efface un dossier client complet. Voir le §1 bis ci-dessous.

> **Le module Insertion IA a été retiré le 01/09/2026.** Aucune clé d'API
> n'est plus nécessaire : `GEMINI_API_KEY` et les variables associées ne
> servent plus à rien et peuvent être supprimées de la configuration.

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

## 1 bis. Authentification (obligatoire, l'application le vérifie)

Deux options, à trancher avec la DSI.

### Option A, recommandée : authentification déléguée à Azure (Entra ID)

Container Apps sait authentifier en amont de l'application (EasyAuth). Les
comptes Greenvolt existants sont utilisés, aucun mot de passe n'est géré par
l'outil, et les départs sont traités par la DSI comme pour n'importe quelle
application interne.

```bash
az containerapp auth microsoft update -n $APP -g $RG   --client-id <APP_ID> --tenant-id <TENANT_ID> --yes
az containerapp auth update -n $APP -g $RG   --unauthenticated-client-action RedirectToLoginPage
```

Puis déclarer à l'application que l'authentification est faite en amont :

```bash
az containerapp update -n $APP -g $RG --set-env-vars GVDP_AUTH_DELEGUEE=1
```

### Option B, dépannage : comptes gérés par l'application

Utile pour une mise à disposition rapide au BE sans attendre la DSI. Les mots
de passe ne sont JAMAIS stockés en clair : on ne dépose que des empreintes
salées (PBKDF2-SHA256, 200 000 itérations).

Générer une empreinte par personne, depuis le dossier GV_DP :

```bash
python -m app.securite
```

Puis les déclarer, séparées par des virgules :

```bash
az containerapp secret set -n $APP -g $RG   --secrets gvdp-comptes="florent:<empreinte>,hajar:<empreinte>"
az containerapp update -n $APP -g $RG   --set-env-vars GVDP_COMPTES=secretref:gvdp-comptes
```

### Ce que l'application garantit

- Elle refuse de démarrer si elle écoute au-delà de `127.0.0.1` sans l'une des
  deux options ci-dessus. Le message dit quoi configurer.
- `/api/sante` reste joignable sans authentification, pour la sonde de
  l'hébergeur. Elle ne divulgue aucune donnée de projet.
- L'utilisateur authentifié devient l'auteur des modifications (champ
  `modifie_par`), et un client ne peut pas usurper une identité en envoyant
  lui-même l'en-tête `X-Utilisateur`.
- En usage local au poste (écoute sur `127.0.0.1`), rien ne change : aucune
  authentification n'est demandée.

### Ce que l'application NE garantit PAS, et qui reste à arbitrer

- Il n'y a pas de rôles : toute personne authentifiée voit et modifie tous les
  projets, y compris la suppression. Pour un bureau d'études de quelques
  personnes, c'est un choix défendable ; il doit être explicite.
- Les verrous de génération sont des verrous de processus : l'hébergement doit
  rester à **une seule réplique** (`--min-replicas 1 --max-replicas 1`).
- Le stockage des dossiers clients doit être tranché avec la DSI (Azure Files
  dans le tenant Greenvolt, chiffrement, sauvegarde, durée de conservation).

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
