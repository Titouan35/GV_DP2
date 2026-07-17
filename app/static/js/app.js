/* GV_DP — interface du wizard (vanilla JS, local-first).
   Étapes 1-2 fonctionnelles (phases 0-1), étapes 3-7 en construction. */
"use strict";

// ---------------- état global ----------------
const state = {
  projet: null,      // objet projet complet (miroir du modèle pydantic)
  evaluation: null,  // {regime, completude} renvoyé par le backend
  etape: 0,          // 0 = accueil (liste des projets)
  geocodeResults: [],
  suggestions: [],   // parcelles proposées depuis l'adresse, à valider
  dirty: false,
};

const STEPS = [
  { n: 1, titre: "Localisation", sous: "Projet, adresse, parcelles" },
  { n: 2, titre: "Pièces du BE", sous: "Masse, coupe, façades, DP6" },
  { n: 3, titre: "Caractéristiques", sous: "Ombrière, coupe" },
  { n: 4, titre: "Insertion IA", sous: "Visuel commercial" },
  { n: 5, titre: "Notice + Cerfa", sous: "Notice, maître d'ouvrage" },
  { n: 6, titre: "Aperçu & export", sous: "PPTX, PDF, Cerfa" },
];

// Libellés « Coupe » présentés à l'utilisateur ; la valeur reste la clé catalogue.
const COUPES = [
  { val: "START PLAINE Bas", lbl: "Mono Bas" },
  { val: "START PLAINE Haut", lbl: "Mono Haut" },
  { val: "START PLAINE Double", lbl: "Double" },
];
const COUPE_LBL = Object.fromEntries(COUPES.map((c) => [c.val, c.lbl]));

// ---------------- utilitaires ----------------
const $ = (sel) => document.querySelector(sel);

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function fmtM2(v) {
  return v == null ? "—" : `${Number(v).toLocaleString("fr-FR")} m²`;
}

function toast(msg, type = "") {
  const el = document.createElement("div");
  el.className = `toast ${type}`;
  el.textContent = msg;
  $("#toasts").appendChild(el);
  setTimeout(() => el.remove(), 4200);
}

async function api(path, options = {}) {
  let resp;
  try {
    resp = await fetch(path, {
      headers: { "Content-Type": "application/json" },
      ...options,
    });
  } catch {
    toast("Serveur injoignable : relancez GV_DP.", "err");
    throw new Error("network");
  }
  if (!resp.ok) {
    let detail = `Erreur ${resp.status}`;
    try { detail = (await resp.json()).detail || detail; } catch { /* brut */ }
    toast(detail, "err");
    throw new Error(detail);
  }
  return resp.json();
}

// accès par chemin ("mo.raison_sociale") dans l'objet projet
function setPath(obj, path, value) {
  const keys = path.split(".");
  let cur = obj;
  for (const k of keys.slice(0, -1)) cur = cur[k] = cur[k] ?? {};
  cur[keys.at(-1)] = value === "" ? null : value;
}
function getPath(obj, path) {
  return path.split(".").reduce((cur, k) => (cur == null ? null : cur[k]), obj);
}

function nouveauProjet(nom) {
  return {
    nom,
    regime: "DP",
    statut: "brouillon",
    mo: {},
    localisation: { parcelles: [] },
    ombriere: { pente_deg: 5 },
    urbanisme: {},
    notice: { sections: {}, genere_par_ia: false, valide_humain: false },
    documents: {},
    meta: {},
  };
}

// ---------------- sauvegarde ----------------
let saveTimer = null;

async function sauvegarder(silencieux = true) {
  const p = state.projet;
  if (!p) return;
  const data = p.id
    ? await api(`/api/projets/${p.id}`, { method: "PUT", body: JSON.stringify(p) })
    : await api("/api/projets", { method: "POST", body: JSON.stringify(p) });
  state.projet = data.projet;
  state.evaluation = data.evaluation;
  state.dirty = false;
  renderChrome();
  if (!silencieux) toast("Projet enregistré.", "ok");
}

function sauvegarderBientot() {
  state.dirty = true;
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => {
    if (state.projet?.id) sauvegarder().catch(() => {});
  }, 900);
}

// ---------------- chrome (topbar, stepper, panneau droit) ----------------
function renderChrome() {
  const p = state.projet;
  $("#proj-badge").hidden = !p;
  if (p) $("#proj-nom").textContent = p.nom;
  $("#btn-save").disabled = !p;
  $("#btn-generer").disabled = !p;
  $("#btn-generer").title = p ? "Aller à l'étape Aperçu & export" : "Créez d'abord un projet";

  // stepper
  const nav = $("#stepper");
  nav.innerHTML = "";
  for (const s of STEPS) {
    const btn = document.createElement("button");
    btn.className = "step" + (state.etape === s.n ? " active" : "") +
      (etapeFaite(s.n) ? " done" : "");
    btn.disabled = !p;
    btn.innerHTML = `<span class="dot">${etapeFaite(s.n) && state.etape !== s.n ? "✓" : s.n}</span>
      <span class="lbl">${esc(s.titre)}<small>${esc(s.sous)}</small></span>`;
    btn.addEventListener("click", () => allerEtape(s.n));
    nav.appendChild(btn);
  }

  // panneau complétude
  const ev = state.evaluation;
  const list = $("#pieces-list");
  list.innerHTML = "";
  if (ev) {
    const c = ev.completude;
    const pct = Math.round((c.pretes / c.total) * 100);
    $("#ring").style.setProperty("--p", pct);
    $("#ring-txt").textContent = `${c.pretes}/${c.total}`;
    $("#prog-label").textContent = `${c.pretes} pièce${c.pretes > 1 ? "s" : ""} prête${c.pretes > 1 ? "s" : ""}`;
    $("#prog-sub").textContent = "sur " + c.total + " attendues";
    for (const piece of c.pieces) {
      const div = document.createElement("div");
      div.className = "piece";
      div.innerHTML = `<span class="st ${esc(piece.statut)}"></span>
        <span class="nm">${esc(piece.titre)}<small>${esc(piece.detail)}</small></span>`;
      list.appendChild(div);
    }
  } else {
    $("#ring").style.setProperty("--p", 0);
    $("#ring-txt").textContent = "—";
    $("#prog-label").textContent = "Aucun projet ouvert";
    $("#prog-sub").textContent = "Créez ou ouvrez un projet";
  }
}

function etapeFaite(n) {
  const p = state.projet;
  if (!p) return false;
  if (n === 1) return Boolean(p.id && p.nom && p.localisation?.code_insee && p.localisation?.parcelles?.length);
  if (n === 3) return Boolean(p.ombriere?.famille && p.ombriere?.puissance_kwc);
  return false;
}

function allerEtape(n) {
  if (!state.projet && n !== 0) return;
  state.etape = n;
  if (state.projet?.id && state.dirty) sauvegarder().catch(() => {});
  render();
}

// ---------------- rendu principal ----------------
function render() {
  renderChrome();
  const main = $("#main");
  if (state.etape === 0) return renderAccueil(main);
  if (state.etape === "fiche") return renderFicheProjet(main);
  if (state.etape === 1) return renderEtapeLocalisation(main);
  if (state.etape === 2) return renderEtapePieces(main);
  if (state.etape === 3) return renderEtapeCaracteristiques(main);
  if (state.etape === 4) return renderEtapeInsertion(main);
  if (state.etape === 5) return renderEtapeNotice(main);
  if (state.etape === 6) return renderEtapeExport(main);
}

// ---------------- accueil ----------------
async function renderAccueil(main) {
  main.innerHTML = `
    <div class="crumb">GV_DP</div>
    <h1>Projets</h1>
    <p class="sub">Ouvrez un dossier en cours ou créez un nouveau projet d'ombrière.</p>
    <div class="home-list">
      <div class="card" style="margin-bottom:18px">
        <div class="bd">
          <div class="field"><label for="new-nom">Nouveau projet</label>
            <div class="searchrow">
              <input id="new-nom" class="input" placeholder="Ex. : Carrefour Mondonville — parking nord" />
              <button class="btn navy" id="btn-new">Créer le projet</button>
            </div>
          </div>
        </div>
      </div>
      <div id="home-items"><p class="sub">Chargement…</p></div>
    </div>`;

  $("#btn-new").addEventListener("click", creerDepuisAccueil);
  $("#new-nom").addEventListener("keydown", (e) => { if (e.key === "Enter") creerDepuisAccueil(); });

  try {
    const data = await api("/api/projets");
    const box = $("#home-items");
    if (!data.projets.length) {
      box.innerHTML = `<p class="sub">Aucun projet pour l'instant.</p>`;
      return;
    }
    box.innerHTML = "";
    for (const pr of data.projets) {
      const btn = document.createElement("button");
      btn.className = "home-item";
      btn.innerHTML = `<span class="t">${esc(pr.nom)}<small>${esc(pr.commune || "localisation à saisir")} · modifié ${esc((pr.date_modification || "").slice(0, 10))}</small></span>
        <span class="badge">${esc(pr.regime || "DP")}</span>`;
      btn.addEventListener("click", () => ouvrirProjet(pr.id));
      box.appendChild(btn);
    }
  } catch { /* toast déjà affiché */ }
}

async function creerDepuisAccueil() {
  const nom = $("#new-nom").value.trim();
  if (!nom) { toast("Donnez un nom au projet.", "err"); return; }
  state.projet = nouveauProjet(nom);
  await sauvegarder();
  state.etape = 1;
  render();
}

async function ouvrirProjet(id) {
  const data = await api(`/api/projets/${id}`);
  state.projet = data.projet;
  state.evaluation = data.evaluation;
  state.suggestions = [];
  state.etape = "fiche";
  render();
}

