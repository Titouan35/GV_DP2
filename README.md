# GV_DP — Générateur de Déclaration Préalable (ombrières PV de parking)

Outil interne Greenvolt Next France pour le bureau d'études : on saisit les
informations d'un projet d'ombrière photovoltaïque de parking, l'outil génère
le dossier de Déclaration Préalable prêt à déposer (Cerfa 16702*03, pièces DP1 à
DP11, notice, assemblage PPTX/PDF à la charte GV).

Plan directeur : [`../PLAN_OUTIL_DP.md`](../PLAN_OUTIL_DP.md) (référence, à
amender : il décrit encore le module Insertion, retiré le 01/09/2026).

> **Refonte du 01/09/2026.** Le module Insertion IA a été RETIRÉ. L'insertion
> paysagère (DP6) est désormais une image déposée par le bureau d'études,
> comme le plan de masse ou la coupe. Le parcours passe de 6 à 5 étapes, et
> l'outil ne fait plus aucun appel à une API payante : plus de clé, plus de
> coût variable, plus de compteur de dépense.

## Lancement

- Windows : double-clic sur `Lancer GV_DP.bat` → http://localhost:8420
- Manuel : `.venv/Scripts/python -m uvicorn app.main:app --port 8420`
- **Jamais `--reload`** : le repo vit dans OneDrive, le watcher scannerait tout le dossier synchronisé.

## Installation

Le dossier partagé embarque un **runtime Python portable** (`runtime/`) : sur un
poste du BE, il n'y a rien à installer, `Lancer GV_DP.bat` suffit.

Pour un poste de développement, ou si l'antivirus refuse le runtime portable,
`Installer GV_DP.bat` crée un environnement **hors OneDrive**
(`%LOCALAPPDATA%\GV_DP\.venv`). Ne créez pas de `.venv` à la racine du dépôt :
il serait synchronisé chez tout le monde (177 Mo, 6 093 fichiers mesurés).

## Architecture (modulaire, cf. plan §11)

```
app/
  config.py        # chemins, couches IGN, CRS, seuils réglementaires
  models.py        # modèle de données du dossier (pydantic)
  regles.py        # moteur réglementaire : régime DP/PC, pièces, complétude
  coherence.py     # contrôles de cohérence : données qui se contredisent
  securite.py      # authentification, refus de démarrer exposé sans protection
  geo/             # noyau géo : geocode (BAN), cadastre, gpu (PLU/ABF), georisques
  api/             # routes FastAPI (geo + CRUD projets)
  static/          # interface HTML/CSS/JS (parcours 5 étapes), Leaflet vendorisé
PROJETS/           # 1 JSON par projet (gitignoré : données client)
tests/             # pytest ; tests réseau marqués `live`, exclus par défaut
```

## État d'avancement (roadmap plan §15)

- [x] Phase 0 — Socle : repo, config, cas de référence Soufflenheim
- [x] Phase 1 — Noyau géo : adresse → parcelles (suggestion + manuel) → PLU → ABF → risques
- [x] Phase 2 — Modèle ombrière paramétrique (DP3 vectorielle, catalogue START PLAINE)
- [x] Phase 3 — Planches cartographiques DP1 à l'échelle (WMS GetMap, Lambert-93)
- [x] Phase 4 — Notice DP11 + Cerfa 16702 pré-rempli
- [x] Phase 6 — Assemblage PPTX + export PDF, mise en page fidèle à la maquette
      Claude Design « DP_Template » (page de garde, cartouche + badge par pièce,
      notice 2 colonnes, DP6 avant/après) ; images optimisées (JPEG, EXIF, 2400 px)
- [x] Phase 7 — Conteneurisation Azure (Dockerfile + doc) ; reste test utilisateur BE
- [x] Audit qualité/fiabilité du 19/07/2026 : EXIF, écritures atomiques, verrous,
      retries réseau, mode « prêt au dépôt »
- [x] Refonte du 01/09/2026 : retrait du module Insertion, contrôles de
      cohérence, corrections du Cerfa, authentification, lanceur fiabilisé
- [x] Partage à l'équipe : par le **dossier OneDrive** (voir `INSTALLATION.md`).
      Rien à installer côté collègue, le moteur Python est dans le dossier.
