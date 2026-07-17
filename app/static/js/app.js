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
  { n: 2, titre: "Pièces du BE", sous: "Masse, coupe, DP6" },
  { n: 3, titre: "Caractéristiques", sous: "Ombrière, coupe" },
  { n: 4, titre: "Insertion IA", sous: "Visuels du projet" },
  { n: 5, titre: "Notice + Cerfa", sous: "Notice, maître d'ouvrage" },
  { n: 6, titre: "Aperçu & export", sous: "PPTX, PDF, Cerfa" },
];

// Libellés « Coupe » présentés à l'utilisateur ; la valeur reste la clé catalogue.

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
  // accueil : sidebar et panneau complétude masqués (CSS .accueil)
  document.body.classList.toggle("accueil", state.etape === 0);

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
      const c = pr.completude || { pretes: 0, total: 1 };
      const pct = Math.round((c.pretes / c.total) * 100);
      const div = document.createElement("div");
      div.className = "home-item";
      div.innerHTML = `
        <span class="t">${esc(pr.nom)}<small>modifié ${esc((pr.date_modification || "").slice(0, 10) || "—")}</small></span>
        <span class="home-comp">
          <span class="home-bar"><i style="width:${pct}%"></i></span>
          <small>${c.pretes}/${c.total} pièces</small>
        </span>
        <button class="iconbtn home-del" title="Supprimer le projet" aria-label="Supprimer le projet">✕</button>`;
      div.addEventListener("click", (e) => {
        if (e.target.closest(".home-del")) return;
        ouvrirProjet(pr.id);
      });
      div.querySelector(".home-del").addEventListener("click", async () => {
        if (!window.confirm(`Supprimer définitivement « ${pr.nom} » ?`)) return;
        await api(`/api/projets/${pr.id}`, { method: "DELETE" });
        toast("Projet supprimé.", "ok");
        renderAccueil(main);
      });
      box.appendChild(div);
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
  state.etape = 1;
  render();
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
        <div class="hd"><span class="ti-title">Repérage</span></div>
        <div id="map"></div>
      </div>
      <div class="card">
        <div class="hd"><span class="ti-title">Parcelles concernées</span></div>
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
// (la coupe du projet = pièce DP3 déposée par le BE, plus de coupe type)
function renderEtapeCaracteristiques(main) {
  const lecture = state.projet.meta?.plan_lecture;
  const proposes = state.projet.meta?.plan_champs_proposes || [];
  main.innerHTML = `
    <div class="crumb">Étape 3 / 6</div>
    <h1>Caractéristiques de l'ombrière</h1>
    ${lecture ? `<div class="note plan-note">Lu sur le plan de masse${lecture.reference_plan ? ` (${esc(lecture.reference_plan)})` : ""} :
      ${esc(resumeLecturePlan(lecture))} <span class="link" id="btn-plan-appliquer">Tout appliquer</span></div>` : ""}
    <div class="formgrid">
      ${champ("Puissance (kWc)", "ombriere.puissance_kwc", { type: "number", step: "1" })}
      ${champ("Orientation (° azimut)", "ombriere.orientation", { type: "number", step: "1", placeholder: "0 = Nord, 90 = Est" })}
      ${champ("Entraxe (m)", "ombriere.entraxe_m", { type: "number", step: "0.1" })}
      ${champ("Pente (°)", "ombriere.pente_deg", { type: "number", step: "0.5" })}
      ${champ("Hauteur bas de pente (m)", "ombriere.garde_au_sol_m", { type: "number", step: "0.05" })}
      ${champ("Hauteur maximale (m)", "ombriere.hauteur_hors_tout_m", { type: "number", step: "0.05" })}
      ${champ("Puissance module (Wc)", "ombriere.module_puissance_wc", { type: "number", step: "5" })}
      ${champ("Dimensions module (mm)", "ombriere.module_dimensions", { placeholder: "Ex. : 1762 x 1134" })}
      ${champ("Nombre de places couvertes", "ombriere.nb_places", { type: "number", step: "1" })}
    </div>

    <div class="actionsrow">
      <button class="btn navy" id="btn-suivant">Continuer vers l'insertion IA</button>
    </div>`;
  brancherChamps(main);
  marquerChampsProposes(main, proposes);
  $("#btn-plan-appliquer")?.addEventListener("click", () => appliquerLecturePlan(lecture));
  $("#btn-suivant").addEventListener("click", async () => { await sauvegarder(); allerEtape(4); });
}