// ---------------- fiche projet (avant d'entrer dans le dossier) ----------------
function renderFicheProjet(main) {
  const p = state.projet;
  const ev = state.evaluation;
  const c = ev?.completude;
  const pct = c ? Math.round((c.pretes / c.total) * 100) : 0;
  const loc = p.localisation || {};
  main.innerHTML = `
    <div class="crumb"><span class="link" id="fp-retour">← Projets</span></div>
    <div class="fiche-tete">
      <div>
        <h1 style="margin-bottom:2px">${esc(p.nom)}</h1>
        <p class="sub" style="margin:0">${esc(loc.adresse || "Localisation à saisir")}
          ${loc.code_insee ? " · INSEE " + esc(loc.code_insee) : ""} · ${esc(ev?.regime?.regime || "DP")} ${esc(ev?.regime?.cerfa || "")}</p>
      </div>
      <div class="fiche-actions">
        <button class="btn" id="fp-renommer">Renommer</button>
        <button class="btn" id="fp-suppr">Supprimer</button>
        <button class="btn primary" id="fp-ouvrir">Ouvrir le dossier</button>
      </div>
    </div>

    <div class="grid" style="grid-template-columns:260px minmax(0,1fr);margin-top:20px">
      <div class="card"><div class="bd" style="text-align:center">
        <div class="ring" style="--p:${pct};margin:6px auto 10px"><span>${pct}%</span></div>
        <b style="color:var(--gv-navy)">${c ? c.pretes : 0}/${c ? c.total : 0} pièces prêtes</b>
        <div class="sub" style="margin-top:2px">Avancement du dossier</div>
      </div></div>
      <div class="card"><div class="bd">
        <div class="ti-title" style="margin-bottom:8px">Aller à une étape</div>
        <div class="fiche-etapes" id="fp-etapes"></div>
      </div></div>
    </div>`;

  $("#fp-retour").addEventListener("click", () => { state.etape = 0; render(); });
  $("#fp-ouvrir").addEventListener("click", () => allerEtape(1));
  $("#fp-renommer").addEventListener("click", async () => {
    const nom = window.prompt("Nouveau nom du projet :", p.nom);
    if (!nom || !nom.trim()) return;
    state.projet.nom = nom.trim();
    await sauvegarder(false);
    renderFicheProjet($("#main"));
  });
  $("#fp-suppr").addEventListener("click", async () => {
    if (!window.confirm(`Supprimer définitivement le projet « ${p.nom} » ?`)) return;
    await api(`/api/projets/${p.id}`, { method: "DELETE" });
    toast("Projet supprimé.", "ok");
    state.projet = null; state.etape = 0; render();
  });
  const box = $("#fp-etapes");
  for (const s of STEPS) {
    const b = document.createElement("button");
    b.className = "fiche-etape" + (etapeFaite(s.n) ? " done" : "");
    b.innerHTML = `<span class="dot">${etapeFaite(s.n) ? "✓" : s.n}</span>
      <span>${esc(s.titre)}<small>${esc(s.sous)}</small></span>`;
    b.addEventListener("click", () => allerEtape(s.n));
    box.appendChild(b);
  }
}

// ---------------- étape 1 : projet ----------------
function champ(label, bind, opts = {}) {
  const val = esc(getPath(state.projet, bind) ?? "");
  const wide = opts.wide ? " wide" : "";
  if (opts.select) {
    const options = opts.select.map((o) =>
      `<option value="${esc(o)}" ${val === o ? "selected" : ""}>${esc(o)}</option>`).join("");
    return `<div class="field${wide}"><label>${esc(label)}</label>
      <select class="input" data-bind="${esc(bind)}"><option value=""></option>${options}</select></div>`;
  }
  const type = opts.type || "text";
  const ph = esc(opts.placeholder || "");
  return `<div class="field${wide}"><label>${esc(label)}</label>
    <input class="input" type="${type}" data-bind="${esc(bind)}" value="${val}" placeholder="${ph}" ${opts.step ? `step="${opts.step}"` : ""} /></div>`;
}

function brancherChamps(main, apres) {
  main.querySelectorAll("[data-bind]").forEach((el) => {
    el.addEventListener("input", () => {
      let v = el.value;
      if (el.type === "number") v = v === "" ? "" : Number(v);
      setPath(state.projet, el.dataset.bind, v);
      sauvegarderBientot();
      if (apres) apres(el.dataset.bind);
    });
  });
}

// ---------------- étape 1 : projet & localisation ----------------
let map = null;
let parcelLayer = null;
let marker = null;
let cadastreLayer = null;

function renderEtapeLocalisation(main) {
  const loc = state.projet.localisation;
  main.innerHTML = `
    <div class="crumb">Étape 1 / 6</div>
    <h1>Projet & localisation</h1>
    <p class="sub">Nom du dossier, adresse (ou coordonnées GPS), et parcelles concernées.</p>

    <div class="field">
      <label for="proj-nom-champ">Nom du projet</label>
      <input id="proj-nom-champ" class="input" data-bind="nom"
        value="${esc(state.projet.nom ?? "")}" placeholder="Ex. : Carrefour Mondonville — parking nord" />
    </div>

    <div class="field">
      <label for="adresse">Adresse du parking <span class="hint">— ou coordonnées « lat, lon »</span></label>
      <div class="searchrow">
        <input id="adresse" class="input" placeholder="Ex. : Allée du Golf, 67620 Soufflenheim   ·   ou   48.8133, 7.9524"
          value="${esc(loc.adresse ?? "")}" />
        <button class="btn navy" id="btn-geocode">Rechercher</button>
      </div>
      <div class="geo-results" id="geo-results" hidden></div>
    </div>

    <div class="grid grid-map">
      <div class="card">
        <div class="hd"><span class="ti-title">Repérage</span>
          <span style="font-size:11px;color:var(--muted)">Glissez le repère pour l'ajuster · cliquez une parcelle pour l'ajouter</span></div>
        <div id="map"></div>
      </div>
      <div class="card">
        <div class="hd"><span class="ti-title">Parcelles concernées</span></div>
        <div class="note" id="note-parcelles">Cliquez une parcelle sur la carte pour l'ajouter (infos pré-remplies), ou saisissez-la ci-dessous.</div>
        <table class="ptable">
          <thead><tr><th>Section</th><th>N°</th><th style="text-align:right">Surface</th><th></th></tr></thead>
          <tbody id="ptable-body"></tbody>
          <tbody>
            <tr class="add">
              <td><input class="mini" id="add-section" placeholder="Ex. 30" maxlength="3" /></td>
              <td><input class="mini" id="add-numero" placeholder="Ex. 464" maxlength="4" /></td>
              <td></td>
              <td><button class="addbtn" id="btn-add-parcelle">Ajouter</button></td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <details class="foldable" id="fold-urba" open>
      <summary>Synthèse urbanisme <span class="hint">(PLU, ABF, risques)</span></summary>
      <div class="chips" id="chips"></div>
    </details>

    <div class="actionsrow">
      <button class="btn navy" id="btn-suivant">Continuer vers les pièces du BE</button>
    </div>`;

  brancherChamps(main);
  $("#btn-geocode").addEventListener("click", rechercherAdresse);
  $("#adresse").addEventListener("keydown", (e) => { if (e.key === "Enter") rechercherAdresse(); });
  $("#btn-add-parcelle").addEventListener("click", ajouterParcelleManuelle);
  $("#btn-suivant").addEventListener("click", async () => {
    if (!state.projet.nom) { toast("Le nom du projet est requis.", "err"); return; }
    await sauvegarder();
    allerEtape(2);
  });

  initCarte();
  renderParcelles();
  renderChips();
}

// coordonnées « lat, lon » saisies à la main (France métropolitaine + DROM)
function parseCoords(s) {
  const m = String(s).match(/^\s*(-?\d{1,3}(?:[.,]\d+)?)\s*[,; ]\s*(-?\d{1,3}(?:[.,]\d+)?)\s*$/);
  if (!m) return null;
  const a = parseFloat(m[1].replace(",", ".")), b = parseFloat(m[2].replace(",", "."));
  const estLat = (v) => v >= 41 && v <= 52, estLon = (v) => v >= -6 && v <= 10;
  if (estLat(a) && estLon(b)) return { lat: a, lon: b };
  if (estLat(b) && estLon(a)) return { lat: b, lon: a };
  return null;
}

// positionne le projet sur un point (marqueur déplacé, clic, coordonnées)
async function positionner(lon, lat, opts = {}) {
  const loc = state.projet.localisation;
  loc.lon = lon; loc.lat = lat;
  majCarte(!!opts.fit);
  if (opts.reverse) {
    try {
      const { resultat } = await api(`/api/reverse-geocode?lon=${lon}&lat=${lat}`);
      if (resultat) {
        loc.adresse = resultat.label || loc.adresse;
        loc.commune = resultat.commune;
        loc.code_postal = resultat.code_postal;
        loc.code_insee = resultat.code_insee || loc.code_insee;
        const ins = loc.code_insee || "";
        loc.departement = ins.startsWith("97") ? ins.slice(0, 3) : ins.slice(0, 2);
        const adr = $("#adresse"); if (adr && loc.adresse) adr.value = loc.adresse;
      }
    } catch { /* non bloquant */ }
  }
  renderChips();
  sauvegarderBientot();
  chargerSuggestions(lon, lat);
  if (loc.code_insee) { chargerUrbanisme(lon, lat, loc.code_insee); chargerRisques(loc.code_insee); }
}

// clic sur la carte : identifie la parcelle au point cliqué et l'ajoute
async function cliquerCarteParcelle(latlng) {
  try {
    const data = await api(`/api/parcelles/suggest?lon=${latlng.lng}&lat=${latlng.lat}`);
    if (!data.parcelles.length) { toast("Aucune parcelle à cet endroit.", ""); return; }
    const dejaLa = new Set(state.projet.localisation.parcelles.map((p) => p.idu));
    let n = 0;
    for (const p of data.parcelles) {
      if (!dejaLa.has(p.idu)) { state.projet.localisation.parcelles.push({ ...p, source: "carte" }); n++; }
    }
    if (n) {
      toast(`${n} parcelle${n > 1 ? "s" : ""} ajoutée${n > 1 ? "s" : ""}.`, "ok");
      renderParcelles(); renderChips(); majCarte(); sauvegarderBientot();
    } else { toast("Parcelle déjà présente.", ""); }
  } catch { /* toast déjà affiché */ }
}

function initCarte() {
  if (map) { map.remove(); map = null; }
  const loc = state.projet.localisation;
  const centre = loc.lat != null ? [loc.lat, loc.lon] : [46.6, 2.4]; // France
  const zoom = loc.lat != null ? 18 : 6;

  map = L.map("map", { zoomControl: true }).setView(centre, zoom);
  const wmts = (layer, format) =>
    `https://data.geopf.fr/wmts?SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0` +
    `&LAYER=${layer}&STYLE=normal&TILEMATRIXSET=PM&FORMAT=${format}` +
    `&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}`;
  const plan = L.tileLayer(wmts("GEOGRAPHICALGRIDSYSTEMS.PLANIGNV2", "image/png"),
    { maxZoom: 19, attribution: "IGN — Géoplateforme" });
  const ortho = L.tileLayer(wmts("ORTHOIMAGERY.ORTHOPHOTOS", "image/jpeg"),
    { maxZoom: 21, maxNativeZoom: 20, attribution: "IGN — Géoplateforme" });
  cadastreLayer = L.tileLayer(wmts("CADASTRALPARCELS.PARCELLAIRE_EXPRESS", "image/png"),
    { maxZoom: 21, maxNativeZoom: 20, opacity: 0.8 });
  (state.projet.localisation.parcelles.length || loc.lat != null ? ortho : plan).addTo(map);
  cadastreLayer.addTo(map);  // limites de parcelles visibles pour cliquer
  L.control.layers(
    { "Plan IGN": plan, "Photo aérienne": ortho },
    { "Cadastre": cadastreLayer },
    { collapsed: true },
  ).addTo(map);

  parcelLayer = L.geoJSON(null, {
    style: { color: "#05DB79", weight: 2.5, fillColor: "#05DB79", fillOpacity: 0.14 },
  }).addTo(map);

  map.on("click", (e) => cliquerCarteParcelle(e.latlng));
  majCarte(true);
}

