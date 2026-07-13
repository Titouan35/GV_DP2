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
  { n: 1, titre: "Projet", sous: "Maître d'ouvrage, nom" },
  { n: 2, titre: "Localisation & cadastre", sous: "Adresse, parcelles" },
  { n: 3, titre: "Caractéristiques", sous: "Ombrière, puissance" },
  { n: 4, titre: "Pièces du BE", sous: "Masse, coupe, façades, DP6" },
  { n: 5, titre: "Insertion IA", sous: "Visuel commercial" },
  { n: 6, titre: "Notice descriptive", sous: "Rédaction assistée" },
  { n: 7, titre: "Aperçu & export", sous: "PPTX, PDF, Cerfa" },
];

const FAMILLES = ["START PLAINE Bas", "START PLAINE Haut", "START PLAINE Double"];

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

  // encart régime
  const ev = state.evaluation;
  const box = $("#regime-box");
  if (ev) {
    const r = ev.regime;
    box.className = "regime" + (r.regime === "PC" ? " pc" : "");
    $("#regime-titre").textContent =
      r.regime === "DP" ? "Déclaration préalable" : "Permis de construire";
    $("#regime-detail").textContent = `Cerfa ${r.cerfa} · ${r.raisons[0] || ""}`;
  }

  // panneau complétude
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
        <span class="nm">${esc(piece.titre)}<small>${esc(piece.detail)}</small></span>
        <span class="tag ${esc(piece.mode)}">${esc(piece.mode)}</span>`;
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
  if (n === 1) return Boolean(p.id && p.nom && p.mo?.raison_sociale);
  if (n === 2) return Boolean(p.localisation?.code_insee && p.localisation?.parcelles?.length);
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
  if (state.etape === 1) return renderEtapeProjet(main);
  if (state.etape === 2) return renderEtapeLocalisation(main);
  if (state.etape === 3) return renderEtapeCaracteristiques(main);
  if (state.etape === 4) return renderEtapePieces(main);
  if (state.etape === 5) return renderEtapeInsertion(main);
  if (state.etape === 6) return renderEtapeNotice(main);
  if (state.etape === 7) return renderEtapeExport(main);
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

function renderEtapeProjet(main) {
  main.innerHTML = `
    <div class="crumb">Étape 1 / 7</div>
    <h1>Projet</h1>
    <p class="sub">Identité du dossier et maître d'ouvrage (déclarant du Cerfa).</p>
    <div class="formgrid">
      ${champ("Nom du projet", "nom", { wide: true, placeholder: "Ex. : Carrefour Mondonville — parking nord" })}
      ${champ("Type de maître d'ouvrage", "mo.type", { select: ["Société", "Collectivité", "Particulier"] })}
      ${champ("Raison sociale / nom", "mo.raison_sociale")}
      ${champ("Représentant", "mo.representant")}
      ${champ("SIRET", "mo.siret", { placeholder: "14 chiffres" })}
      ${champ("Adresse du maître d'ouvrage", "mo.adresse", { wide: true })}
    </div>
    <div class="actionsrow">
      <button class="btn navy" id="btn-suivant">Continuer vers la localisation</button>
    </div>`;
  brancherChamps(main);
  $("#btn-suivant").addEventListener("click", async () => {
    if (!state.projet.nom) { toast("Le nom du projet est requis.", "err"); return; }
    await sauvegarder();
    allerEtape(2);
  });
}

// ---------------- étape 2 : localisation & cadastre ----------------
let map = null;
let parcelLayer = null;
let marker = null;

function renderEtapeLocalisation(main) {
  const loc = state.projet.localisation;
  main.innerHTML = `
    <div class="crumb">Étape 2 / 7</div>
    <h1>Localisation & cadastre</h1>
    <p class="sub">L'adresse propose les parcelles ; vous les validez ou les ajoutez à la main.</p>

    <div class="field">
      <label for="adresse">Adresse du parking</label>
      <div class="searchrow">
        <input id="adresse" class="input" placeholder="Ex. : Allée du Golf, 67620 Soufflenheim"
          value="${esc(loc.adresse ?? "")}" />
        <button class="btn navy" id="btn-geocode">Rechercher</button>
      </div>
      <div class="geo-results" id="geo-results" hidden></div>
    </div>

    <div class="grid">
      <div class="card">
        <div class="hd"><span class="ti-title">Repérage</span>
          <span style="font-size:11px;color:var(--muted)">Fond : IGN Géoplateforme</span></div>
        <div id="map"></div>
      </div>
      <div class="card">
        <div class="hd"><span class="ti-title">Parcelles</span></div>
        <div class="note" id="note-parcelles">Parcelles proposées depuis l'adresse — vérifiez la sélection avant d'ajouter.</div>
        <table class="ptable">
          <thead><tr><th>Section</th><th>N°</th><th style="text-align:right">Surface</th><th></th></tr></thead>
          <tbody id="ptable-body"></tbody>
          <tbody>
            <tr class="add">
              <td><input class="mini" id="add-section" placeholder="Ex. 30" maxlength="2" /></td>
              <td><input class="mini" id="add-numero" placeholder="Ex. 464" maxlength="4" /></td>
              <td></td>
              <td><button class="addbtn" id="btn-add-parcelle">Ajouter</button></td>
            </tr>
          </tbody>
        </table>
        <div class="chips" id="chips"></div>
      </div>
    </div>`;

  $("#btn-geocode").addEventListener("click", rechercherAdresse);
  $("#adresse").addEventListener("keydown", (e) => { if (e.key === "Enter") rechercherAdresse(); });
  $("#btn-add-parcelle").addEventListener("click", ajouterParcelleManuelle);

  initCarte();
  renderParcelles();
  renderChips();
}

function initCarte() {
  if (map) { map.remove(); map = null; }
  const loc = state.projet.localisation;
  const centre = loc.lat != null ? [loc.lat, loc.lon] : [46.6, 2.4]; // France
  const zoom = loc.lat != null ? 17 : 6;

  map = L.map("map", { zoomControl: true }).setView(centre, zoom);
  const wmts = (layer, format) =>
    `https://data.geopf.fr/wmts?SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0` +
    `&LAYER=${layer}&STYLE=normal&TILEMATRIXSET=PM&FORMAT=${format}` +
    `&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}`;
  const plan = L.tileLayer(wmts("GEOGRAPHICALGRIDSYSTEMS.PLANIGNV2", "image/png"),
    { maxZoom: 19, attribution: "IGN — Géoplateforme" });
  const ortho = L.tileLayer(wmts("ORTHOIMAGERY.ORTHOPHOTOS", "image/jpeg"),
    { maxZoom: 20, attribution: "IGN — Géoplateforme" });
  const cadastre = L.tileLayer(wmts("CADASTRALPARCELS.PARCELLAIRE_EXPRESS", "image/png"),
    { maxZoom: 20, opacity: 0.7 });
  (state.projet.localisation.parcelles.length ? ortho : plan).addTo(map);
  L.control.layers(
    { "Plan IGN": plan, "Photo aérienne": ortho },
    { "Cadastre": cadastre },
    { collapsed: true },
  ).addTo(map);

  parcelLayer = L.geoJSON(null, {
    style: { color: "#05DB79", weight: 2.5, fillColor: "#05DB79", fillOpacity: 0.14 },
  }).addTo(map);

  majCarte();
}