// résumé lisible de la lecture du plan (bannière étape 3)
function resumeLecturePlan(lecture) {
  const bits = [];
  if (lecture.puissance_kwc) bits.push(`${lecture.puissance_kwc} kWc`);
  if (lecture.module_puissance_wc) bits.push(`modules ${lecture.module_puissance_wc} Wc`);
  if (lecture.module_dimensions) bits.push(lecture.module_dimensions);
  if (lecture.pente_deg) bits.push(`pente ${lecture.pente_deg}°`);
  if (lecture.garde_au_sol_m) bits.push(`+${lecture.garde_au_sol_m} m`);
  if (lecture.hauteur_hors_tout_m) bits.push(`+${lecture.hauteur_hors_tout_m} m`);
  if (lecture.echelle_plan) bits.push(`échelle ${lecture.echelle_plan}`);
  return bits.join(" · ") || "aucun champ reconnu";
}

// surligne les champs pré-remplis depuis le plan (jusqu'à modification)
function marquerChampsProposes(main, proposes) {
  for (const champ of proposes) {
    const el = main.querySelector(`[data-bind="ombriere.${champ}"]`);
    if (!el) continue;
    el.classList.add("propose");
    el.addEventListener("input", () => el.classList.remove("propose"), { once: true });
  }
}