function majCarte(fit = false) {
  if (!map) return;
  const loc = state.projet.localisation;
  parcelLayer.clearLayers();
  for (const p of loc.parcelles) {
    if (p.geometry) {
      parcelLayer.addData({
        type: "Feature", geometry: p.geometry,
        properties: { label: `${p.section} ${p.numero}` },
      });
    }
  }
  // marqueur : créé une fois, ensuite simplement déplacé (évite de recréer le layer)
  if (loc.lat != null) {
    if (!marker) {
      marker = L.marker([loc.lat, loc.lon], { draggable: true, title: "Glissez pour repositionner" }).addTo(map);
      marker.on("dragend", () => {
        const ll = marker.getLatLng();
        positionner(ll.lng, ll.lat, { reverse: true });
      });
    } else {
      marker.setLatLng([loc.lat, loc.lon]);
    }
  } else if (marker) {
    marker.remove(); marker = null;
  }
  if (fit) {  // recadrage seulement sur géocodage / repositionnement volontaire
    const bounds = parcelLayer.getBounds();
    if (bounds.isValid()) map.fitBounds(bounds.pad(0.2));
    else if (loc.lat != null) map.setView([loc.lat, loc.lon], 18);
  }
}

async function rechercherAdresse() {
  const q = $("#adresse").value.trim();
  const coords = parseCoords(q);
  if (coords) {
    $("#geo-results").hidden = true;
    await positionner(coords.lon, coords.lat, { reverse: true, fit: true });
    toast("Positionné sur les coordonnées.", "ok");
    return;
  }
  if (q.length < 3) return;
  const box = $("#geo-results");
  box.hidden = false;
  box.innerHTML = `<button disabled>Recherche…</button>`;
  try {
    const data = await api(`/api/geocode?q=${encodeURIComponent(q)}`);
    state.geocodeResults = data.resultats;
    if (!data.resultats.length) {
      box.innerHTML = `<button disabled>Aucun résultat.</button>`;
      return;
    }
    box.innerHTML = "";
    data.resultats.forEach((r, i) => {
      const b = document.createElement("button");
      b.innerHTML = `${esc(r.label)} <small>· ${esc(r.contexte || "")}</small>`;
      b.addEventListener("click", () => choisirAdresse(i));
      box.appendChild(b);
    });
  } catch { box.hidden = true; }
}

async function choisirAdresse(i) {
  const r = state.geocodeResults[i];
  $("#geo-results").hidden = true;
  $("#adresse").value = r.label;
  const insee = r.code_insee || "";
  const loc = state.projet.localisation;
  Object.assign(loc, {
    adresse: r.label,
    code_postal: r.code_postal,
    commune: r.commune,
    code_insee: insee,
    departement: insee.startsWith("97") ? insee.slice(0, 3) : insee.slice(0, 2),
    lon: r.lon,
    lat: r.lat,
  });
  majCarte(true);
  renderChips();
  sauvegarderBientot();

  // 3 appels indépendants : suggestion parcelles, urbanisme, risques
  chargerSuggestions(r.lon, r.lat);
  chargerUrbanisme(r.lon, r.lat, insee);
  chargerRisques(insee);
}

async function chargerSuggestions(lon, lat) {
  try {
    const data = await api(`/api/parcelles/suggest?lon=${lon}&lat=${lat}`);
    const dejaLa = new Set(state.projet.localisation.parcelles.map((p) => p.idu));
    state.suggestions = data.parcelles.filter((p) => !dejaLa.has(p.idu));
    renderParcelles();
    if (!data.parcelles.length) {
      toast("Aucune parcelle sous le point géocodé : ajoutez-les à la main.", "");
    }
  } catch { /* toast déjà affiché */ }
}

async function chargerUrbanisme(lon, lat, insee) {
  try {
    const data = await api(`/api/urbanisme?lon=${lon}&lat=${lat}&insee=${encodeURIComponent(insee)}`);
    const u = state.projet.urbanisme;
    u.zonage = data.zonage;
    u.servitudes = data.servitudes;
    u.rnu = data.rnu ?? null;
    if (u.secteur_abf == null && data.servitudes?.disponible) {
      u.secteur_abf = Boolean(data.servitudes.abf);
    }
    renderChips();
    sauvegarderBientot();
  } catch { /* non bloquant */ }
}

async function chargerRisques(insee) {
  if (!insee) return;
  try {
    const data = await api(`/api/risques?insee=${encodeURIComponent(insee)}`);
    state.projet.urbanisme.risques = data;
    renderChips();
    sauvegarderBientot();
  } catch { /* non bloquant */ }
}