function majCarte() {
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
  if (marker) { marker.remove(); marker = null; }
  if (loc.lat != null) {
    marker = L.marker([loc.lat, loc.lon]).addTo(map);
  }
  const bounds = parcelLayer.getBounds();
  if (bounds.isValid()) map.fitBounds(bounds.pad(0.2));
  else if (loc.lat != null) map.setView([loc.lat, loc.lon], 17);
}

async function rechercherAdresse() {
  const q = $("#adresse").value.trim();
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
  majCarte();
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

// ---------------- étape 3 : caractéristiques ----------------
function renderEtapeCaracteristiques(main) {
  main.innerHTML = `
    <div class="crumb">Étape 3 / 7</div>
    <h1>Caractéristiques de l'ombrière</h1>
    <p class="sub">Le type choisi dans le catalogue (coupes Solstyce START) alimentera la coupe DP3, les façades DP4 et le module Insertion IA.</p>
    <div class="formgrid">
      ${champ("Famille de structure", "ombriere.famille", { select: FAMILLES })}
      ${champ("Puissance (kWc)", "ombriere.puissance_kwc", { type: "number", step: "1" })}
      ${champ("Longueur (m)", "ombriere.longueur_m", { type: "number", step: "0.1" })}
      ${champ("Largeur (m)", "ombriere.largeur_m", { type: "number", step: "0.1" })}
      ${champ("Nombre de travées", "ombriere.nb_travees", { type: "number", step: "1" })}
      ${champ("Entraxe (m)", "ombriere.entraxe_m", { type: "number", step: "0.1" })}
      ${champ("Pente (°)", "ombriere.pente_deg", { type: "number", step: "0.5" })}
      ${champ("Garde au sol (m)", "ombriere.garde_au_sol_m", { type: "number", step: "0.05" })}
      ${champ("Hauteur hors-tout (m)", "ombriere.hauteur_hors_tout_m", { type: "number", step: "0.05" })}
      ${champ("Type de module", "ombriere.type_module", { placeholder: "Ex. : 500 Wc full black" })}
      ${champ("Orientation", "ombriere.orientation", { placeholder: "Ex. : sud, est-ouest" })}
      ${champ("Nombre de places couvertes", "ombriere.nb_places", { type: "number", step: "1" })}
    </div>
    <p class="sub" style="margin-top:6px">La puissance pilote le régime : ≥ 3 000 kWc bascule le dossier en permis de construire.</p>
    <div class="actionsrow">
      <button class="btn" id="btn-apercus">Mettre à jour les aperçus DP3 / DP4</button>
      <button class="btn navy" id="btn-suivant">Continuer vers les pièces du BE</button>
    </div>
    <div class="grid" style="margin-top:18px" id="apercus" hidden>
      <div class="card"><div class="hd"><span class="ti-title">Aperçu DP3 · Coupe</span></div>
        <img id="img-dp3" class="planche-img" alt="Coupe DP3" /></div>
      <div class="card"><div class="hd"><span class="ti-title">Aperçu DP4 · Façades / toiture</span></div>
        <img id="img-dp4" class="planche-img" alt="Façades DP4" /></div>
    </div>`;
  brancherChamps(main);
  const chargerApercus = async () => {
    if (!state.projet.ombriere?.famille) { toast("Choisissez d'abord une famille de structure.", "err"); return; }
    await sauvegarder();
    $("#apercus").hidden = false;
    const t = Date.now();
    $("#img-dp3").src = `/api/projets/${state.projet.id}/planches/dp3_coupe.png?regen=1&t=${t}`;
    $("#img-dp4").src = `/api/projets/${state.projet.id}/planches/dp4_facades.png?regen=1&t=${t}`;
  };
  $("#btn-apercus").addEventListener("click", () => chargerApercus().catch(() => {}));
  $("#btn-suivant").addEventListener("click", async () => { await sauvegarder(); allerEtape(4); });
}

// ---------------- étape 4 : pièces du BE (uploads) ----------------
const PIECES_UPLOAD = [
  { code: "dp2", titre: "DP2 · Plan de masse", note: "site-spécifique, fourni par le BE" },
  { code: "dp3", titre: "DP3 · Coupe (repli BE)", note: "facultatif : remplace la coupe paramétrique" },
  { code: "dp4", titre: "DP4 · Façades (repli BE)", note: "facultatif : remplace les façades paramétriques" },
  { code: "dp6", titre: "DP6 · Photomontage d'insertion", note: "pièce officielle, jamais générée par IA" },
  { code: "dp7", titre: "DP7 · Photo environnement proche", note: "photo datée et repérée" },
  { code: "dp8", titre: "DP8 · Photo paysage lointain", note: "photo datée et repérée" },
];

function renderEtapePieces(main) {
  main.innerHTML = `
    <div class="crumb">Étape 4 / 7</div>
    <h1>Pièces du bureau d'études</h1>
    <p class="sub">PDF, PNG ou JPG (40 Mo max). Un fichier par pièce ; le dernier envoi remplace le précédent.</p>
    <div class="home-list" id="slots"></div>`;
  const box = $("#slots");
  for (const piece of PIECES_UPLOAD) {
    const doc = state.projet.documents?.[piece.code];
    const div = document.createElement("div");
    div.className = "card upload-slot";
    div.innerHTML = `
      <div class="bd" style="display:flex;align-items:center;gap:14px">
        <span class="st ${doc ? "prete" : "en_attente"}" style="width:10px;height:10px;border-radius:50%;flex:none;background:${doc ? "var(--gv-green)" : "var(--gv-grey)"}"></span>
        <span style="flex:1"><b style="color:var(--gv-navy)">${esc(piece.titre)}</b>
          <small style="display:block;color:var(--muted)">${doc ? esc(doc.nom_fichier) + " · " + esc((doc.date || "").slice(0, 10)) : esc(piece.note)}</small></span>
        ${doc ? `<a class="btn" href="/api/projets/${state.projet.id}/documents/${piece.code}" target="_blank">Voir</a>
                 <button class="btn" data-del="${piece.code}">Retirer</button>` : ""}
        <label class="btn navy" style="cursor:pointer">${doc ? "Remplacer" : "Téléverser"}
          <input type="file" accept=".pdf,.png,.jpg,.jpeg" data-code="${piece.code}" hidden /></label>
      </div>`;
    box.appendChild(div);
  }
  box.querySelectorAll("input[type=file]").forEach((inp) => {
    inp.addEventListener("change", async () => {
      if (!inp.files.length) return;
      const fd = new FormData();
      fd.append("fichier", inp.files[0]);
      let resp;
      try {
        resp = await fetch(`/api/projets/${state.projet.id}/documents/${inp.dataset.code}`, { method: "POST", body: fd });
      } catch { toast("Serveur injoignable.", "err"); return; }
      if (!resp.ok) { toast((await resp.json()).detail || "Échec de l'envoi.", "err"); return; }
      const data = await resp.json();
      state.projet = data.projet; state.evaluation = data.evaluation;
      toast("Pièce enregistrée.", "ok");
      render();
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

// ---------------- étape 5 : insertion IA (squelette phase 5) ----------------
async function renderEtapeInsertion(main) {
  main.innerHTML = `
    <div class="crumb">Étape 5 / 7</div>
    <h1>Insertion IA (visuel commercial)</h1>
    <p class="sub">Génération photoréaliste pour les fiches d'emprise et présentations. Étiquetée « visuel IA », jamais utilisée en pièce DP6.</p>
    <div id="ia-statut" class="placeholder">Vérification de la configuration…</div>`;
  try {
    const s = await api("/api/insertion/statut");
    const box = $("#ia-statut");
    if (s.configure) {
      box.innerHTML = `<b>Module configuré (${esc(s.fournisseur)})</b>
        L'interface de génération (photo du site, zone d'implantation, consignes libres, variantes) arrive dans la suite de la phase 5.`;
    } else {
      box.innerHTML = `<b>Clé API à créer (action Florent, ~10 min)</b>
        <div style="text-align:left;max-width:560px;margin:14px auto 0">
          ${s.guide.map((l) => `<div style="margin-bottom:7px">${esc(l)}</div>`).join("")}
          <div style="margin-top:12px;color:var(--gv-navy);font-weight:500">
            ${s.rappels.map((l) => `<div>· ${esc(l)}</div>`).join("")}
          </div>
        </div>
        <span class="phase-badge">Phase 5 · en attente de la clé API</span>`;
    }
  } catch { /* toast affiché */ }
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

function renderEtapeNotice(main) {
  const n = state.projet.notice || { sections: {} };
  main.innerHTML = `
    <div class="crumb">Étape 6 / 7</div>
    <h1>Notice descriptive (DP11)</h1>
    <p class="sub">Brouillon généré depuis la saisie et les données officielles (PLU, Géorisques). À relire et valider avant export.</p>
    <div class="actionsrow" style="margin:0 0 16px">
      <button class="btn navy" id="btn-gen-notice">Générer le brouillon</button>
      <button class="btn" id="btn-regen-notice" title="Écrase les 7 sections avec un nouveau brouillon">Tout regénérer</button>
      <label class="chip ${n.valide_humain ? "ok" : ""}" style="cursor:pointer;padding:8px 14px">
        <input type="checkbox" id="chk-valide" ${n.valide_humain ? "checked" : ""} style="margin-right:6px" />
        Notice relue et validée
      </label>
    </div>
    <div class="home-list" id="notice-sections"></div>`;
  const box = $("#notice-sections");
  for (const [cle, titre] of NOTICE_SECTIONS) {
    const div = document.createElement("div");
    div.className = "card";
    div.innerHTML = `<div class="hd"><span class="ti-title">${esc(titre)}</span></div>
      <div class="bd"><textarea class="input notice-ta" data-cle="${cle}" rows="4">${esc(n.sections?.[cle] || "")}</textarea></div>`;
    box.appendChild(div);
  }
  box.querySelectorAll("textarea").forEach((ta) => {
    ta.addEventListener("input", () => {
      state.projet.notice.sections[ta.dataset.cle] = ta.value;
      state.projet.notice.valide_humain = false;
      sauvegarderBientot();
    });
  });
  const generer = async (force) => {
    const data = await api(`/api/projets/${state.projet.id}/notice/generer?force=${force}`, { method: "POST" });
    state.projet = data.projet; state.evaluation = data.evaluation;
    toast("Brouillon de notice généré.", "ok");
    render();
  };
  $("#btn-gen-notice").addEventListener("click", () => generer(0).catch(() => {}));
  $("#btn-regen-notice").addEventListener("click", () => generer(1).catch(() => {}));
  $("#chk-valide").addEventListener("change", (e) => {
    state.projet.notice.valide_humain = e.target.checked;
    sauvegarder(false).catch(() => {});
  });
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
    <div class="crumb">Étape 7 / 7</div>
    <h1>Aperçu & export</h1>
    <p class="sub">Vérifiez les planches, pré-remplissez le Cerfa puis assemblez le dossier PowerPoint (export PDF pour le dépôt).</p>
    <div class="actionsrow" style="margin:0 0 8px">
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

  const chargerImg = (code, regen) => {
    const img = $(`#pl-${code}`);
    img.src = `/api/projets/${state.projet.id}/planches/${code}.png?regen=${regen ? 1 : 0}&t=${Date.now()}`;
    img.onerror = () => { img.alt = "Planche indisponible (complétez les étapes 2-3)"; img.style.opacity = 0.25; };
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
    liens.innerHTML = `Dossier assemblé : <a href="${esc(data.telechargement)}">${esc(data.fichier)}</a>`
      + (data.avertissements.length ? `<br />Avertissements : ${esc(data.avertissements.join(" ; "))}` : "");
    toast("Dossier PPTX assemblé.", "ok");
  });
  $("#btn-pdf").addEventListener("click", async () => {
    liens.textContent = "Export PDF via PowerPoint…";
    const data = await api(`/api/projets/${state.projet.id}/dossier/pdf`, { method: "POST" });
    liens.innerHTML = `PDF prêt : <a href="${esc(data.telechargement)}">${esc(data.fichier)}</a>`;
    toast("PDF exporté.", "ok");
  });
}

// ---------------- init ----------------
$("#btn-save").addEventListener("click", () => sauvegarder(false).catch(() => {}));
$("#btn-accueil").addEventListener("click", () => { state.etape = 0; render(); });
$("#btn-generer").addEventListener("click", () => allerEtape(7));

render();
