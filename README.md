# GV_DP — Générateur de Déclaration Préalable (ombrières PV de parking)

Outil interne Greenvolt Next France pour le bureau d'études : on saisit les
informations d'un projet d'ombrière photovoltaïque de parking, l'outil génère
le dossier de Déclaration Préalable prêt à déposer (Cerfa 16702*03, pièces DP1 à
DP11, notice, assemblage PPTX/PDF à la charte GV).

Plan directeur : [`../PLAN_OUTIL_DP.md`](../PLAN_OUTIL_DP.md) (référence, mis à jour le 2026-07-16).

## Lancement

- Windows : double-clic sur `Lancer GV_DP.bat` → http://localhost:8420
- Manuel : `.venv/Scripts/python -m uvicorn app.main:app --port 8420`
- **Jamais `--reload`** : le repo vit dans OneDrive, le watcher scannerait tout le dossier synchronisé.

## Installation (par machine, `.venv` non versionné)

```
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt      # Windows
# ou .venv/bin/pip install -r requirements.txt     # Mac
```

## Architecture (modulaire, cf. plan §11)

```
app/
  config.py        # chemins, couches IGN, CRS, seuils réglementaires
  models.py        # modèle de données du dossier (pydantic)
  regles.py        # moteur réglementaire : régime DP/PC, pièces, complétude
  geo/             # noyau géo : geocode (BAN), cadastre, gpu (PLU/ABF), georisques
  api/             # routes FastAPI (geo + CRUD projets)
  static/          # interface HTML/CSS/JS (wizard 7 étapes), Leaflet vendorisé
PROJETS/           # 1 JSON par projet (gitignoré : données client)
tests/             # pytest ; tests réseau marqués `live`, exclus par défaut
```

## État d'avancement (roadmap plan §15)

- [x] Phase 0 — Socle : repo, config, cas de référence Soufflenheim
- [x] Phase 1 — Noyau géo : adresse → parcelles (suggestion + manuel) → PLU → ABF → risques
- [x] Phase 2 — Modèle ombrière paramétrique (DP3 vectorielle, catalogue START PLAINE)
- [x] Phase 3 — Planches cartographiques DP1 à l'échelle (WMS GetMap, Lambert-93)
- [x] Phase 4 — Notice DP11 + Cerfa 16702 pré-rempli
- [x] Phase 5 — Module Insertion IA : **génération directe via l'API Gemini**
      (flux « un geste », 17-18/07/2026), en tâche de fond
- [x] Phase 6 — Assemblage PPTX + export PDF, mise en page fidèle à la maquette
      Claude Design « DP_Template » (page de garde, cartouche + badge par pièce,
      notice 2 colonnes, DP6 avant/après) ; images optimisées (JPEG, EXIF, 2400 px)
- [x] Phase 7 — Conteneurisation Azure (Dockerfile + doc) ; reste test utilisateur BE
- [x] Audit qualité/fiabilité du 19/07/2026 : EXIF, écritures atomiques, verrous,
      retries réseau, compteur de dépense fiable, mode « prêt au dépôt »

## Insertion IA (étape 4) : génération directe Gemini, flux « un geste »

Le module appelle **l'API Gemini image** (« Nano Banana », clé `GEMINI_API_KEY`
dans le `.env` CLAUDE). La seule saisie de placement est **un cliqué-glissé du
bord avant** de chaque ombrière sur la photo du site ; le type (Mono Bas /
Mono Haut / Double), les hauteurs et la pente viennent du catalogue, les cotes
du plan de masse (lecture automatique du cartouche GVN).

Gemini reçoit : la **photo repérée** (traits magenta), la **coupe** (la DP3 du
BE prime, sinon la coupe type nettoyée), une **photo de référence** réelle et un
**prompt déterministe** (proportions, sens de pente, interdiction du profil en Y,
repère d'échelle et consignes libres). L'image revient dans la galerie, avec
avant/après, réutilisation du prompt et contrôle de présence ; post-traitements
automatiques : décadrage, effacement du repère, recollage de la scène d'origine.

- La génération tourne **en tâche de fond** (verrou serveur anti double-dépense,
  reprise du suivi après un refresh).
- Compteur de dépense **local** (l'API n'expose pas de solde) + journal
  `PROJETS/_journal_ia.jsonl` (date, projet, coût, couverture, prompt).
- Les visuels sont étiquetés « visuel IA — usage commercial » : ils illustrent
  l'avant/après et les planches d'illustration, mais ne remplacent **jamais** la
  pièce DP6 officielle du BE.

> Flux précédents archivés dans `app/_archive/` : générateur de prompt ChatGPT
> sans API (16/07), scaffold/axes/vue aérienne v5 (17/07).

## Export : brouillon vs dépôt

- **Assembler le dossier (PPTX)** : mode brouillon, les pièces manquantes sont
  signalées en avertissements (placeholders dans le PPTX, coupe DP3 provisoire
  paramétrique si le BE n'a rien fourni).
- **Assembler pour dépôt** : contrôle bloquant, refuse tant que les 11 pièces ne
  sont pas prêtes (notice validée humainement incluse).
- **Nettoyer** : purge les fichiers régénérables (caches, images non retenues)
  du dossier `.assets` — utile sur OneDrive.

## Tests

```
.venv/Scripts/python -m pytest              # tests unitaires (offline)
.venv/Scripts/python -m pytest -m live      # + appels réels aux APIs open data
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