function renderParcelles() {
  const body = $("#ptable-body");
  if (!body) return;
  const parcelles = state.projet.localisation.parcelles;
  body.innerHTML = "";

  parcelles.forEach((p, idx) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td><b>${esc(p.section)}</b>${p.com_abs && p.com_abs !== "000" ? ` <small>(${esc(p.com_abs)})</small>` : ""}</td>
      <td>${esc(p.numero)}</td>
      <td class="num">${fmtM2(p.contenance_m2)}</td>
      <td><button class="iconbtn" title="Retirer" aria-label="Retirer la parcelle">✕</button></td>`;
    tr.querySelector(".iconbtn").addEventListener("click", () => {
      parcelles.splice(idx, 1);
      renderParcelles(); renderChips(); majCarte(); sauvegarderBientot();
    });
    body.appendChild(tr);
  });

  state.suggestions.forEach((s, idx) => {
    const tr = document.createElement("tr");
    tr.className = "suggest-row";
    tr.innerHTML = `<td>${esc(s.section)} <small style="color:var(--gv-violet)">proposée</small></td>
      <td>${esc(s.numero)}</td>
      <td class="num">${fmtM2(s.contenance_m2)}</td>
      <td><button>Ajouter</button></td>`;
    tr.querySelector("button").addEventListener("click", () => {
      state.projet.localisation.parcelles.push({ ...s, source: "suggestion" });
      state.suggestions.splice(idx, 1);
      renderParcelles(); renderChips(); majCarte(); sauvegarderBientot();
    });
    body.appendChild(tr);
  });

  if (!parcelles.length && !state.suggestions.length) {
    body.innerHTML = `<tr><td colspan="4" style="color:var(--faint)">Aucune parcelle. Recherchez une adresse ou saisissez section + numéro.</td></tr>`;
  }
}

async function ajouterParcelleManuelle() {
  const insee = state.projet.localisation.code_insee;
  if (!insee) { toast("Recherchez d'abord l'adresse (le code INSEE en dépend).", "err"); return; }
  const section = $("#add-section").value.trim();
  const numero = $("#add-numero").value.trim();
  if (!section || !numero) { toast("Renseignez section et numéro.", "err"); return; }
  const data = await api(
    `/api/parcelles/lookup?insee=${encodeURIComponent(insee)}&section=${encodeURIComponent(section)}&numero=${encodeURIComponent(numero)}`,
  );
  const dejaLa = new Set(state.projet.localisation.parcelles.map((p) => p.idu));
  for (const p of data.parcelles) {
    if (!dejaLa.has(p.idu)) {
      state.projet.localisation.parcelles.push({ ...p, source: "manuelle" });
    }
  }
  $("#add-section").value = ""; $("#add-numero").value = "";
  renderParcelles(); renderChips(); majCarte(); sauvegarderBientot();
}

function renderChips() {
  const box = $("#chips");
  if (!box) return;
  const loc = state.projet.localisation;
  const u = state.projet.urbanisme || {};
  const chips = [];

  chips.push(loc.code_insee
    ? `<span class="chip">INSEE <b>${esc(loc.code_insee)}</b></span>`
    : `<span class="chip off">INSEE —</span>`);

  const surf = loc.parcelles.reduce((t, p) => t + (p.contenance_m2 || 0), 0);
  chips.push(surf
    ? `<span class="chip">Surface terrain <b>${fmtM2(surf)}</b></span>`
    : `<span class="chip off">Surface —</span>`);

  if (u.zonage) {
    if (!u.zonage.disponible) chips.push(`<span class="chip off">PLU indisponible</span>`);
    else if (!u.zonage.couvert) {
      const rnu = u.rnu?.rnu ? " (RNU)" : "";
      chips.push(`<span class="chip warn">Hors couverture GPU${rnu}</span>`);
    } else {
      const z = u.zonage.zones[0];
      chips.push(`<span class="chip" title="${esc(z.libelong || "")}">Zonage PLU <b>${esc(z.libelle)}</b></span>`);
    }
  } else {
    chips.push(`<span class="chip off">Zonage PLU —</span>`);
  }

  if (u.servitudes) {
    if (!u.servitudes.disponible) chips.push(`<span class="chip off">ABF : donnée indisponible</span>`);
    else if (u.secteur_abf) {
      const libs = (u.servitudes.servitudes || []).map((s) => s.libelle).join(", ");
      chips.push(`<span class="chip warn" title="${esc(libs)}">Secteur ABF → PC</span>`);
    } else {
      chips.push(`<span class="chip ok">Hors secteur ABF</span>`);
    }
  }

  if (u.risques) {
    const n = (u.risques.risques || []).length;
    const libs = (u.risques.risques || []).map((r) => r.libelle).join(", ");
    chips.push(u.risques.disponible
      ? `<span class="chip ${n ? "" : "ok"}" title="${esc(libs)}">Risques recensés <b>${n}</b></span>`
      : `<span class="chip off">Géorisques indisponible</span>`);
  }

  box.innerHTML = chips.join("");
}

// ---------------- étape 2 : caractéristiques ----------------
function selectCoupe() {
  const val = state.projet.ombriere?.famille || "";
  const opts = COUPES.map((c) =>
    `<option value="${esc(c.val)}" ${val === c.val ? "selected" : ""}>${esc(c.lbl)}</option>`).join("");
  return `<div class="field"><label>Coupe</label>
    <select class="input" data-bind="ombriere.famille"><option value=""></option>${opts}</select></div>`;
}

function renderEtapeCaracteristiques(main) {
  main.innerHTML = `
    <div class="crumb">Étape 3 / 6</div>
    <h1>Caractéristiques de l'ombrière</h1>
    <p class="sub">La coupe choisie (Mono Bas / Mono Haut / Double) alimente la coupe DP3, les façades DP4 et le module Insertion IA.</p>
    <div class="formgrid">
      ${selectCoupe()}
      ${champ("Puissance (kWc)", "ombriere.puissance_kwc", { type: "number", step: "1" })}
      ${champ("Longueur (m)", "ombriere.longueur_m", { type: "number", step: "0.1" })}
      ${champ("Largeur (m)", "ombriere.largeur_m", { type: "number", step: "0.1" })}
      ${champ("Orientation (° azimut)", "ombriere.orientation", { type: "number", step: "1", placeholder: "0 = Nord, 90 = Est" })}
      ${champ("Nombre de travées", "ombriere.nb_travees", { type: "number", step: "1" })}
      ${champ("Entraxe (m)", "ombriere.entraxe_m", { type: "number", step: "0.1" })}
      ${champ("Pente (°)", "ombriere.pente_deg", { type: "number", step: "0.5" })}
      ${champ("Hauteur bas de pente (m)", "ombriere.garde_au_sol_m", { type: "number", step: "0.05" })}
      ${champ("Hauteur maximale (m)", "ombriere.hauteur_hors_tout_m", { type: "number", step: "0.05" })}
      ${champ("Puissance module (Wc)", "ombriere.module_puissance_wc", { type: "number", step: "5" })}
      ${champ("Dimensions module (mm)", "ombriere.module_dimensions", { placeholder: "Ex. : 1762 x 1134" })}
      ${champ("Nombre de places couvertes", "ombriere.nb_places", { type: "number", step: "1" })}
    </div>
    <p class="sub" style="margin-top:6px">La puissance pilote le régime : ≥ 3 000 kWc bascule le dossier en permis de construire.
      Astuce : mesurez Longueur / Largeur / Orientation sur le plan de masse à l'étape Pièces du BE.</p>

    <div class="actionsrow">
      <button class="btn" id="btn-apercus">Mettre à jour les aperçus DP3 / DP4</button>
      <button class="btn navy" id="btn-suivant">Continuer vers l'insertion IA</button>
    </div>
    <div class="grid" style="margin-top:18px" id="apercus" hidden>
      <div class="card"><div class="hd"><span class="ti-title">Aperçu DP3 · Coupe</span></div>
        <img id="img-dp3" class="planche-img" alt="Coupe DP3" /></div>
      <div class="card"><div class="hd"><span class="ti-title">Aperçu DP4 · Façades / toiture</span></div>
        <img id="img-dp4" class="planche-img" alt="Façades DP4" /></div>
    </div>`;
  brancherChamps(main);
  const chargerApercus = async () => {
    if (!state.projet.ombriere?.famille) { toast("Choisissez d'abord une coupe.", "err"); return; }
    await sauvegarder();
    $("#apercus").hidden = false;
    const t = Date.now();
    $("#img-dp3").src = `/api/projets/${state.projet.id}/planches/dp3_coupe.png?regen=1&t=${t}`;
    $("#img-dp4").src = `/api/projets/${state.projet.id}/planches/dp4_facades.png?regen=1&t=${t}`;
  };
  $("#btn-apercus").addEventListener("click", () => chargerApercus().catch(() => {}));
  $("#btn-suivant").addEventListener("click", async () => { await sauvegarder(); allerEtape(4); });
}

// ---- outil de mesure sur le plan de masse (calibrage + tracé) ----
const mesure = { mode: null, img: null, s: 1, pxPerM: null, seg: {}, pts: [], zoom: 1, pan: { x: 0, y: 0 }, drag: null };

function renderMesure() {
  const body = $("#mesure-body");
  if (!body) return;
  const dp2 = state.projet.documents?.dp2;
  if (!dp2) {
    body.innerHTML = `
      <div class="dropzone" id="mesure-drop">
        <input type="file" id="mesure-file" accept=".pdf,.png,.jpg,.jpeg" hidden />
        <b>Importe le plan de masse (DP2)</b><span>pour mesurer dessus (déposer ou cliquer)</span>
      </div>`;
    const drop = $("#mesure-drop"), inp = $("#mesure-file");
    drop.addEventListener("click", () => inp.click());
    inp.addEventListener("change", () => { if (inp.files.length) uploaderPlanMesure(inp.files[0]); });
    ["dragover"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("over"); }));
    ["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
    drop.addEventListener("drop", (e) => { const f = e.dataTransfer?.files?.[0]; if (f) uploaderPlanMesure(f); });
    return;
  }
  mesure.pxPerM = state.projet.ombriere?.echelle_plan_px_par_m || null;
  body.innerHTML = `
    <div class="mesure-tools">
      <button class="btn" data-mode="cal">1 · Calibrer l'échelle</button>
      <button class="btn" data-mode="len">2 · Tracer la longueur</button>
      <button class="btn" data-mode="wid">3 · Tracer la largeur</button>
      <span class="spacer" style="flex:1"></span>
      <button class="btn primary" id="mesure-appliquer">Appliquer aux champs</button>
    </div>
    <div class="mesure-status" id="mesure-status"></div>
    <div class="mesure-canvas-wrap"><canvas id="mesure-canvas"></canvas></div>
    <p class="sub" style="margin:8px 0 0">Molette = zoom · glisser = déplacer. Calibre l'échelle (2 points d'une distance connue, une place = 2,50 m…), puis trace la longueur puis la largeur de l'ombrière.</p>`;

  const canvas = $("#mesure-canvas");
  const img = new Image();
  img.onload = () => {
    const maxW = 760;
    mesure.s = Math.min(1, maxW / img.naturalWidth);
    canvas.width = Math.round(img.naturalWidth * mesure.s);
    canvas.height = Math.round(img.naturalHeight * mesure.s);
    mesure.img = img;
    mesure.zoom = 1; mesure.pan = { x: 0, y: 0 };
    dessinerMesure();
    majStatutMesure();
  };
  img.src = `/api/projets/${state.projet.id}/documents/dp2/image?t=${Date.now()}`;

  body.querySelectorAll("[data-mode]").forEach((b) => b.addEventListener("click", () => {
    mesure.mode = b.dataset.mode; mesure.pts = [];
    body.querySelectorAll("[data-mode]").forEach((x) => x.classList.toggle("primary", x === b));
    majStatutMesure();
  }));
  // clic = point de mesure · glisser = déplacer · molette = zoom
  canvas.addEventListener("mousedown", (e) => {
    mesure.drag = { x: e.clientX, y: e.clientY, px: mesure.pan.x, py: mesure.pan.y, moved: false };
  });
  canvas.addEventListener("mousemove", (e) => {
    if (!mesure.drag) return;
    const dx = e.clientX - mesure.drag.x, dy = e.clientY - mesure.drag.y;
    if (Math.abs(dx) + Math.abs(dy) > 4) mesure.drag.moved = true;
    if (mesure.drag.moved) { mesure.pan.x = mesure.drag.px + dx; mesure.pan.y = mesure.drag.py + dy; dessinerMesure(); }
  });
  const finDrag = (e) => {
    if (!mesure.drag) return;
    const moved = mesure.drag.moved; mesure.drag = null;
    if (!moved) clicMesure(e);  // clic simple = point de mesure
  };
  canvas.addEventListener("mouseup", finDrag);
  canvas.addEventListener("mouseleave", () => { mesure.drag = null; });
  canvas.addEventListener("wheel", (e) => {
    e.preventDefault();
    const r = canvas.getBoundingClientRect();
    const cx = (e.clientX - r.left) * (canvas.width / r.width);
    const cy = (e.clientY - r.top) * (canvas.height / r.height);
    const nz = Math.max(1, Math.min(8, mesure.zoom * (e.deltaY < 0 ? 1.15 : 1 / 1.15)));
    const ratio = nz / mesure.zoom;
    mesure.pan.x = cx - (cx - mesure.pan.x) * ratio;
    mesure.pan.y = cy - (cy - mesure.pan.y) * ratio;
    mesure.zoom = nz;
    if (mesure.zoom === 1) { mesure.pan.x = 0; mesure.pan.y = 0; }
    dessinerMesure();
  }, { passive: false });
  $("#mesure-appliquer").addEventListener("click", appliquerMesure);
}

async function uploaderPlanMesure(file) {
  const fd = new FormData();
  fd.append("fichier", file);
  let resp;
  try { resp = await fetch(`/api/projets/${state.projet.id}/documents/dp2`, { method: "POST", body: fd }); }
  catch { toast("Serveur injoignable.", "err"); return; }
  if (!resp.ok) { toast((await resp.json()).detail || "Échec.", "err"); return; }
  const data = await resp.json();
  state.projet = data.projet; state.evaluation = data.evaluation;
  toast("Plan de masse importé.", "ok");
  renderMesure();
}

function naturel(e) {
  const canvas = $("#mesure-canvas");
  const r = canvas.getBoundingClientRect();
  const cx = (e.clientX - r.left) * (canvas.width / r.width);
  const cy = (e.clientY - r.top) * (canvas.height / r.height);
  const k = mesure.s * mesure.zoom;
  return { x: (cx - mesure.pan.x) / k, y: (cy - mesure.pan.y) / k };
}

function distN(a, b) { return Math.hypot(b.x - a.x, b.y - a.y); }

function clicMesure(e) {
  if (!mesure.mode) { toast("Choisis d'abord un outil (calibrer / longueur / largeur).", ""); return; }
  mesure.pts.push(naturel(e));
  if (mesure.pts.length === 2) {
    const [a, b] = mesure.pts;
    if (mesure.mode === "cal") {
      const rep = window.prompt("Distance réelle entre les 2 points, en mètres :", "2.5");
      const m = parseFloat((rep || "").replace(",", "."));
      if (m > 0) {
        mesure.pxPerM = distN(a, b) / m;
        state.projet.ombriere.echelle_plan_px_par_m = mesure.pxPerM;
        mesure.seg.cal = [a, b];
        sauvegarderBientot();
      }
    } else if (mesure.mode === "len") {
      if (!mesure.pxPerM) { toast("Calibre d'abord l'échelle.", "err"); mesure.pts = []; return; }
      mesure.seg.len = [a, b];
      mesure.L = distN(a, b) / mesure.pxPerM;
      const dx = b.x - a.x, dy = b.y - a.y;
      mesure.orient = (Math.atan2(dx, -dy) * 180 / Math.PI + 360) % 360;
    } else if (mesure.mode === "wid") {
      if (!mesure.pxPerM) { toast("Calibre d'abord l'échelle.", "err"); mesure.pts = []; return; }
      mesure.seg.wid = [a, b];
      mesure.l = distN(a, b) / mesure.pxPerM;
    }
    mesure.pts = [];
    dessinerMesure(); majStatutMesure();
  } else {
    dessinerMesure();
  }
}

