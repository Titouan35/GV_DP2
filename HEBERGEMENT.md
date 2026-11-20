# Héberger GV_DP en ligne

Trois façons d'utiliser l'outil :

- **au poste**, depuis le dossier OneDrive (`INSTALLATION.md`) ;
- **en ligne avec un dossier lié** : l'outil est hébergé, les projets restent
  dans le dossier PROJETS de chaque poste (le OneDrive partagé). C'est le mode
  à retenir pour l'équipe ;
- **en ligne sans dossier lié** : espace temporaire, pour dépanner quelqu'un
  qui n'a ni le dossier OneDrive ni Edge ou Chrome.

---

## Mode web : rien n'est conservé sur le serveur

Le blocage de l'hébergement n'a jamais été Python, LibreOffice ni
l'authentification : c'était le **disque persistant**. Les dossiers sont des
fichiers, et aucune offre gratuite n'offre de volume qui survive à un
redéploiement.

Le mode web supprime le besoin d'en avoir un. Chaque visiteur reçoit un espace
de travail temporaire et isolé, purgé après 12 h d'inactivité. **Rien n'est
conservé côté serveur.**

### Le dossier lié : les projets restent sur le poste

Depuis le 20/11/2026, Edge et Chrome permettent de **lier le dossier PROJETS
du poste** (bouton « Lier mon dossier PROJETS » en haut de page). C'est alors
ce dossier qui fait foi, et le serveur n'en garde qu'une copie de travail le
temps des calculs :

1. à l'ouverture d'un projet, le navigateur envoie au serveur les fichiers du
   projet. Il n'envoie que ce que le serveur n'a pas déjà : la première
   ouverture de la journée transfère tout le dossier (20 à 80 Mo mesurés), les
   suivantes presque rien ;
2. après chaque enregistrement, dépôt de pièce ou assemblage, le navigateur
   rapatrie ce que le serveur a produit et l'écrit dans le dossier du poste ;
3. si le serveur a perdu sa copie (mise en veille, redémarrage), le navigateur
   la renvoie de lui-même et l'action reprend.

La liste des projets se calcule sans rien écrire sur le serveur : le
navigateur lui envoie les fiches JSON, il rend les lignes (régime, complétude).

En pratique, pour l'équipe :

- **on lie le dossier OneDrive partagé** `…\GV_DP\PROJETS`. Les collègues au
  poste et en ligne travaillent alors sur les mêmes dossiers, et l'historique
  des versions OneDrive reste le filet de sécurité ;
- la présence « ouvert par … » fonctionne entre les deux modes : le fichier
  `.lock` circule par OneDrive comme avant ;
- **la version du poste fait foi.** Si un fichier change sur le poste pendant
  qu'on travaille (synchro OneDrive d'un collègue), l'outil ne l'écrase pas.
  Le bandeau passe à l'orange et demande de rouvrir le projet ;
- le navigateur redemande l'accès au dossier à chaque session : un clic sur
  « Reconnecter » ;
- le dossier OneDrive doit être **disponible hors connexion** (« Toujours
  conserver sur cet appareil »), comme pour le mode poste.

Firefox et Safari ne savent pas lier un dossier : l'outil retombe alors sur
l'espace temporaire, et le bandeau l'annonce.

Ce que ça change, concrètement :

| | Mode poste (OneDrive) | En ligne, dossier lié | En ligne, temporaire |
|---|---|---|---|
| Suivi des dossiers dans la durée | oui | **oui, sur le poste** | non |
| Rien à installer sur le poste | non (450 Mo) | **oui** | **oui** |
| Disque persistant sur le serveur | sans objet | **non** | **non** |
| Mémoire du serveur | sans objet | **~150 Mo** | **~150 Mo** |
| Export PDF du dossier | oui | non (PPTX + Cerfa PDF) | non (PPTX + Cerfa PDF) |
| Données clients au repos sur le serveur | sans objet | **aucune** (copie de travail, 12 h max) | **aucune** |
| Navigateur | tous | Edge, Chrome | tous |

