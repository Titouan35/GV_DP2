# Héberger GV_DP en ligne

Deux usages, deux modes. Le partage quotidien à l'équipe passe par le dossier
OneDrive (`INSTALLATION.md`), avec suivi des dossiers dans la durée. Le mode
**web** sert à autre chose : donner l'outil à quelqu'un qui n'a pas le dossier
OneDrive, sans rien conserver côté serveur.

---

## Mode web : espace éphémère, hébergeable gratuitement

Le blocage de l'hébergement n'a jamais été Python, LibreOffice ni
l'authentification : c'était le **disque persistant**. Les dossiers sont des
fichiers, et aucune offre gratuite n'offre de volume qui survive à un
redéploiement.

Le mode web supprime le besoin d'en avoir un. Chaque visiteur reçoit un espace
de travail temporaire et isolé, y monte son dossier, télécharge le PPTX et le
Cerfa, et l'espace est purgé après 12 h d'inactivité. **Rien n'est conservé.**

Ce que ça change, concrètement :

| | Mode poste (OneDrive) | Mode web |
|---|---|---|
| Suivi des dossiers dans la durée | oui | non, éphémère |
| Disque persistant nécessaire | oui | **non** |
| Mémoire nécessaire | 2 Go (LibreOffice) | **~150 Mo** |
| Export PDF du dossier | oui | non (PPTX + Cerfa PDF) |
| Données clients au repos sur le serveur | oui | **aucune** |
| Hébergement gratuit possible | non | **oui** |

### Déployer

Utilise `Dockerfile.web` (sans LibreOffice). Toute plateforme qui construit
depuis un Dockerfile convient, y compris les offres gratuites : il n'y a ni
volume à monter, ni base de données.

```
GVDP_MODE=web         (déjà dans l'image)
GVDP_SECRET           chaîne aléatoire longue, pour que les espaces survivent
                      à un redémarrage
GVDP_COMPTES          facultatif : restreint l'accès à l'équipe
```

Une seule réplique : un visiteur doit retomber sur l'instance qui détient son
espace.

### Ce que le mode web ne fait pas

L'export PDF du dossier est indisponible sans LibreOffice : le bouton répond
avec un message explicite et le PPTX reste téléchargeable. Le Cerfa, lui, reste
un PDF, il ne dépend pas de LibreOffice.

Et il n'y a pas de suivi : quelqu'un qui ferme son onglet et revient le
lendemain repart de zéro. L'interface l'annonce en haut de page.

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