function dessinerMesure() {
  const canvas = $("#mesure-canvas");
  if (!canvas || !mesure.img) return;
  const ctx = canvas.getContext("2d");
  const k = mesure.s * mesure.zoom;
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.drawImage(mesure.img, mesure.pan.x, mesure.pan.y,
                mesure.img.naturalWidth * k, mesure.img.naturalHeight * k);
  const sx = (p) => mesure.pan.x + p.x * k, sy = (p) => mesure.pan.y + p.y * k;
  const seg = (pts, color) => {
    if (!pts) return;
    ctx.strokeStyle = color; ctx.fillStyle = color; ctx.lineWidth = 3;
    ctx.beginPath(); ctx.moveTo(sx(pts[0]), sy(pts[0])); ctx.lineTo(sx(pts[1]), sy(pts[1])); ctx.stroke();
    for (const p of pts) { ctx.beginPath(); ctx.arc(sx(p), sy(p), 5, 0, 7); ctx.fill(); }
  };
  seg(mesure.seg.cal, "#E53935");
  seg(mesure.seg.len, "#05DB79");
  seg(mesure.seg.wid, "#776DF8");
  if (mesure.pts.length === 1) {
    const p = mesure.pts[0];
    ctx.fillStyle = "#002455";
    ctx.beginPath(); ctx.arc(sx(p), sy(p), 5, 0, 7); ctx.fill();
  }
}

function majStatutMesure() {
  const el = $("#mesure-status");
  if (!el) return;
  const bits = [];
  bits.push(mesure.pxPerM ? `Échelle : <b>${mesure.pxPerM.toFixed(1)} px/m</b>` : `<span class="warn-txt">Échelle non calibrée</span>`);
  if (mesure.L) bits.push(`Longueur : <b>${mesure.L.toFixed(1)} m</b>`);
  if (mesure.l) bits.push(`Largeur : <b>${mesure.l.toFixed(1)} m</b>`);
  if (mesure.orient != null) bits.push(`Orientation : <b>${Math.round(mesure.orient)}°</b>`);
  const modeTxt = { cal: "cliquez 2 points d'une distance connue", len: "cliquez le long de la longueur", wid: "cliquez en travers de la largeur" }[mesure.mode];
  el.innerHTML = bits.join(" &nbsp;·&nbsp; ") + (modeTxt ? ` <span class="hint">— ${modeTxt}</span>` : "");
}

function appliquerMesure() {
  const o = state.projet.ombriere;
  let n = 0;
  const setChamp = (bind, val) => {
    const el = document.querySelector(`[data-bind="${bind}"]`);
    if (el) { el.value = val; }
  };
  if (mesure.L) { o.longueur_m = Math.round(mesure.L * 10) / 10; setChamp("ombriere.longueur_m", o.longueur_m); n++; }
  if (mesure.l) { o.largeur_m = Math.round(mesure.l * 10) / 10; setChamp("ombriere.largeur_m", o.largeur_m); n++; }
  if (mesure.orient != null) { o.orientation = Math.round(mesure.orient); setChamp("ombriere.orientation", o.orientation); n++; }
  if (!n) { toast("Trace d'abord la longueur / largeur.", "err"); return; }
  sauvegarderBientot();
  toast("Dimensions reportées (modifiables).", "ok");
}

// ---------------- étape 3 : pièces du BE (uploads, glisser-déposer) ----------------
const PIECES_UPLOAD = [
  { code: "dp2", titre: "DP2 · Plan de masse", note: "site-spécifique, fourni par le BE" },
  { code: "dp3", titre: "DP3 · Coupe (repli BE)", note: "facultatif : remplace la coupe paramétrique" },
  { code: "dp4", titre: "DP4 · Façades (repli BE)", note: "facultatif : remplace les façades paramétriques" },
  { code: "dp6", titre: "DP6 · Photomontage d'insertion", note: "pièce officielle, jamais générée par IA" },
  { code: "dp7", titre: "DP7 · Photo environnement proche", note: "photo datée et repérée" },
  { code: "dp8", titre: "DP8 · Photo paysage lointain", note: "photo datée et repérée" },
];

async function uploaderPieceBE(code, file) {
  const fd = new FormData();
  fd.append("fichier", file);
  let resp;
  try { resp = await fetch(`/api/projets/${state.projet.id}/documents/${code}`, { method: "POST", body: fd }); }
  catch { toast("Serveur injoignable.", "err"); return; }
  if (!resp.ok) { toast((await resp.json()).detail || "Échec de l'envoi.", "err"); return; }
  const data = await resp.json();
  state.projet = data.projet; state.evaluation = data.evaluation;
  toast("Pièce enregistrée.", "ok");
  render();
}

function renderEtapePieces(main) {
  main.innerHTML = `
    <div class="crumb">Étape 2 / 6</div>
    <h1>Pièces du bureau d'études</h1>
    <p class="sub">Glissez-déposez chaque pièce (PDF, PNG ou JPG, 40 Mo max). Un fichier par pièce ; le dernier envoi remplace le précédent.</p>
    <div class="home-list" id="slots"></div>

    <details class="foldable" id="fold-mesure" style="margin-top:18px">
      <summary>Mesurer Longueur / Largeur / Orientation sur le plan de masse <span class="hint">(optionnel, à l'échelle)</span></summary>
      <div class="bd" id="mesure-body"></div>
    </details>

    <h2 style="font-size:17px;color:var(--gv-navy);margin:24px 0 4px">Photos du site (pour l'insertion)</h2>
    <p class="sub" style="margin:0 0 10px">Plusieurs photos possibles. Elles serviront de base à l'insertion IA (étape 4). Repère d'échelle facultatif pour aider l'IA à dimensionner.</p>
    <div id="pieces-photos" class="sub" style="margin-bottom:10px"></div>
    <div class="scale-row" style="margin-bottom:6px">
      <label class="btn navy" style="cursor:pointer">+ ajouter des photos
        <input type="file" id="pieces-photos-add" accept=".png,.jpg,.jpeg,.webp" multiple hidden /></label>
      <input class="input" id="pieces-scale-desc" placeholder="repère d'échelle (ex. : largeur d'une place)" value="${esc((state.projet.insertion || {}).echelle_desc || "")}" />
      <input class="input" id="pieces-scale-dist" type="number" step="0.1" placeholder="m" value="${(state.projet.insertion || {}).echelle_distance_m ?? ""}" style="max-width:80px" />
    </div>

    <div class="actionsrow">
      <button class="btn navy" id="btn-suivant-pieces">Continuer vers les caractéristiques</button>
    </div>`;
  const box = $("#slots");
  $("#btn-suivant-pieces").addEventListener("click", () => allerEtape(3));
  renderMesure();
  renderPhotosInsertion("#pieces-photos");
  $("#pieces-photos-add").addEventListener("change", (e) => {
    if (e.target.files.length) uploaderPhotosSite(e.target.files, "#pieces-photos").catch(() => {});
  });
  const sauverEchellePieces = () => {
    api(`/api/projets/${state.projet.id}/insertion/consignes`, {
      method: "PUT",
      body: JSON.stringify({
        echelle_desc: $("#pieces-scale-desc").value,
        echelle_distance_m: $("#pieces-scale-dist").value || "",
      }),
    }).then((d) => { state.projet = d.projet; }).catch(() => {});
  };
  ["#pieces-scale-desc", "#pieces-scale-dist"].forEach((s) => {
    let t; $(s).addEventListener("input", () => { clearTimeout(t); t = setTimeout(sauverEchellePieces, 700); });
  });
  for (const piece of PIECES_UPLOAD) {
    const doc = state.projet.documents?.[piece.code];
    const div = document.createElement("div");
    div.className = "card upload-slot dropzone-slot";
    div.dataset.code = piece.code;
    div.innerHTML = `
      <div class="bd" style="display:flex;align-items:center;gap:14px">
        <span class="st ${doc ? "prete" : "en_attente"}" style="width:10px;height:10px;border-radius:50%;flex:none;background:${doc ? "var(--gv-green)" : "var(--gv-grey)"}"></span>
        <span style="flex:1"><b style="color:var(--gv-navy)">${esc(piece.titre)}</b>
          <small style="display:block;color:var(--muted)">${doc ? esc(doc.nom_fichier) + " · " + esc((doc.date || "").slice(0, 10)) : esc(piece.note) + " — glisser un fichier ici"}</small></span>
        ${doc ? `<a class="btn" href="/api/projets/${state.projet.id}/documents/${piece.code}" target="_blank">Voir</a>
                 <button class="btn" data-del="${piece.code}">Retirer</button>` : ""}
        <label class="btn navy" style="cursor:pointer">${doc ? "Remplacer" : "Téléverser"}
          <input type="file" accept=".pdf,.png,.jpg,.jpeg" data-code="${piece.code}" hidden /></label>
      </div>`;
    box.appendChild(div);
  }
  box.querySelectorAll("input[type=file]").forEach((inp) => {
    inp.addEventListener("change", () => { if (inp.files.length) uploaderPieceBE(inp.dataset.code, inp.files[0]); });
  });
  box.querySelectorAll(".dropzone-slot").forEach((slot) => {
    const code = slot.dataset.code;
    ["dragover", "dragenter"].forEach((ev) => slot.addEventListener(ev, (e) => { e.preventDefault(); slot.classList.add("over"); }));
    ["dragleave", "drop"].forEach((ev) => slot.addEventListener(ev, (e) => { e.preventDefault(); slot.classList.remove("over"); }));
    slot.addEventListener("drop", (e) => {
      const f = e.dataTransfer?.files?.[0];
      if (f) uploaderPieceBE(code, f);
    });
  });
  box.querySelectorAll("[data-del]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const data = await api(`/api/projets/${state.projet.id}/documents/${btn.dataset.del}`, { method: "DELETE" });
      state.projet = data.projet; state.evaluation = data.evaluation;
      render();
    });
  });
}