### Ce que le mode web ne fait pas

L'export PDF du dossier est indisponible sans LibreOffice : le bouton répond
avec un message explicite. Le PPTX reste téléchargeable et, avec le dossier
lié, il est écrit dans le dossier du poste : on l'exporte en PDF depuis
PowerPoint. Le Cerfa, lui, reste un PDF, il ne dépend pas de LibreOffice.

Sans dossier lié, il n'y a pas de suivi : quelqu'un qui ferme son onglet et
revient le lendemain repart de zéro. L'interface l'annonce en haut de page.

### Variables

Utilise `Dockerfile.web` (sans LibreOffice). Toute plateforme qui construit
depuis un Dockerfile convient, y compris les offres gratuites : il n'y a ni
volume à monter, ni base de données.

```
GVDP_MODE=web         (déjà dans l'image)
TZ=Europe/Paris       (déjà dans l'image) : les fichiers de présence portent
                      une heure locale, partagée avec les postes
GVDP_SECRET           chaîne aléatoire longue. Sans elle, chaque redémarrage
                      déconnecte tout le monde
GVDP_COMPTES          INDISPENSABLE pour l'équipe : sans compte, tout le monde
                      s'appelle « ? » et personne ne voit qu'un collègue a
                      ouvert un dossier
```

Une seule instance : un visiteur doit retomber sur l'instance qui détient son
espace.

Conseil pour les comptes : donne à chacun **son identifiant Windows**
(`%USERNAME%`). C'est le nom que le mode poste inscrit dans les fichiers de
présence : quelqu'un qui passe du poste à l'outil en ligne n'est alors pas
pris pour un collègue par sa propre présence.

---

## Koyeb, pas à pas (offre gratuite)

Vérifié le 20/11/2026 : une instance gratuite par organisation, 512 Mo de
mémoire, 0,1 vCPU, 2 Go de disque, région **Francfort**, pas de volume
persistant (inutile ici). Elle se met en veille après **1 h** sans visite ;
la première requête la réveille en quelques secondes, et le dossier lié
renvoie de lui-même la copie de travail perdue.

### 0. Mettre le code sur GitHub

Koyeb construit l'image depuis un dépôt GitHub (privé accepté) ; le dépôt
`origin` de ce dossier convient. Avant de pousser, vérifie que **tous** les
modules de `app/` sont suivis : le 20/11/2026, plusieurs fichiers récents
(`cerfa_erp.py`, `coupes_dp3.py`, `decoupe_pdf.py`, `routes_synchro.py`, …)
n'étaient pas encore dans git, et l'image ne démarrerait pas sans eux.

```
git add -A
git status        # PROJETS/, runtime/ et .venv/ ne doivent PAS apparaître
git commit -m "Mode web : dossier lié"
git push
```

`PROJETS/` est exclu par `.gitignore` et `.dockerignore` : aucun dossier client
ne part sur GitHub ni dans l'image.

### 1. Préparer les secrets, sur un poste

Une empreinte de mot de passe par personne. Le mot de passe ne quitte pas le
poste et n'est jamais stocké, seulement son empreinte :

```
%LOCALAPPDATA%\GV_DP\.venv\Scripts\python.exe -m app.securite
```

Assemble les lignes obtenues, séparées par des virgules :
`identifiant1:<empreinte>,identifiant2:<empreinte>`.

Secret de signature des sessions :

```
%LOCALAPPDATA%\GV_DP\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(48))"
```

### 2. Créer le service dans Koyeb

Dans le tableau de bord : **Create Web Service → GitHub**, puis le dépôt.

| Réglage | Valeur |
|---|---|
| Builder | **Dockerfile** |
| Dockerfile location | `Dockerfile.web` |
| Instance | **Free** |
| Region | **Frankfurt** |
| Port exposé | `8420`, protocole HTTP, route `/` |
| Health check | HTTP, port `8420`, chemin `/api/sante` |
| Variables | `GVDP_SECRET` et `GVDP_COMPTES`, en **Secret** (pas en texte clair) |