- [x] **Mode web** (`GVDP_MODE=web`, `Dockerfile.web`) : espace de travail
      éphémère par visiteur, rien conservé côté serveur, aucun disque
      persistant ni LibreOffice. Hébergeable gratuitement, pour donner l'outil
      à quelqu'un qui n'a pas le dossier OneDrive. Voir `HEBERGEMENT.md`.

## Le parcours (5 étapes)

1. **Localisation** — projet, adresse (BAN), parcelles (API Carto), PLU, risques
2. **Pièces du BE** — dépôt du plan de masse (DP2), de la coupe (DP3), du
   photomontage d'insertion (DP6) et des photos (DP7, DP8)
3. **Caractéristiques** — type d'ombrière, puissance, cotes
4. **Notice + Cerfa** — notice DP11, maître d'ouvrage, Cerfa 16702*03 pré-rempli
5. **Aperçu & export** — assemblage PPTX, export PDF

### Ce que l'outil s'interdit d'affirmer

Doctrine retenue avec Florent le 01/09/2026 : **brouillon avec trous signalés**.
Ce que l'outil ne sait pas, il le laisse vide et le dit, plutôt que de le
deviner. Concrètement : aucune cote du catalogue tant qu'aucun type d'ombrière
n'est choisi, aucune affirmation sur les risques ou le secteur ABF tant que les
APIs n'ont pas répondu, aucune case de consentement cochée à la place du
déclarant, et aucune conclusion juridique (conformité APER) mise dans sa bouche.

### Contrôles de cohérence

Compter les pièces ne suffit pas : une pièce peut être « prête » et porter une
donnée fausse. `app/coherence.py` traque les données qui se contredisent et les
affiche dans le panneau de droite : parcelles hors de la commune du terrain,
notice rédigée avec d'autres valeurs, pièce déclarée dont le fichier a disparu,
projet relevant en réalité du permis de construire. Les anomalies bloquantes
empêchent l'assemblage « pour dépôt ».

## Export : brouillon vs dépôt

- **Assembler le dossier (PPTX)** : mode brouillon, les pièces manquantes sont
  signalées en avertissements (placeholders dans le PPTX, coupe DP3 provisoire
  paramétrique si le BE n'a rien fourni).
- **Assembler pour dépôt** : contrôle bloquant, refuse tant que les 11 pièces ne
  sont pas prêtes (notice validée humainement incluse) **et** tant qu'une
  anomalie de cohérence bloquante subsiste.
- **Nettoyer** : purge les caches régénérables du dossier `.assets`, ainsi que
  le dossier `insertion/` hérité de l'ancien module — utile sur OneDrive.

## Tests

```
runtime/python.exe -m pytest              # tests unitaires (offline)
runtime/python.exe -m pytest -m live      # + appels réels aux APIs open data
```

Cas de recette : Allée du Golf, 67620 Soufflenheim (INSEE 67473,
parcelle 30 0464, ~30 201 m²) — dossier réel de référence dans `../EXEMPLE/`.

## Pièges connus

- CRS : APIs en WGS84 ordre [lon, lat] ; toute mesure de surface/distance en
  Lambert-93 (EPSG:2154). Les surfaces affichées = contenance cadastrale (m²).
- Couverture GPU partielle : flags `disponible` dans les réponses, l'UI dégrade proprement.
- Le point géocodé d'un grand parking ne tombe pas toujours sur la bonne
  parcelle : la suggestion reste à valider manuellement (décision plan §16).
- Repo dans OneDrive : éviter les sessions git simultanées Windows/Mac.
- L'outil **refuse de démarrer** s'il écoute au-delà de `127.0.0.1` sans
  authentification (`app/securite.py`). Ce n'est pas une panne : voir
  `HEBERGEMENT.md`.
- Netlify, Vercel et GitHub Pages ne conviennent pas : leurs fonctions
  n'acceptent que JavaScript, pas un serveur Python qui écrit sur disque.
- Le mode QuickEdit de Windows fige un serveur qui écrit dans sa console. Le
  lanceur redirige donc toute la sortie vers `%LOCALAPPDATA%\GV_DP\`.