// ---------------- étape 5 : insertion IA (générateur de prompt, sans API) ----------------
function urlInsertion(chemin) {
  return `/api/projets/${state.projet.id}/insertion/fichier?chemin=${encodeURIComponent(chemin)}&t=${Date.now()}`;
}

async function copierPressePapier(txt) {
  try {
    await navigator.clipboard.writeText(txt);
    return true;
  } catch {
    const ta = document.createElement("textarea");
    ta.value = txt;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    let ok = false;
    try { ok = document.execCommand("copy"); } catch { ok = false; }
    ta.remove();
    return ok;
  }
}

function resumeOmbriere() {
  const o = state.projet.ombriere || {};
  if (!o.famille) return "Type d'ombrière non renseigné (étape 3) — le prompt restera générique.";
  const bits = [o.famille];
  if (o.puissance_kwc) bits.push(`${o.puissance_kwc} kWc`);
  if (o.nb_places) bits.push(`${o.nb_places} places`);
  if (o.hauteur_hors_tout_m) bits.push(`h. ${o.hauteur_hors_tout_m} m`);
  return bits.join(" · ");
}

async function renderEtapeInsertion(main) {
  main.innerHTML = `
    <div class="crumb">Étape 4 / 6</div>
    <h1>Insertion IA</h1>
    <p class="sub">Génère un visuel d'insertion photoréaliste (Gemini) depuis une photo du site, le plan de masse et la coupe.
      Visuel « visuel IA » réservé au commercial, jamais utilisé en pièce DP6.</p>
    <div id="ia-statut" class="placeholder">Chargement…</div>`;
  let s;
  try { s = await api(`/api/projets/${state.projet.id}/insertion/statut`); }
  catch { return; }
  if (!s.api_configuree) {
    $("#ia-statut").innerHTML = `<b>Clé Gemini absente</b>
      Ajoute <code>GEMINI_API_KEY</code> dans le fichier .env puis relance GV_DP pour activer la génération d'insertion.`;
    return;
  }
  renderInsertionAtelier(s);
}

function renderInsertionAtelier(s) {
  const ins = state.projet.insertion || {};
  const main = $("#main");
  main.querySelector("#ia-statut").outerHTML = `
    <div class="grid">
      <div class="card">
        <div class="hd"><span class="ti-title">1 · Photo & consignes</span>
          <label class="link" style="cursor:pointer">+ ajouter des photos
            <input type="file" id="ins-photos-add" accept=".png,.jpg,.jpeg,.webp" multiple hidden /></label></div>
        <div class="bd">
          <div id="ins-photos" class="sub">Chargement des photos…</div>
          <div class="field" style="margin-top:14px"><label>Prompt complémentaire (facultatif)</label>
            <textarea class="input notice-ta" id="ins-consignes" rows="3"
              placeholder="Ex. : 3 rangées depuis la façade, garder le mât d'éclairage, pas de bleu au sol">${esc(ins.consignes || "")}</textarea></div>
          <div class="note" style="margin-top:4px">Ombrière : <b>${esc(resumeOmbriere())}</b></div>
          <button class="btn primary" id="ins-generer-api" style="width:100%;margin-top:14px">Générer l'insertion</button>
          <div class="hint" style="margin-top:6px;text-align:center">Gemini ${esc(s.api_modele)} · photo + plan de masse + coupe · ≈ 0,04 €/image</div>
          <div id="ins-progress" class="sub" style="margin-top:6px;text-align:center;min-height:18px"></div>
        </div>
      </div>

      <div class="card">
        <div class="hd"><span class="ti-title">2 · Insertions générées</span></div>
        <div class="bd">
          <div class="gallery" id="ins-galerie"></div>
          <div class="actionsrow" style="margin-top:12px">
            <button class="btn navy" id="ins-fiche" ${ins.retenue ? "" : "disabled"}>Fiche de validation d'emprise (PPTX)</button>
            <span id="ins-fiche-lien" class="sub"></span>
          </div>
        </div>
      </div>
    </div>`;

  $("#ins-photos-add").addEventListener("change", (e) => {
    if (e.target.files.length) uploaderPhotosSite(e.target.files).catch(() => {});
  });
  let tc;
  $("#ins-consignes").addEventListener("input", () => {
    clearTimeout(tc);
    tc = setTimeout(() => {
      api(`/api/projets/${state.projet.id}/insertion/consignes`, {
        method: "PUT", body: JSON.stringify({ consignes: $("#ins-consignes").value }),
      }).then((d) => { state.projet = d.projet; }).catch(() => {});
    }, 700);
  });
  $("#ins-generer-api").addEventListener("click", () => genererInsertionAPI().catch(() => {}));
  $("#ins-fiche").addEventListener("click", () => genererFiche().catch(() => {}));

  renderPhotosInsertion();
  renderGalerie();
}

// sélecteur de photos du site (multi) : active = base de génération
async function renderPhotosInsertion(sel = "#ins-photos") {
  const box = $(sel);
  if (!box) return;
  let data;
  try { data = await api(`/api/projets/${state.projet.id}/insertion/photos-disponibles`); }
  catch { return; }
  let html = "";
  if (data.photos.length) {
    html += `<div class="hint" style="margin-bottom:6px">Photo de génération (clique pour choisir) :</div>
      <div class="photo-choices">${data.photos.map((p) => `
        <div class="photo-choice ${p.chemin === data.active ? "actif" : ""}" data-active="${esc(p.chemin)}">
          <img src="${esc(p.url)}&t=${Date.now()}" alt="photo site" />
          <button class="photo-suppr" data-suppr="${esc(p.chemin)}" title="Retirer">✕</button>
        </div>`).join("")}</div>`;
  } else {
    html += `<div class="placeholder" style="padding:18px"><b>Aucune photo du site</b>Ajoute-les ici ou à l'étape Pièces du BE.</div>`;
  }
  if (data.reutilisables.length) {
    html += `<div class="hint" style="margin:12px 0 6px">Ou reprends une photo des Pièces BE :</div>
      <div class="photo-choices">${data.reutilisables.map((p) => `
        <button class="photo-choice petite" data-piece="${esc(p.code)}" title="${esc(p.libelle)}">
          <img src="${esc(p.url)}?t=${Date.now()}" alt="${esc(p.libelle)}" />
          <span>${esc(p.libelle)}</span>
        </button>`).join("")}</div>`;
  }
  box.innerHTML = html;
  box.querySelectorAll("[data-active]").forEach((el) => el.addEventListener("click", async (e) => {
    if (e.target.closest("[data-suppr]")) return;
    const d = await api(`/api/projets/${state.projet.id}/insertion/photo-active`, {
      method: "PUT", body: JSON.stringify({ chemin: el.dataset.active }),
    });
    state.projet = d.projet; renderPhotosInsertion(sel);
  }));
  box.querySelectorAll("[data-suppr]").forEach((b) => b.addEventListener("click", async (e) => {
    e.stopPropagation();
    const d = await api(`/api/projets/${state.projet.id}/insertion/photo?chemin=${encodeURIComponent(b.dataset.suppr)}`,
      { method: "DELETE" });
    state.projet = d.projet; renderPhotosInsertion(sel);
  }));
  box.querySelectorAll("[data-piece]").forEach((b) => b.addEventListener("click", async () => {
    const d = await api(`/api/projets/${state.projet.id}/insertion/photo-piece`, {
      method: "PUT", body: JSON.stringify({ code: b.dataset.piece }),
    });
    state.projet = d.projet; toast("Photo reprise des pièces BE.", "ok"); renderPhotosInsertion(sel);
  }));
}

async function uploaderPhotosSite(files, sel = "#ins-photos") {
  const fd = new FormData();
  for (const f of files) fd.append("fichiers", f);
  let resp;
  try { resp = await fetch(`/api/projets/${state.projet.id}/insertion/photos`, { method: "POST", body: fd }); }
  catch { toast("Serveur injoignable.", "err"); return; }
  if (!resp.ok) { toast((await resp.json()).detail || "Échec.", "err"); return; }
  state.projet = (await resp.json()).projet;
  toast("Photo(s) ajoutée(s).", "ok");
  renderPhotosInsertion(sel);
}

// génération directe via l'API Gemini (Nano Banana) — 1 image par clic
async function genererInsertionAPI() {
  const ins = state.projet.insertion || {};
  if (!ins.photo) { toast("Ajoute d'abord une photo du site (ou reprends une pièce BE).", "err"); return; }
  const btn = $("#ins-generer-api");
  const prog = $("#ins-progress");
  if (btn) btn.disabled = true;
  if (prog) prog.textContent = "Génération en cours (10 à 30 s)…";
  try {
    const data = await api(`/api/projets/${state.projet.id}/insertion/generer`, {
      method: "POST", body: JSON.stringify({}),
    });
    state.projet = data.projet;
    if (prog) prog.textContent = "Image générée — regarde la galerie ci-dessous.";
    toast("Insertion générée.", "ok");
    renderGalerie();
    const fiche = $("#ins-fiche"); if (fiche) fiche.disabled = !state.projet.insertion?.retenue;
  } catch { if (prog) prog.textContent = ""; }
  finally { if (btn) btn.disabled = false; }
}

// réutiliser une photo déjà importée dans les Pièces BE (DP7/DP8/DP6)
async function chargerPhotosDispo() {
  const box = $("#ins-photo-choices");
  if (!box) return;
  let data;
  try { data = await api(`/api/projets/${state.projet.id}/insertion/photos-disponibles`); }
  catch { return; }
  if (!data.photos.length) { box.innerHTML = ""; return; }
  box.innerHTML = `<div class="hint" style="margin-bottom:6px">Ou réutilise une photo déjà importée dans les Pièces BE :</div>
    <div class="photo-choices">${data.photos.map((p) => `
      <button class="photo-choice" data-code="${esc(p.code)}" title="${esc(p.libelle)}">
        <img src="${esc(p.url)}?t=${Date.now()}" alt="${esc(p.libelle)}" />
        <span>${esc(p.libelle)}</span>
      </button>`).join("")}</div>`;
  box.querySelectorAll(".photo-choice").forEach((b) => b.addEventListener("click", async () => {
    try {
      const d = await api(`/api/projets/${state.projet.id}/insertion/photo-piece`, {
        method: "PUT", body: JSON.stringify({ code: b.dataset.code }),
      });
      state.projet = d.projet;
      toast("Photo reprise depuis les pièces BE.", "ok");
      renderEtapeInsertion($("#main"));
    } catch { /* toast déjà affiché */ }
  }));
}