Le déploiement construit l'image (quelques minutes), puis donne une adresse
`https://<service>-<organisation>.koyeb.app`. Le HTTPS est fourni par Koyeb ;
le navigateur l'exige d'ailleurs pour lier un dossier.

### 3. Vérifier

```
curl https://<adresse>/api/sante
```

La réponse doit contenir `"mode":"web"` et `"authentification":"comptes"`.
Puis, dans Edge : se connecter, cliquer « Lier mon dossier PROJETS », choisir
le dossier OneDrive `PROJETS`, ouvrir un projet et l'assembler.

### Points à surveiller au premier essai

- **La durée de l'assemblage.** Mesuré sur un poste le 20/11/2026 sur un
  dossier réel : 6 s de calcul pur, 13 à 25 s au total avec les planches
  cartographiques. Avec 0,1 vCPU, compter de l'ordre de dix fois plus de
  calcul. D'après un message du personnel Koyeb sur leur forum (pas dans la
  documentation officielle), une requête est coupée au bout de **120 s**. Si
  l'assemblage échoue sur un délai dépassé, il faudra le rendre asynchrone ou
  passer sur une instance payante.
- **Le disque de 2 Go.** Il porte l'image et les copies de travail. Une
  dizaine de projets ouverts dans la même demi-journée tient sans peine ; la
  purge à 12 h et la mise en veille vident le reste.
- **Le réveil.** Après 1 h sans visite, la première page met quelques
  secondes à répondre.

---

## Mode poste hébergé (avec suivi des dossiers)

## Ce qu'il faudrait alors

| Besoin | Pourquoi |
|---|---|
| Build depuis le `Dockerfile` | l'image installe Python, LibreOffice et les polices |
| **Volume persistant** | les dossiers clients sont des fichiers ; sans volume, un redéploiement les efface |
| **Une seule instance** | les verrous sont des verrous de processus |
| HTTPS | mots de passe et sessions ne doivent pas circuler en clair |
| ~1 vCPU, 2 Go de RAM | LibreOffice a besoin de mémoire pour la conversion PDF |

Variables à fournir :

```
GVDP_COMPTES        florent:<empreinte>,collegue:<empreinte>
GVDP_SECRET         <chaîne aléatoire longue>
GVDP_PROJETS_DIR    /data          (déjà positionné dans l'image)
```

Empreinte d'un mot de passe : `runtime/python.exe -m app.securite`.
Le mot de passe n'est jamais stocké, seulement une empreinte PBKDF2-SHA256 salée.

Si l'hébergeur authentifie en amont (Entra ID, proxy d'entreprise) :
`GVDP_AUTH_DELEGUEE=1`. L'identité est reprise de `X-MS-CLIENT-PRINCIPAL-NAME`.

## Ce qui ne convient pas

**Netlify, Vercel, GitHub Pages.** Leurs fonctions n'acceptent que JavaScript et
TypeScript ; GV_DP est une application Python qui tourne en continu, écrit des
fichiers et pilote LibreOffice. Aucun réglage n'y change rien. Netlify peut
seulement servir de relais vers un serveur externe, avec un plafond de
26 secondes par requête (mesuré : un assemblage prend 8 à 10 s, l'export PDF
peut dépasser).

**Les offres gratuites de conteneurs.** Vérifié en septembre 2026 : pas de
disque persistant, et 512 Mo de mémoire. Les dossiers clients seraient effacés
au premier redéploiement.

## La décision qui n'est pas technique

Les dossiers contiennent nom, adresse électronique, téléphone et SIRET de
maîtres d'ouvrage, plus des plans et photos de sites clients. Les héberger
engage Greenvolt. La voie propre reste Azure Container Apps dans le tenant
Greenvolt (`DEPLOIEMENT_AZURE.md`), avec accord de la DSI.

## Vérifier, le jour venu

```bash
curl https://<domaine>/api/sante
```

La réponse doit contenir `"authentification":"comptes"` ou `"delegue"`.
Si elle contient `"local"`, l'instance est **ouverte** : arrête-la.
