# Héberger GV_DP en ligne, pour le partager

Objectif : que le bureau d'études accède à l'outil depuis un navigateur, sans
rien installer.

> **Netlify ne peut pas exécuter GV_DP**, et aucun réglage n'y changera rien :
> les Netlify Functions n'acceptent que JavaScript et TypeScript, alors que
> GV_DP est une application Python qui tourne en continu, écrit des fichiers et
> pilote LibreOffice. Même chose pour Vercel et GitHub Pages.
>
> **Netlify reste utile pour autant** : il porte très bien le domaine, le
> certificat HTTPS et le relais vers l'application. Voir la section « Netlify
> en porte d'entrée » ci-dessous. L'application, elle, tourne sur un hébergeur
> de **conteneurs**.

---

## Netlify en porte d'entrée

Si tu tiens à passer par Netlify, le montage qui fonctionne est le suivant :
Netlify porte le domaine et relaie tout vers l'application, qui tourne
ailleurs. Netlify le prend en charge nativement (réécriture en `status = 200`
vers une URL absolue).

`netlify.toml` est déjà dans le dépôt. Une seule ligne à changer :

```toml
[[redirects]]
  from = "/*"
  to = "https://TON-HEBERGEUR/:splat"   # <- ici
  status = 200
  force = true
```

Tant que l'adresse d'exemple y figure, le site affiche une page qui l'explique,
plutôt qu'une erreur opaque.

### La limite à connaître

Un relais Netlify **expire à 26 secondes**. Mesuré sur un dossier réel
(Rive-de-Gier, 8 planches, cartes IGN comprises) :

| Opération | Durée |
|---|---|
| Assemblage du PPTX, à froid | 10,4 s |
| Assemblage du PPTX, caches chauds | 8,0 s |
| Export PDF (LibreOffice) | non mesuré, c'est le seul à risque |

L'usage courant passe largement. Seul l'export PDF d'un gros dossier peut
dépasser les 26 secondes. S'il expire, le PPTX reste téléchargeable : on ne perd
pas le travail, on perd la conversion.

### Si cette limite devient gênante

Fais pointer un sous-domaine directement sur l'hébergeur de conteneurs, par
exemple `dp.tondomaine.fr`, par un simple enregistrement DNS. Netlify garde le
site principal, l'outil vit à côté, et il n'y a plus ni relais ni plafond de
26 secondes. C'est plus simple et plus robuste que le relais, pour exactement le
même confort d'usage.

---

## Ce que l'hébergeur doit fournir

| Besoin | Pourquoi |
|---|---|
| Build depuis un `Dockerfile` | l'image installe Python, LibreOffice et les polices |
| **Volume persistant** | les dossiers clients sont des fichiers ; sans volume, un redéploiement les efface |
| **Une seule instance** | les verrous sont des verrous de processus (`threading.Lock`), plusieurs répliques les contourneraient |
| HTTPS | les mots de passe et les sessions ne doivent pas circuler en clair |
| ~1 vCPU, 2 Go de RAM | LibreOffice a besoin de mémoire pour convertir en PDF |

Le dépôt étant déjà sur GitHub, la plupart des hébergeurs construisent
directement depuis la branche : rien à installer sur ton poste.

---

## Variables à configurer

```
GVDP_COMPTES        florent:<empreinte>,agent-be:<empreinte>
GVDP_SECRET         <chaîne aléatoire longue>
GVDP_PROJETS_DIR    /data          (déjà positionné dans l'image)
```

Générer une empreinte, depuis le dossier GV_DP :

```bash
runtime/python.exe -m app.securite
```

Le mot de passe n'est **jamais** stocké : seule une empreinte PBKDF2-SHA256
salée (200 000 itérations) figure dans la variable.

`GVDP_SECRET` signe les sessions. Sans elle, une nouvelle valeur est tirée à
chaque démarrage et tout le monde doit se reconnecter après un déploiement.

En alternative à `GVDP_COMPTES`, si l'hébergeur authentifie en amont (Azure
Container Apps avec Entra ID, ou un proxy d'entreprise) : `GVDP_AUTH_DELEGUEE=1`.
L'application reprend alors l'identité de l'en-tête `X-MS-CLIENT-PRINCIPAL-NAME`.

---

## Le garde-fou, à comprendre avant de déployer

L'application **refuse de démarrer** si elle écoute au-delà de `127.0.0.1` sans
authentification configurée. Le conteneur écoute sur `0.0.0.0` : sans
`GVDP_COMPTES` ni `GVDP_AUTH_DELEGUEE`, il s'arrêtera avec un message explicite.

Ce n'est pas une panne. C'est ce qui empêche de publier par distraction des
données personnelles de maîtres d'ouvrage. Si un hébergeur ou un tutoriel te
demande de désactiver ce contrôle pour « faire marcher le déploiement », c'est
le signe qu'il ne faut pas y aller.

---

## La décision qui n'est pas technique

Les dossiers contiennent le **nom, l'adresse électronique, le téléphone et le
SIRET** de maîtres d'ouvrage, ainsi que des plans et photos de sites clients.
Les héberger chez un prestataire engage Greenvolt, pas seulement toi.

Trois cas, par ordre de sécurité décroissante :

1. **Azure Container Apps dans le tenant Greenvolt.** Les données restent dans
   l'infrastructure de l'entreprise, l'authentification s'appuie sur les comptes
   existants. Voir `DEPLOIEMENT_AZURE.md`. Demande à la DSI, compte en semaines.
2. **Un hébergeur de conteneurs européen** (Scaleway, OVHcloud, Clever Cloud).
   Fonctionne avec ce dépôt tel quel. À faire référencer par la DSI avant d'y
   mettre des dossiers réels, et à inscrire au registre des traitements.
3. **Un hébergeur hors Union européenne.** À éviter pour ces données.

**Recommandation pratique :** monte l'instance avec des dossiers de test, montre
qu'elle fonctionne, et sers-toi de cette démonstration pour obtenir l'option 1.
Ne verse pas de dossiers clients réels sur une instance dont le statut n'est pas
tranché.

---

## Vérifier après déploiement

```bash
curl https://<domaine>/api/sante
```

La réponse doit contenir `"authentification":"comptes"` ou `"delegue"`.
Si elle contient `"local"`, l'instance est **ouverte** : arrête-la.

Puis, dans un navigateur : `https://<domaine>` doit rediriger vers la page de
connexion, et non afficher l'outil.

---

## Sauvegardes

Le volume monté sur `/data` contient tout : un JSON par dossier et ses pièces.
Aucune base de données. Une copie régulière de ce volume suffit, et une
restauration consiste à le remettre en place.
