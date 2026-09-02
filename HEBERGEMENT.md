# Héberger GV_DP en ligne

> **Ce n'est PAS la voie retenue.** Le partage se fait par le dossier OneDrive
> (voir `INSTALLATION.md`) : gratuit, immédiat, et les données restent chez
> Greenvolt. Ce document est conservé pour le jour où quelqu'un devra accéder à
> l'outil **sans avoir le dossier OneDrive** : un prestataire externe, une
> consultation hors du domaine.

L'application est prête pour cela, rien n'est à développer :

- image Docker complète (Python, LibreOffice pour l'export PDF, polices) ;
- page de connexion, sessions signées, déconnexion ;
- refus de démarrer si elle écoute au-delà de `127.0.0.1` sans authentification ;
- dossier des projets déplaçable par `GVDP_PROJETS_DIR`, volume `/data` déclaré.

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