// repère d'échelle sur la photo d'insertion (2 points, aide visuelle)
const insScale = { img: null, pts: [], mode: false };

function initPhotoScale(ins) {
  const canvas = $("#ins-photo-canvas");
  if (!canvas) return;
  const img = new Image();
  img.onload = () => {
    const maxW = 520;
    const s = Math.min(1, maxW / img.naturalWidth);
    canvas.width = Math.round(img.naturalWidth * s);
    canvas.height = Math.round(img.naturalHeight * s);
    insScale.img = img; insScale.pts = [];
    dessinerPhotoScale();
  };
  img.src = urlInsertion(ins.photo);
  $("#ins-scale-btn")?.addEventListener("click", () => {
    insScale.mode = true; insScale.pts = []; dessinerPhotoScale();
    toast("Clique 2 points sur la photo (ex. les deux bords d'une place).", "");
  });
  canvas.addEventListener("click", (e) => {
    if (!insScale.mode) return;
    const r = canvas.getBoundingClientRect();
    insScale.pts.push({ x: (e.clientX - r.left) * (canvas.width / r.width),
                        y: (e.clientY - r.top) * (canvas.height / r.height) });
    if (insScale.pts.length >= 2) {
      insScale.mode = false; insScale.pts = insScale.pts.slice(0, 2);
      toast("Points placés — saisis la distance réelle et ce qu'ils relient.", "ok");
      $("#ins-scale-dist")?.focus();
    }
    dessinerPhotoScale();
  });
}

function dessinerPhotoScale() {
  const canvas = $("#ins-photo-canvas");
  if (!canvas || !insScale.img) return;
  const ctx = canvas.getContext("2d");
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.drawImage(insScale.img, 0, 0, canvas.width, canvas.height);
  if (insScale.pts.length === 2) {
    ctx.strokeStyle = "#E53935"; ctx.lineWidth = 3;
    ctx.beginPath(); ctx.moveTo(insScale.pts[0].x, insScale.pts[0].y);
    ctx.lineTo(insScale.pts[1].x, insScale.pts[1].y); ctx.stroke();
  }
  ctx.fillStyle = "#E53935";
  for (const p of insScale.pts) { ctx.beginPath(); ctx.arc(p.x, p.y, 5, 0, 7); ctx.fill(); }
}

async function uploaderPhotoSite(input) {
  if (!input.files.length) return;
  const fd = new FormData();
  fd.append("fichier", input.files[0]);
  let resp;
  try { resp = await fetch(`/api/projets/${state.projet.id}/insertion/photo`, { method: "POST", body: fd }); }
  catch { toast("Serveur injoignable.", "err"); return; }
  if (!resp.ok) { toast((await resp.json()).detail || "Échec.", "err"); return; }
  state.projet = (await resp.json()).projet;
  toast("Photo enregistrée.", "ok");
  renderEtapeInsertion($("#main"));
}

async function genererPrompt(affinage = "") {
  const btn = $("#ins-generer");
  if (btn) btn.disabled = true;
  try {
    const data = await api(`/api/projets/${state.projet.id}/insertion/prompt`, {
      method: "POST", body: JSON.stringify({ affinage }),
    });
    state.projet = data.projet;
    const copie = await copierPressePapier(data.prompt);
    toast(copie ? "Prompt copié dans le presse-papier." : "Prompt généré (copie manuelle).", copie ? "ok" : "");
    renderSortiePrompt(data.prompt, MODE_EMPLOI_INS, data.kit);
  } catch { /* toast déjà affiché */ }
  finally { if (btn) btn.disabled = false; }
}

const MODE_EMPLOI_INS = [
  "Ouvre ChatGPT (un modèle avec génération d'image).",
  "Colle le prompt (déjà copié).",
  "Joins les images du kit ci-dessous.",
  "Génère, affine si besoin, puis glisse l'image retenue dans la zone de dépôt.",
];

function renderSortiePrompt(prompt, modeEmploi, kit) {
  const box = $("#ins-sortie");
  if (!box) return;
  const etapes = (modeEmploi || MODE_EMPLOI_INS).map((l, i) => `<li>${esc(l)}</li>`).join("");
  box.innerHTML = `
    <div class="chip ok" style="margin-bottom:10px">Prompt prêt · copié dans le presse-papier</div>
    <details class="prompt-box"><summary>Voir / copier le prompt</summary>
      <pre id="ins-prompt-txt">${esc(prompt)}</pre>
      <button class="btn" id="ins-copier">Copier à nouveau</button>
    </details>
    <ol class="mode-emploi">${etapes}</ol>
    <div class="kit-head">
      <span>Images à joindre dans ChatGPT</span>
      <button class="btn navy" id="ins-kit-zip">Télécharger le kit (ZIP)</button>
    </div>
    <p class="sub" style="margin:0 0 8px">Le presse-papier ne transporte pas les pièces jointes : télécharge le kit puis glisse ces 3 images dans ChatGPT.</p>
    <div class="kit-grid" id="ins-kit"></div>
    <div class="field" style="margin-top:14px"><label>Affiner le prompt (corrections)</label>
      <textarea class="input notice-ta" id="ins-affinage" rows="2"
        placeholder="Ex. : allonge la rangée jusqu'au mât, assombris les modules">${esc(state.projet.insertion?.affinage || "")}</textarea>
      <button class="btn navy" id="ins-affiner" style="margin-top:8px">Régénérer le prompt affiné</button></div>`;

  $("#ins-copier").addEventListener("click", async () => {
    const ok = await copierPressePapier($("#ins-prompt-txt").textContent);
    toast(ok ? "Copié." : "Copie impossible.", ok ? "ok" : "err");
  });
  $("#ins-kit-zip").addEventListener("click", () =>
    telechargerFichier(`/api/projets/${state.projet.id}/insertion/kit.zip`, "kit_insertion.zip"));
  $("#ins-affiner").addEventListener("click", () => genererPrompt($("#ins-affinage").value).catch(() => {}));
  renderKit(kit);
}

async function renderKit(kit) {
  const box = $("#ins-kit");
  if (!box) return;
  // si le kit n'est pas fourni (réaffichage), on le recharge
  if (!kit) {
    try { kit = (await api(`/api/projets/${state.projet.id}/insertion/prompt`, {
      method: "POST", body: JSON.stringify({ affinage: state.projet.insertion?.affinage || "" }),
    })).kit; } catch { return; }
  }
  box.innerHTML = "";
  for (const it of kit) {
    const div = document.createElement("div");
    div.className = "kit-item" + (it.disponible ? "" : " off");
    const t = Date.now();
    const src = it.disponible ? `/api/projets/${state.projet.id}/insertion/kit/${it.role}?t=${t}` : "";
    div.innerHTML = `
      ${it.disponible
        ? `<a href="${src}" download="${esc(it.role)}.png" title="Télécharger"><img src="${src}" alt="${esc(it.titre)}" /></a>`
        : `<div class="kit-manquant">manquant</div>`}
      <div class="kit-lbl"><b>${esc(it.titre)}</b><small>${it.disponible ? esc(it.note) : (it.requis ? "à ajouter (obligatoire)" : "optionnel — " + esc(it.note))}</small></div>`;
    box.appendChild(div);
  }
}

async function importerImage(file) {
  const fd = new FormData();
  fd.append("fichier", file);
  let resp;
  try { resp = await fetch(`/api/projets/${state.projet.id}/insertion/import`, { method: "POST", body: fd }); }
  catch { toast("Serveur injoignable.", "err"); return; }
  if (!resp.ok) { toast((await resp.json()).detail || "Échec de l'import.", "err"); return; }
  state.projet = (await resp.json()).projet;
  toast("Image importée.", "ok");
  renderGalerie();
  const fiche = $("#ins-fiche");
  if (fiche) fiche.disabled = !state.projet.insertion?.retenue;
}

function renderGalerie() {
  const box = $("#ins-galerie");
  if (!box) return;
  const ins = state.projet.insertion || {};
  const images = ins.images || [];
  if (!images.length) {
    box.innerHTML = `<p class="sub">Aucune image importée pour l'instant.</p>`;
    return;
  }
  box.innerHTML = "";
  for (const v of images) {
    const retenue = ins.retenue === v.fichier;
    const div = document.createElement("div");
    div.className = "card" + (retenue ? " retenue" : "");
    div.innerHTML = `
      <img class="planche-img" src="${urlInsertion(v.fichier)}" alt="Insertion IA" />
      <div class="bd" style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
        <span class="chip ${retenue ? "ok" : ""}" style="font-size:10px">${esc(v.etiquette || "visuel IA")}</span>
        <span style="flex:1"></span>
        <a class="btn" href="${urlInsertion(v.fichier)}" download target="_blank">Télécharger</a>
        <button class="btn" data-suppr="${esc(v.fichier)}">Retirer</button>
        <button class="btn ${retenue ? "primary" : "navy"}" data-retenue="${esc(v.fichier)}">${retenue ? "Retenue ✓" : "Retenir"}</button>
      </div>`;
    box.appendChild(div);
  }
  box.querySelectorAll("[data-retenue]").forEach((b) => b.addEventListener("click", async () => {
    const d = await api(`/api/projets/${state.projet.id}/insertion/retenue`, {
      method: "PUT", body: JSON.stringify({ fichier: b.dataset.retenue }),
    });
    state.projet = d.projet; renderGalerie();
    const fiche = $("#ins-fiche"); if (fiche) fiche.disabled = false;
  }));
  box.querySelectorAll("[data-suppr]").forEach((b) => b.addEventListener("click", async () => {
    const d = await api(`/api/projets/${state.projet.id}/insertion/image?fichier=${encodeURIComponent(b.dataset.suppr)}`,
      { method: "DELETE" });
    state.projet = d.projet; renderGalerie();
    const fiche = $("#ins-fiche"); if (fiche) fiche.disabled = !state.projet.insertion?.retenue;
  }));
}

async function genererFiche() {
  const lien = $("#ins-fiche-lien");
  if (lien) lien.textContent = "Génération de la fiche…";
  try {
    const data = await api(`/api/projets/${state.projet.id}/insertion/fiche`, { method: "POST" });
    if (lien) lien.innerHTML = `Fiche prête : <a href="${esc(data.telechargement)}">${esc(data.fichier)}</a>`;
    toast("Fiche d'emprise générée.", "ok");
  } catch { if (lien) lien.textContent = ""; }
}

