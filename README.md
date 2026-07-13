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
- [ ] Phase 2 — Modèle ombrière paramétrique (DP3/DP4 vectorielles)
- [ ] Phase 3 — Planches cartographiques DP1 à l'échelle (WMS GetMap)
- [ ] Phase 4 — Notice + Cerfa
- [ ] Phase 5 — Module Insertion IA (clé API requise)
- [ ] Phase 6 — Assemblage PPTX/PDF
- [ ] Phase 7 — Intégration & tests

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