// bouton « Tout appliquer » : reporte toutes les valeurs lues (écrase la saisie)
function appliquerLecturePlan(lecture) {
  if (!lecture) return;
  const CHAMPS = ["puissance_kwc", "module_puissance_wc", "module_dimensions",
                  "pente_deg", "garde_au_sol_m", "hauteur_hors_tout_m"];
  let n = 0;
  for (const champ of CHAMPS) {
    if (lecture[champ] == null) continue;
    state.projet.ombriere[champ] = lecture[champ];
    const el = document.querySelector(`[data-bind="ombriere.${champ}"]`);
    if (el) { el.value = lecture[champ]; el.classList.add("propose"); }
    n++;
  }
  if (n) { sauvegarderBientot(); toast(`${n} champs appliqués depuis le plan.`, "ok"); }
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
      <button class="btn" data-mode="cal">Calibrer l'échelle</button>
    </div>
    <div class="mesure-status" id="mesure-status"></div>
    <div class="mesure-canvas-wrap"><canvas id="mesure-canvas"></canvas></div>
    <p class="sub" style="margin:8px 0 0">Molette = zoom · glisser = déplacer · 2 points d'une distance connue (une place = 2,50 m).</p>`;

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
  if (!mesure.mode) { toast("Clique d'abord « Calibrer l'échelle ».", ""); return; }
  mesure.pts.push(naturel(e));
  if (mesure.pts.length === 2) {
    const [a, b] = mesure.pts;
    const rep = window.prompt("Distance réelle entre les 2 points, en mètres :", "2.5");
    const m = parseFloat((rep || "").replace(",", "."));
    if (m > 0) {
      mesure.pxPerM = distN(a, b) / m;
      state.projet.ombriere.echelle_plan_px_par_m = mesure.pxPerM;
      mesure.seg.cal = [a, b];
      sauvegarderBientot();
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
  if (mesure.mode === "cal") bits.push(`<span class="hint">cliquez 2 points d'une distance connue</span>`);
  el.innerHTML = bits.join(" &nbsp;·&nbsp; ");
}

// ---------------- étape 3 : pièces du BE (uploads, glisser-déposer) ----------------
const PIECES_UPLOAD = [
  { code: "dp2", titre: "DP2 · Plan de masse", note: "" },
  { code: "dp3", titre: "DP3 · Coupe du projet", note: "sert aussi de référence structure à l'insertion IA" },
  { code: "dp6", titre: "DP6 · Photomontage d'insertion", note: "" },
  { code: "dp7", titre: "DP7 · Photo environnement proche", note: "" },
  { code: "dp8", titre: "DP8 · Photo paysage lointain", note: "" },
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
  if (data.plan_champs_proposes?.length) {
    toast(`Plan lu : ${data.plan_champs_proposes.length} champs pré-remplis à l'étape 3.`, "ok");
  } else {
    toast("Pièce enregistrée.", "ok");
  }
  render();
}

function renderEtapePieces(main) {
  main.innerHTML = `
    <div class="crumb">Étape 2 / 6</div>
    <h1>Pièces du bureau d'études</h1>
    <div class="home-list" id="slots"></div>

    <details class="foldable" id="fold-mesure" style="margin-top:18px">
      <summary>Calibrer l'échelle sur le plan de masse</summary>
      <div class="bd" id="mesure-body"></div>
    </details>

    <h2 style="font-size:17px;color:var(--gv-navy);margin:24px 0 4px">Photos du site (pour l'insertion)</h2>
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
          <small style="display:block;color:var(--muted)">${doc ? esc(doc.nom_fichier) + " · " + esc((doc.date || "").slice(0, 10)) : esc(piece.note || "glisser un fichier ici")}</small></span>
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

// ---------------- étape 4 : insertion IA (génération Gemini) ----------------
function urlInsertion(chemin) {
  return `/api/projets/${state.projet.id}/insertion/fichier?chemin=${encodeURIComponent(chemin)}&t=${Date.now()}`;
}

// agrandissement plein écran d'une image (clic pour fermer)
function ouvrirLightbox(src) {
  $("#lightbox-img").src = src;
  $("#lightbox").hidden = false;
}

function resumeOmbriere() {
  const o = state.projet.ombriere || {};
  const bits = [];
  if (o.puissance_kwc) bits.push(`${o.puissance_kwc} kWc`);
  if (o.garde_au_sol_m && o.hauteur_hors_tout_m) bits.push(`${o.garde_au_sol_m} → ${o.hauteur_hors_tout_m} m`);
  if (o.pente_deg) bits.push(`pente ${o.pente_deg}°`);
  return bits.join(" · ") || "caractéristiques à saisir (étape 3)";
}

async function renderEtapeInsertion(main) {
  main.innerHTML = `
    <div class="crumb">Étape 4 / 6</div>
    <h1>Insertion IA</h1>
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

// aperçu éditable du prompt : chargé depuis l'auto, modifiable, envoyé tel quel
async function chargerApercuPrompt(force = false) {
  const ta = $("#ins-prompt");
  if (!ta) return;
  if (state.promptEdite && !force) { majEtatPrompt(); return; }
  try {
    const d = await api(`/api/projets/${state.projet.id}/insertion/apercu-prompt`);
    ta.value = d.prompt || "";
    state.promptEdite = false;
    majEtatPrompt();
  } catch { /* toast déjà affiché */ }
}

function majEtatPrompt() {
  const el = $("#ins-prompt-etat");
  if (el) el.textContent = state.promptEdite
    ? "prompt modifié — c'est cette version qui sera envoyée"
    : "prompt automatique (issu du plan, de la coupe et des saisies)";
}

function texteDepense(imagesProjet, imagesGlobal, cout) {
  const eur = (n) => (n * (cout || 0)).toLocaleString("fr-FR", { style: "currency", currency: "EUR" });
  return `${imagesProjet} image${imagesProjet > 1 ? "s" : ""} (projet, ≈ ${eur(imagesProjet)}) · ${imagesGlobal} au total (≈ ${eur(imagesGlobal)})`;
}

function renderInsertionAtelier(s) {
  const ins = state.projet.insertion || {};
  state.coutImage = s.cout_image_eur || 0;
  const main = $("#main");
  main.querySelector("#ia-statut").outerHTML = `
    <div class="card">
      <div class="hd"><span class="ti-title">1 · Photo du site</span>
        <label class="link" style="cursor:pointer">+ ajouter des photos
          <input type="file" id="ins-photos-add" accept=".png,.jpg,.jpeg,.webp" multiple hidden /></label></div>
      <div class="bd">
        <div id="ins-alerte-photo"></div>
        <div id="ins-photos" class="sub">Chargement des photos…</div>
      </div>
    </div>

    <div class="card" style="margin-top:16px">
      <div class="hd"><span class="ti-title">2 · Caler l'ombrière</span>
        <span class="hint">l'emprise magenta est la contrainte de placement envoyée à Gemini</span></div>
      <div class="bd">
        <div class="calage-grid">
          <div>
            <div class="calage-titre">Vue aérienne (du plan) <span class="hint" id="aer-etat"></span></div>
            <div id="aer-body"><p class="sub">Chargement…</p></div>
          </div>
          <div>
            <div class="calage-titre">Sur la photo <span class="hint" id="ins-guides-etat"></span></div>
            <div id="ins-guides-body"></div>
          </div>
        </div>
      </div>
    </div>

    <div class="card" style="margin-top:16px">
      <div class="hd"><span class="ti-title">3 · Générer</span>
        <span class="hint">Ombrière : ${esc(resumeOmbriere())}</span></div>
      <div class="bd">
        <div class="calage-titre">Images envoyées à Gemini</div>
        <div class="payload-strip" id="ins-payload"><p class="sub">Chargement…</p></div>
        <div class="field" style="margin-top:12px"><label>Consignes complémentaires (facultatif)</label>
          <textarea class="input notice-ta" id="ins-consignes" rows="2"
            placeholder="Ex. : garder le mât d'éclairage">${esc(ins.consignes || "")}</textarea></div>
        <details class="foldable" id="ins-prompt-fold" style="margin-top:10px"><summary>Prompt envoyé <span class="hint">(modifiable)</span></summary>
          <div class="bd">
            <textarea class="input prompt-ta" id="ins-prompt" rows="12" spellcheck="false"></textarea>
            <div class="actionsrow" style="margin-top:8px">
              <button class="btn" id="ins-prompt-auto" title="Reconstruire le prompt depuis les données du projet">↺ Prompt auto</button>
              <span class="hint" id="ins-prompt-etat"></span>
            </div>
          </div>
        </details>
        <button class="btn primary" id="ins-generer-api" style="width:100%;margin-top:12px">Générer l'insertion</button>
        <div id="ins-progress" class="sub" style="margin-top:6px;text-align:center;min-height:18px"></div>
        <div class="hint" id="ins-depense" style="margin-top:2px;text-align:center">${esc(texteDepense(s.images_projet || 0, s.images_global || 0, s.cout_image_eur))}</div>
      </div>
    </div>

    <div class="card" style="margin-top:16px">
      <div class="hd"><span class="ti-title">Insertions générées</span>
        <span class="hint">cochées = incluses au dossier DP</span></div>
      <div class="bd">
        <div class="gallery" id="ins-galerie"></div>
        <div class="actionsrow" style="margin-top:12px">
          <button class="btn navy" id="ins-fiche" ${ins.retenue ? "" : "disabled"}>Fiche de validation d'emprise (PPTX)</button>
          <span id="ins-fiche-lien" class="sub"></span>
        </div>
      </div>
    </div>`;

  $("#ins-photos-add").addEventListener("change", (e) => {
    if (e.target.files.length) uploaderPhotosSite(e.target.files).catch(() => {});
  });
  chargerApercuPrompt();
  $("#ins-prompt").addEventListener("input", () => { state.promptEdite = true; majEtatPrompt(); });
  $("#ins-prompt-auto").addEventListener("click", () => chargerApercuPrompt(true));
  let tc;
  $("#ins-consignes").addEventListener("input", () => {
    clearTimeout(tc);
    tc = setTimeout(() => {
      api(`/api/projets/${state.projet.id}/insertion/consignes`, {
        method: "PUT", body: JSON.stringify({ consignes: $("#ins-consignes").value }),
      }).then((d) => { state.projet = d.projet; rafraichirPayloadEtPrompt(); }).catch(() => {});
    }, 700);
  });
  $("#ins-generer-api").addEventListener("click", () => genererInsertionAPI().catch(() => {}));
  $("#ins-fiche").addEventListener("click", () => genererFiche().catch(() => {}));

  renderPhotosInsertion();
  renderGalerie();
}

// ---- bandeau du payload : les images EXACTES qui partiront à Gemini ----
let payloadTimer = null;
function rafraichirPayloadEtPrompt() {
  clearTimeout(payloadTimer);
  payloadTimer = setTimeout(() => {
    renderPayload();
    if (!state.promptEdite) chargerApercuPrompt();
  }, 600);
}

async function renderPayload() {
  const box = $("#ins-payload");
  if (!box) return;
  let d;
  try { d = await api(`/api/projets/${state.projet.id}/insertion/apercu-payload`); }
  catch { return; }
  if (!d.images.length) {
    box.innerHTML = `<p class="sub">Ajoute une photo du site pour préparer l'envoi.</p>`;
    return;
  }
  box.innerHTML = d.images.map((im) => `
    <figure class="payload-item" title="${esc(im.titre)}">
      <img src="${esc(im.url)}&t=${Date.now()}" alt="${esc(im.titre)}" data-zoom="${esc(im.url)}" />
      <figcaption>${esc(im.titre)}</figcaption>
    </figure>`).join("");
  box.querySelectorAll("[data-zoom]").forEach((img) =>
    img.addEventListener("click", () => ouvrirLightbox(img.dataset.zoom)));
}

// ---- alerte si la photo active est une pièce BE réutilisée ----
function renderAlertePhoto() {
  const box = $("#ins-alerte-photo");
  if (!box) return;
  const photo = state.projet.insertion?.photo || "";
  const piece = (photo.match(/\/uploads\/(dp[678])\./) || [])[1];
  box.innerHTML = piece
    ? `<div class="note warn" style="margin-bottom:10px">La photo active provient de la pièce ${piece.toUpperCase()}.
       Une photo dédiée du site (bien cadrée, zone dégagée) donne de meilleurs résultats.</div>`
    : "";
}

// ---- vue aérienne : emprise auto extraite du plan, coins ajustables ----
const aerUI = { img: null, data: null, drag: null };

async function renderAerienne() {
  const body = $("#aer-body");
  if (!body) return;
  let d;
  try { d = await api(`/api/projets/${state.projet.id}/insertion/aerienne`); }
  catch { return; }
  const etat = $("#aer-etat");
  if (!d.disponible) {
    body.innerHTML = `<p class="sub">Dépose le plan de masse (DP2, étape 2) pour extraire l'emprise automatiquement.</p>`;
    if (etat) etat.textContent = "";
    return;
  }
  aerUI.data = d;
  if (etat) etat.textContent = d.auto ? "(auto, extraite du plan)" : "(ajustée à la main)";
  body.innerHTML = `
    <div class="mesure-canvas-wrap"><canvas id="aer-canvas"></canvas></div>
    <div class="actionsrow" style="margin-top:8px">
      <button class="btn" id="aer-reset" ${d.auto ? "disabled" : ""}>↺ Emprise auto</button>
      <span class="hint">glisse les coins magenta pour ajuster</span>
    </div>`;
  const canvas = $("#aer-canvas");
  const img = new Image();
  img.onload = () => {
    const maxW = 640;
    const sc = Math.min(1, maxW / img.naturalWidth);
    canvas.width = Math.round(img.naturalWidth * sc);
    canvas.height = Math.round(img.naturalHeight * sc);
    aerUI.img = img;
    dessinerAerienne();
  };
  img.src = `${d.fond}&t=${Date.now()}`;

  const pos = (e) => {
    const r = canvas.getBoundingClientRect();
    return [(e.clientX - r.left) / r.width, (e.clientY - r.top) / r.height];
  };
  canvas.addEventListener("mousedown", (e) => {
    const [x, y] = pos(e);
    let plusProche = null;
    aerUI.data.emprises.forEach((emp, ei) => emp.forEach((p, pi) => {
      const dist = Math.hypot((p[0] - x) * canvas.width, (p[1] - y) * canvas.height);
      if (dist < 14 && (!plusProche || dist < plusProche.dist)) plusProche = { ei, pi, dist };
    }));
    aerUI.drag = plusProche;
  });
  canvas.addEventListener("mousemove", (e) => {
    if (!aerUI.drag) return;
    const [x, y] = pos(e);
    aerUI.data.emprises[aerUI.drag.ei][aerUI.drag.pi] =
      [Math.min(1, Math.max(0, x)), Math.min(1, Math.max(0, y))];
    dessinerAerienne();
  });
  const finDrag = () => {
    if (!aerUI.drag) return;
    aerUI.drag = null;
    api(`/api/projets/${state.projet.id}/insertion/aerienne`, {
      method: "PUT", body: JSON.stringify({ emprises: aerUI.data.emprises }),
    }).then((d2) => {
      state.projet = d2.projet;
      aerUI.data.auto = false;
      const b = $("#aer-reset"); if (b) b.disabled = false;
      const et = $("#aer-etat"); if (et) et.textContent = "(ajustée à la main)";
      rafraichirPayloadEtPrompt();
    }).catch(() => {});
  };
  canvas.addEventListener("mouseup", finDrag);
  canvas.addEventListener("mouseleave", finDrag);
  $("#aer-reset").addEventListener("click", async () => {
    const d2 = await api(`/api/projets/${state.projet.id}/insertion/aerienne`, { method: "DELETE" });
    state.projet = d2.projet;
    renderAerienne();
    rafraichirPayloadEtPrompt();
  });
}

function dessinerAerienne() {
  const canvas = $("#aer-canvas");
  if (!canvas || !aerUI.img || !aerUI.data) return;
  const dr = canvas.getContext("2d");
  dr.clearRect(0, 0, canvas.width, canvas.height);
  dr.drawImage(aerUI.img, 0, 0, canvas.width, canvas.height);
  const X = (p) => p[0] * canvas.width, Y = (p) => p[1] * canvas.height;
  for (const emp of aerUI.data.emprises) {
    dr.beginPath();
    emp.forEach((p, i) => (i ? dr.lineTo(X(p), Y(p)) : dr.moveTo(X(p), Y(p))));
    dr.closePath();
    dr.strokeStyle = "#FF00C8"; dr.lineWidth = 3; dr.stroke();
    for (const p of emp) {
      dr.beginPath(); dr.arc(X(p), Y(p), 6, 0, 7); dr.fillStyle = "#FF00C8"; dr.fill();
      dr.lineWidth = 2; dr.strokeStyle = "#fff"; dr.stroke();
    }
  }
  const f = aerUI.data.fleche;
  if (f) {
    const a = [X(f.a), Y(f.a)], b = [X(f.b), Y(f.b)];
    const v = [b[0] - a[0], b[1] - a[1]];
    const n = Math.hypot(...v) || 1;
    const u = [v[0] / n, v[1] / n], p = [-u[1], u[0]];
    dr.strokeStyle = "#fff"; dr.lineWidth = 6;
    dr.beginPath(); dr.moveTo(...a); dr.lineTo(...b); dr.stroke();
    dr.strokeStyle = "#14141e"; dr.lineWidth = 3;
    dr.beginPath(); dr.moveTo(...a); dr.lineTo(...b);
    for (const s of [1, -1]) {
      dr.moveTo(...b);
      dr.lineTo(b[0] - 14 * u[0] + s * 8 * p[0], b[1] - 14 * u[1] + s * 8 * p[1]);
    }
    dr.stroke();
  }
}

// ---- guides d'implantation tracés sur la photo active (envoyés à Gemini) ----
const guidesUI = { img: null, mode: null, pts: [], scale: 1 };

function guidesPhotoActive() {
  const ins = state.projet.insertion || {};
  if (!ins.photo) return null;
  const data = (ins.guides || {})[ins.photo] || {};
  return {
    photo: ins.photo,
    emprises: (data.emprises || []).map((e) => e.map((p) => [...p])),
    calibrage: data.calibrage ? JSON.parse(JSON.stringify(data.calibrage)) : null,
  };
}

let guidesSaveTimer = null;
function sauverGuides(ctx) {
  clearTimeout(guidesSaveTimer);
  guidesSaveTimer = setTimeout(() => {
    api(`/api/projets/${state.projet.id}/insertion/guides`, {
      method: "PUT",
      body: JSON.stringify({ photo: ctx.photo, emprises: ctx.emprises, calibrage: ctx.calibrage }),
    }).then((d) => { state.projet = d.projet; majEtatGuides(); rafraichirPayloadEtPrompt(); }).catch(() => {});
  }, 500);
}

function majEtatGuides() {
  const el = $("#ins-guides-etat");
  if (!el) return;
  const ctx = guidesPhotoActive();
  const bouts = [];
  if (ctx?.emprises?.length) bouts.push(`${ctx.emprises.length} emprise${ctx.emprises.length > 1 ? "s" : ""}`);
  if (ctx?.calibrage) bouts.push(`échelle ${ctx.calibrage.distance_m} m`);
  el.textContent = bouts.length ? `(${bouts.join(" · ")})` : "(aucun guide — Gemini placera seul)";
}

function renderGuides() {
  const body = $("#ins-guides-body");
  if (!body) return;
  majEtatGuides();
  const ctx = guidesPhotoActive();
  if (!ctx) {
    body.innerHTML = `<p class="sub" style="padding:10px 14px">Choisis d'abord une photo du site.</p>`;
    return;
  }
  guidesUI.mode = null;
  guidesUI.pts = [];
  body.innerHTML = `
    <div class="mesure-tools">
      <button class="btn" data-gmode="emprise">+ Emprise (4 coins au sol)</button>
      <button class="btn" data-gmode="cal">Échelle (2 points)</button>
      <input class="input" id="g-dist" type="number" step="0.1" placeholder="m" style="max-width:74px"
        value="${ctx.calibrage?.distance_m ?? "2.5"}" />
      <input class="input" id="g-lib" placeholder="repère (ex. : largeur d'une place)" style="max-width:230px"
        value="${esc(ctx.calibrage?.libelle || "largeur d'une place")}" />
      <span style="flex:1"></span>
      <button class="btn" id="g-annuler">Annuler</button>
      <button class="btn" id="g-effacer">Tout effacer</button>
    </div>
    <div class="mesure-status" id="g-statut"></div>
    <div class="mesure-canvas-wrap"><canvas id="g-canvas"></canvas></div>`;

  const canvas = $("#g-canvas");
  const img = new Image();
  img.onload = () => {
    guidesUI.scale = Math.min(1, 760 / img.naturalWidth);
    canvas.width = Math.round(img.naturalWidth * guidesUI.scale);
    canvas.height = Math.round(img.naturalHeight * guidesUI.scale);
    guidesUI.img = img;
    dessinerGuides(ctx);
    majStatutGuides();
  };
  img.src = urlInsertion(ctx.photo);

  body.querySelectorAll("[data-gmode]").forEach((b) => b.addEventListener("click", () => {
    guidesUI.mode = b.dataset.gmode;
    guidesUI.pts = [];
    body.querySelectorAll("[data-gmode]").forEach((x) => x.classList.toggle("primary", x === b));
    majStatutGuides();
  }));
  canvas.addEventListener("click", (e) => {
    if (!guidesUI.mode) { toast("Choisis d'abord Emprise ou Échelle.", ""); return; }
    const r = canvas.getBoundingClientRect();
    guidesUI.pts.push([
      Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)),
      Math.min(1, Math.max(0, (e.clientY - r.top) / r.height)),
    ]);
    if (guidesUI.mode === "emprise" && guidesUI.pts.length === 4) {
      ctx.emprises.push(guidesUI.pts);
      guidesUI.pts = [];
      sauverGuides(ctx);
      toast("Emprise ajoutée.", "ok");
    } else if (guidesUI.mode === "cal" && guidesUI.pts.length === 2) {
      const dist = parseFloat(($("#g-dist").value || "").replace(",", "."));
      if (!(dist > 0)) { toast("Renseigne la distance réelle (m).", "err"); guidesUI.pts = []; return; }
      ctx.calibrage = { a: guidesUI.pts[0], b: guidesUI.pts[1], distance_m: dist,
                        libelle: $("#g-lib").value.trim() };
      guidesUI.pts = [];
      sauverGuides(ctx);
      toast("Repère d'échelle posé.", "ok");
    }
    dessinerGuides(ctx);
    majStatutGuides();
  });
  $("#g-annuler").addEventListener("click", () => {
    if (guidesUI.pts.length) guidesUI.pts.pop();
    else if (ctx.emprises.length) { ctx.emprises.pop(); sauverGuides(ctx); }
    else if (ctx.calibrage) { ctx.calibrage = null; sauverGuides(ctx); }
    dessinerGuides(ctx); majStatutGuides();
  });
  $("#g-effacer").addEventListener("click", () => {
    ctx.emprises = []; ctx.calibrage = null; guidesUI.pts = [];
    sauverGuides(ctx); dessinerGuides(ctx); majStatutGuides();
  });
  ["#g-dist", "#g-lib"].forEach((s) => $(s).addEventListener("input", () => {
    if (!ctx.calibrage) return;
    const dist = parseFloat(($("#g-dist").value || "").replace(",", "."));
    if (dist > 0) ctx.calibrage.distance_m = dist;
    ctx.calibrage.libelle = $("#g-lib").value.trim();
    sauverGuides(ctx);
  }));
}

function dessinerGuides(ctx) {
  const canvas = $("#g-canvas");
  if (!canvas || !guidesUI.img) return;
  const dr = canvas.getContext("2d");
  dr.clearRect(0, 0, canvas.width, canvas.height);
  dr.drawImage(guidesUI.img, 0, 0, canvas.width, canvas.height);
  const X = (p) => p[0] * canvas.width, Y = (p) => p[1] * canvas.height;
  for (const emprise of ctx.emprises) {
    dr.beginPath();
    emprise.forEach((p, i) => (i ? dr.lineTo(X(p), Y(p)) : dr.moveTo(X(p), Y(p))));
    dr.closePath();
    dr.strokeStyle = "#FF00C8"; dr.lineWidth = 3; dr.stroke();
    for (const p of emprise) {
      dr.beginPath(); dr.arc(X(p), Y(p), 5, 0, 7); dr.fillStyle = "#FF00C8"; dr.fill();
    }
  }
  if (ctx.calibrage) {
    const { a, b } = ctx.calibrage;
    dr.strokeStyle = "#FFC800"; dr.lineWidth = 3;
    dr.beginPath(); dr.moveTo(X(a), Y(a)); dr.lineTo(X(b), Y(b)); dr.stroke();
    for (const p of [a, b]) {
      dr.beginPath(); dr.arc(X(p), Y(p), 5, 0, 7); dr.fillStyle = "#FFC800"; dr.fill();
    }
  }
  dr.fillStyle = "#002455";
  for (const p of guidesUI.pts) { dr.beginPath(); dr.arc(X(p), Y(p), 5, 0, 7); dr.fill(); }
}

function majStatutGuides() {
  const el = $("#g-statut");
  if (!el) return;
  const restant = guidesUI.mode === "emprise" ? 4 - guidesUI.pts.length
    : guidesUI.mode === "cal" ? 2 - guidesUI.pts.length : 0;
  const consigne = { emprise: `clique les 4 coins de l'emprise AU SOL (${restant} restant${restant > 1 ? "s" : ""})`,
                     cal: `clique les 2 extrémités d'une distance connue (${restant} restant${restant > 1 ? "s" : ""})` }[guidesUI.mode];
  el.innerHTML = consigne ? `<span class="hint">${consigne}</span>`
    : `<span class="hint">Vert = emprise des rangées · jaune = repère d'échelle. Envoyés à Gemini comme contrainte de placement.</span>`;
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
  if (sel === "#ins-photos") {  // étape 4 : tout ce qui dépend de la photo active
    renderGuides();
    renderAerienne();
    renderAlertePhoto();
    rafraichirPayloadEtPrompt();
  }
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
    const promptEdite = state.promptEdite ? ($("#ins-prompt")?.value || "") : "";
    const data = await api(`/api/projets/${state.projet.id}/insertion/generer`, {
      method: "POST", body: JSON.stringify(promptEdite ? { prompt: promptEdite } : {}),
    });
    state.projet = data.projet;
    if (prog) prog.textContent = "Image générée — regarde la galerie ci-contre.";
    const dep = $("#ins-depense");
    if (dep && data.images_projet != null) {
      dep.textContent = texteDepense(data.images_projet, data.images_global || 0, state.coutImage || 0);
    }
    toast("Insertion générée.", "ok");
    renderGalerie();
    const fiche = $("#ins-fiche"); if (fiche) fiche.disabled = !state.projet.insertion?.retenue;
  } catch { if (prog) prog.textContent = ""; }
  finally { if (btn) btn.disabled = false; }
}