// ---------------- étape 6 : notice descriptive ----------------
const NOTICE_SECTIONS = [
  ["presentation", "1. Présentation du projet et du demandeur"],
  ["etat_initial", "2. État initial du terrain et de ses abords"],
  ["description", "3. Description du projet"],
  ["insertion", "4. Insertion paysagère et aspect extérieur"],
  ["reglementaire", "5. Contexte réglementaire, servitudes et risques"],
  ["acces_reseaux", "6. Accès, réseaux et raccordement"],
  ["chantier", "7. Chantier et remise en état"],
];

let noticeVars = [];

// surligne dans un texte les valeurs concrètes du projet (relecture)
function surligner(texte, vars) {
  let s = String(texte || "");
  const map = [];
  for (const v of [...new Set(vars)].filter(Boolean)) {
    if (!s.includes(v)) continue;
    const tok = "⁣HL" + map.length + "⁣";
    s = s.split(v).join(tok);
    map.push(v);
  }
  let html = esc(s);
  map.forEach((v, i) => {
    html = html.split("⁣HL" + i + "⁣").join(`<mark class="v" title="donnée du projet">${esc(v)}</mark>`);
  });
  return html;
}

function renderEtapeNotice(main) {
  const n = state.projet.notice || { sections: {} };
  main.innerHTML = `
    <div class="crumb">Étape 5 / 6</div>
    <h1>Notice + Cerfa</h1>
    <p class="sub">Notice type pré-remplie depuis la saisie et les données officielles (PLU, Géorisques).
      Les <mark class="v">informations du projet</mark> sont surlignées ; relisez, ajustez, puis validez.</p>
    <div class="actionsrow" style="margin:0 0 16px">
      <button class="btn navy" id="btn-gen-notice">Générer / remplir la notice</button>
      <button class="btn" id="btn-regen-notice" title="Écrase les 7 sections avec la notice type remplie">Tout regénérer</button>
      <label class="chip ${n.valide_humain ? "ok" : ""}" style="cursor:pointer;padding:8px 14px">
        <input type="checkbox" id="chk-valide" ${n.valide_humain ? "checked" : ""} style="margin-right:6px" />
        Notice relue et validée
      </label>
    </div>
    <div class="home-list" id="notice-sections"></div>

    <h2 style="font-size:17px;color:var(--gv-navy);margin:26px 0 4px">Informations pour le Cerfa</h2>
    <p class="sub" style="margin:0 0 12px">Le déclarant (maître d'ouvrage) et son contact, nécessaires au pré-remplissage du Cerfa. Le reste (adresse, parcelles, puissance) est repris des étapes précédentes.</p>
    <div class="formgrid" style="max-width:820px">
      ${champ("Type de maître d'ouvrage", "mo.type", { select: ["Société", "Collectivité", "Particulier"] })}
      ${champ("Raison sociale / nom", "mo.raison_sociale")}
      ${champ("Représentant", "mo.representant")}
      ${champ("SIRET", "mo.siret", { placeholder: "14 chiffres" })}
      ${champ("Adresse du maître d'ouvrage", "mo.adresse", { wide: true })}
      ${champ("Courriel", "mo.email", { type: "email", placeholder: "suivi du dossier" })}
      ${champ("Téléphone", "mo.telephone")}
    </div>`;

  brancherChamps(main);  // champs maître d'ouvrage (Cerfa)
  const generer = async (force) => {
    const data = await api(`/api/projets/${state.projet.id}/notice/generer?force=${force}`, { method: "POST" });
    state.projet = data.projet; state.evaluation = data.evaluation;
    noticeVars = data.variables || noticeVars;
    toast("Notice remplie.", "ok");
    render();
  };
  $("#btn-gen-notice").addEventListener("click", () => generer(0).catch(() => {}));
  $("#btn-regen-notice").addEventListener("click", () => generer(1).catch(() => {}));
  $("#chk-valide").addEventListener("change", (e) => {
    state.projet.notice.valide_humain = e.target.checked;
    sauvegarder(false).catch(() => {});
  });

  renderNoticeSections();
  api(`/api/projets/${state.projet.id}/notice/variables`)
    .then((d) => { noticeVars = d.variables || []; renderNoticeSections(); })
    .catch(() => {});
}

function renderNoticeSections() {
  const box = $("#notice-sections");
  if (!box) return;
  const n = state.projet.notice || { sections: {} };
  box.innerHTML = "";
  for (const [cle, titre] of NOTICE_SECTIONS) {
    const txt = n.sections?.[cle] || "";
    const div = document.createElement("div");
    div.className = "card notice-sec";
    div.innerHTML = `
      <div class="hd"><span class="ti-title">${esc(titre)}</span>
        <span class="link" data-edit="${cle}">${txt ? "Modifier" : ""}</span></div>
      <div class="bd">
        <div class="notice-preview" data-prev="${cle}">${txt
          ? surligner(txt, noticeVars)
          : '<span class="sub">Section vide — cliquez « Générer / remplir la notice ».</span>'}</div>
        <textarea class="input notice-ta" data-cle="${cle}" rows="7" hidden>${esc(txt)}</textarea>
      </div>`;
    box.appendChild(div);
  }
  box.querySelectorAll("[data-edit]").forEach((btn) =>
    btn.addEventListener("click", () => basculerEditionNotice(btn.dataset.edit)));
  box.querySelectorAll("textarea").forEach((ta) => {
    ta.addEventListener("input", () => {
      state.projet.notice.sections[ta.dataset.cle] = ta.value;
      state.projet.notice.valide_humain = false;
      const chk = $("#chk-valide"); if (chk) chk.checked = false;
      sauvegarderBientot();
    });
  });
}

function basculerEditionNotice(cle) {
  const prev = document.querySelector(`[data-prev="${cle}"]`);
  const ta = document.querySelector(`textarea[data-cle="${cle}"]`);
  const btn = document.querySelector(`[data-edit="${cle}"]`);
  if (!prev || !ta || !ta.value) return;
  if (ta.hidden) {
    ta.hidden = false; prev.hidden = true; btn.textContent = "Aperçu"; ta.focus();
  } else {
    ta.hidden = true; prev.hidden = false;
    prev.innerHTML = surligner(ta.value, noticeVars); btn.textContent = "Modifier";
  }
}

// ---------------- étape 7 : aperçu & export ----------------
const PLANCHES_APERCU = [
  ["dp1_situation", "DP1 · Situation"],
  ["dp1_cadastral", "DP1 · Cadastre"],
  ["dp1_aerien", "DP1 · Vue aérienne"],
  ["dp3_coupe", "DP3 · Coupe"],
  ["dp4_facades", "DP4 · Façades"],
];

function renderEtapeExport(main) {
  main.innerHTML = `
    <div class="crumb">Étape 6 / 6</div>
    <h1>Aperçu & export</h1>
    <p class="sub">Vérifiez les planches, pré-remplissez le Cerfa (le maître d'ouvrage se saisit à l'étape Notice + Cerfa) puis assemblez le dossier (export PDF pour le dépôt).</p>

    <div class="actionsrow" style="margin:14px 0 8px">
      <button class="btn" id="btn-cerfa">Pré-remplir le Cerfa 16702</button>
      <button class="btn navy" id="btn-pptx">Assembler le dossier (PPTX)</button>
      <button class="btn primary" id="btn-pdf">Exporter en PDF</button>
    </div>
    <div id="export-liens" class="sub" style="min-height:20px"></div>
    <div class="gallery" id="gallery">
      ${PLANCHES_APERCU.map(([code, titre]) => `
        <div class="card"><div class="hd"><span class="ti-title">${esc(titre)}</span>
          <span class="link" data-regen="${code}">régénérer</span></div>
          <img class="planche-img" id="pl-${code}" alt="${esc(titre)}" /></div>`).join("")}
    </div>`;

  brancherChamps(main);  // champs maître d'ouvrage
  const chargerImg = (code, regen) => {
    const img = $(`#pl-${code}`);
    img.src = `/api/projets/${state.projet.id}/planches/${code}.png?regen=${regen ? 1 : 0}&t=${Date.now()}`;
    img.onerror = () => { img.alt = "Planche indisponible (complétez la localisation et les caractéristiques)"; img.style.opacity = 0.25; };
  };
  PLANCHES_APERCU.forEach(([code]) => chargerImg(code, false));
  main.querySelectorAll("[data-regen]").forEach((el) =>
    el.addEventListener("click", () => chargerImg(el.dataset.regen, true)));

  const liens = $("#export-liens");
  $("#btn-cerfa").addEventListener("click", async () => {
    const data = await api(`/api/projets/${state.projet.id}/cerfa`, { method: "POST" });
    state.projet = data.projet; state.evaluation = data.evaluation;
    renderChrome();
    liens.innerHTML = `Cerfa ${esc(data.cerfa)} pré-rempli (${data.champs_remplis} champs) —
      <a href="/api/projets/${state.projet.id}/cerfa.pdf" target="_blank">ouvrir le PDF</a> (brouillon à relire).`;
    toast("Cerfa pré-rempli.", "ok");
  });
  $("#btn-pptx").addEventListener("click", async () => {
    liens.textContent = "Assemblage en cours (génération des planches)…";
    const data = await api(`/api/projets/${state.projet.id}/dossier`, { method: "POST" });
    telechargerFichier(data.telechargement, data.fichier);
    liens.innerHTML = `Dossier téléchargé : <b>${esc(data.fichier)}</b> (dossier Téléchargements) — `
      + `<a href="${esc(data.telechargement)}" download>relancer</a>`
      + (data.avertissements.length ? `<br />Avertissements : ${esc(data.avertissements.join(" ; "))}` : "");
    toast("Dossier PPTX téléchargé.", "ok");
  });
  $("#btn-pdf").addEventListener("click", async () => {
    liens.textContent = "Export PDF en cours…";
    const data = await api(`/api/projets/${state.projet.id}/dossier/pdf`, { method: "POST" });
    telechargerFichier(data.telechargement, data.fichier);
    liens.innerHTML = `PDF téléchargé : <b>${esc(data.fichier)}</b> (dossier Téléchargements) — `
      + `<a href="${esc(data.telechargement)}" download>relancer</a>`;
    toast("PDF téléchargé.", "ok");
  });
}

// déclenche le téléchargement d'un fichier (atterrit dans le dossier Téléchargements)
function telechargerFichier(url, nom) {
  const a = document.createElement("a");
  a.href = url;
  a.download = nom || "";
  document.body.appendChild(a);
  a.click();
  a.remove();
}

// ---------------- init ----------------
$("#btn-save").addEventListener("click", () => sauvegarder(false).catch(() => {}));
$("#btn-accueil").addEventListener("click", () => { state.etape = 0; render(); });
$("#btn-generer").addEventListener("click", () => allerEtape(6));

render();
