# GV_DP — Générateur de Déclaration Préalable (ombrières PV de parking)

Outil interne Greenvolt Next France pour le bureau d'études : on saisit les
informations d'un projet d'ombrière photovoltaïque de parking, l'outil génère
le dossier de Déclaration Préalable prêt à déposer (Cerfa 13404, pièces DP1 à
DP11, notice, assemblage PPTX/PDF à la charte GV).

Plan directeur : [`../PLAN_OUTIL_DP.md`](../PLAN_OUTIL_DP.md) (référence, mis à jour le 2026-07-13).

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
- [x] Phase 2 — Modèle ombrière paramétrique (DP3/DP4 vectorielles, catalogue START PLAINE)
- [x] Phase 3 — Planches cartographiques DP1 à l'échelle (WMS GetMap, Lambert-93)
- [x] Phase 4 — Notice DP11 + Cerfa 16702 pré-rempli
- [x] Phase 5 — Module Insertion IA (Gemini branché ; clé API à définir, voir ci-dessous)
- [x] Phase 6 — Assemblage PPTX + export PDF (PowerPoint)
- [ ] Phase 7 — Test utilisateur BE + déploiement Azure

## Insertion IA (étape 5) : créer la clé Gemini

Le module de génération de visuels commerciaux utilise **Gemini** (Google).
Tant que la clé n'est pas définie, l'étape 5 affiche le guide et reste inactive.

1. Ouvrir **https://aistudio.google.com/apikey** (compte Google), cliquer « Create API key », copier la clé (`AIza…`).
2. Définir la variable d'environnement sur ce poste, puis relancer GV_DP :

   ```powershell
   setx GEMINI_API_KEY "AIza…votre_clé…"
   ```

   Ouvrir un **nouveau** terminal (ou relancer `Lancer GV_DP.bat`) pour que la variable soit prise en compte.
3. Options : `GVDP_GEMINI_MODEL` pour changer de modèle (défaut `gemini-2.5-flash-image`,
   ex. `gemini-3.1-flash-image`), `GVDP_IMAGE_PROVIDER=azure-openai` pour un autre fournisseur.

La clé reste **côté serveur** (jamais envoyée au navigateur). Les visuels générés sont
étiquetés « visuel IA » et ne servent jamais de pièce DP6 officielle.

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