function renderGalerie() {
  const box = $("#ins-galerie");
  if (!box) return;
  const ins = state.projet.insertion || {};
  const images = ins.images || [];
  if (!images.length) {
    box.innerHTML = `<p class="sub">Aucune insertion pour l'instant.</p>`;
    return;
  }
  box.innerHTML = "";
  const dansDossier = new Set(ins.dans_dossier || []);
  for (const v of images) {
    const retenue = ins.retenue === v.fichier;
    const incluse = dansDossier.has(v.fichier);
    const div = document.createElement("div");
    div.className = "card" + (retenue ? " retenue" : "");
    div.innerHTML = `
      <img class="planche-img zoomable" src="${urlInsertion(v.fichier)}" alt="Insertion IA" title="Cliquer pour agrandir" />
      <div class="bd" style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
        <label class="chip ${incluse ? "ok" : ""}" style="cursor:pointer" title="Inclure cette insertion au dossier DP exporté">
          <input type="checkbox" data-dossier="${esc(v.fichier)}" ${incluse ? "checked" : ""} style="margin-right:5px" />Dossier DP
        </label>
        <span style="flex:1"></span>
        <a class="btn" href="/api/projets/${state.projet.id}/insertion/image.jpg?chemin=${encodeURIComponent(v.fichier)}" download>JPEG</a>
        <button class="btn" data-suppr="${esc(v.fichier)}">Retirer</button>
        <button class="btn ${retenue ? "primary" : "navy"}" data-retenue="${esc(v.fichier)}" title="Image de la fiche de validation d'emprise">${retenue ? "Retenue ✓" : "Retenir"}</button>
      </div>`;
    div.querySelector(".zoomable").addEventListener("click", () => ouvrirLightbox(urlInsertion(v.fichier)));
    box.appendChild(div);
  }
  box.querySelectorAll("[data-dossier]").forEach((chk) => chk.addEventListener("change", async () => {
    const d = await api(`/api/projets/${state.projet.id}/insertion/dossier`, {
      method: "PUT", body: JSON.stringify({ fichier: chk.dataset.dossier, inclure: chk.checked }),
    });
    state.projet = d.projet; renderGalerie();
  }));
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
    <div class="actionsrow" style="margin:0 0 16px">
      <button class="btn navy" id="btn-gen-notice">Générer / remplir la notice</button>
      <button class="btn" id="btn-regen-notice" title="Écrase les 7 sections avec la notice type remplie">Tout regénérer</button>
      <label class="chip ${n.valide_humain ? "ok" : ""}" style="cursor:pointer;padding:8px 14px">
        <input type="checkbox" id="chk-valide" ${n.valide_humain ? "checked" : ""} style="margin-right:6px" />
        Notice relue et validée
      </label>
    </div>
    <div class="home-list" id="notice-sections"></div>

    <h2 style="font-size:17px;color:var(--gv-navy);margin:26px 0 12px">Informations pour le Cerfa</h2>
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
];

function renderEtapeExport(main) {
  main.innerHTML = `
    <div class="crumb">Étape 6 / 6</div>
    <h1>Aperçu & export</h1>

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
$("#btn-accueil").addEventListener("click", () => { state.etape = 0; render(); });
$("#lightbox").addEventListener("click", () => { $("#lightbox").hidden = true; });
document.addEventListener("keydown", (e) => { if (e.key === "Escape") $("#lightbox").hidden = true; });

render();
