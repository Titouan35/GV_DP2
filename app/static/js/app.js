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
// chaîne : jamais deux PUT en parallèle, sinon la 2e sauvegarde partirait avec
// une date_modification périmée et déclencherait le verrou optimiste (409)
// pour un simple double-appui local.
let chaineSauvegarde = Promise.resolve();

function sauvegarder(silencieux = true) {
  chaineSauvegarde = chaineSauvegarde.catch(() => {}).then(() => _sauvegarderMaintenant(silencieux));
  return chaineSauvegarde;
}

async function _sauvegarderMaintenant(silencieux) {
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

  // panneau complétude : anneau SVG + liste des pièces
  const ev = state.evaluation;
  const list = $("#pieces-list");
  const arc = $("#ring-arc");
  const CIRC = 2 * Math.PI * 30;      // r = 30 dans le SVG
  list.innerHTML = "";
  if (ev) {
    const c = ev.completude;
    arc.setAttribute("stroke-dasharray", `${(c.pretes / c.total * CIRC).toFixed(1)} ${CIRC.toFixed(1)}`);
    $("#ring-txt").textContent = c.pretes;
    $("#ring-tot").textContent = `/${c.total}`;
    $("#prog-label").textContent = "Pièces prêtes";
    $("#prog-sub").textContent = `${c.pretes} sur ${c.total}`;
    for (const piece of c.pieces) {
      const ok = piece.statut === "prete";
      const div = document.createElement("div");
      div.className = "piece" + (ok ? " ok" : "");
      div.innerHTML = `<span class="st ${esc(piece.statut)}">${ok ? "✓" : ""}</span>
        <span class="nm">${esc(piece.titre)}</span>`;
      list.appendChild(div);
    }
  } else {
    arc.setAttribute("stroke-dasharray", `0 ${CIRC.toFixed(1)}`);
    $("#ring-txt").textContent = "—";
    $("#ring-tot").textContent = "";
    $("#prog-label").textContent = "Aucun projet ouvert";
    $("#prog-sub").textContent = "";
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
      const complet = c.pretes >= c.total;
      const teinte = complet ? "var(--gv-green)" : "var(--gv-violet)";
      const div = document.createElement("div");
      div.className = "home-item";
      div.innerHTML = `
        <span class="t">${esc(pr.nom)}<small>modifié ${esc((pr.date_modification || "").slice(0, 10) || "—")}</small></span>
        <span class="home-comp">
          <span class="home-comp-head">Complétude<b style="color:${teinte}">${c.pretes}/${c.total} pièces</b></span>
          <span class="home-bar"><i style="width:${pct}%;background:${teinte}"></i></span>
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
  signalerPresence(id, data.verrou);
}

// ---------------- présence sur un projet (mode partagé entre postes) -------
// Chaque poste exécute son propre serveur sur des dossiers OneDrive communs :
// aucun ne voit les verrous de l'autre. On dépose donc un fichier de présence,
// comme Word sur SharePoint, pour éviter que deux personnes ne travaillent en
// même temps sur le même dossier (OneDrive créerait une copie de conflit).
let presenceTimer = null;

async function signalerPresence(id, verrouConnu) {
  clearInterval(presenceTimer);
  let etat = verrouConnu;
  try {
    if (!etat || etat.a_moi) etat = await api(`/api/projets/${id}/verrou`, { method: "POST" });
  } catch { return; }
  afficherPresence(etat);
  if (etat && etat.a_moi) {
    // rafraîchit la présence : sans ça elle expire au bout de 15 min
    presenceTimer = setInterval(() => {
      if (state.projet?.id !== id) { clearInterval(presenceTimer); return; }
      api(`/api/projets/${id}/verrou`, { method: "POST" }).catch(() => {});
    }, 5 * 60 * 1000);
  }
}

function afficherPresence(etat) {
  const barre = $("#presence");
  if (!barre) return;
  if (!etat || etat.a_moi) { barre.hidden = true; barre.innerHTML = ""; return; }
  const depuis = (etat.depuis || "").replace("T", " ").slice(11, 16);
  barre.hidden = false;
  barre.innerHTML = `<span>⚠ Dossier ouvert par <b>${esc(etat.detenteur || "un collègue")}</b>`
    + (depuis ? ` depuis ${esc(depuis)}` : "")
    + ` — évitez d'y travailler à deux, OneDrive ne saurait pas départager.</span>`
    + `<button class="btn btn-sm" id="presence-forcer">Prendre la main</button>`;
  $("#presence-forcer").addEventListener("click", async () => {
    try {
      const e = await api(`/api/projets/${state.projet.id}/verrou?forcer=1`, { method: "POST" });
      afficherPresence(e);
      toast("Tu as pris la main sur ce dossier.", "ok");
      signalerPresence(state.projet.id, e);
    } catch { /* message déjà affiché */ }
  });
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
  $("#lightbox").classList.remove("compare");
  $("#lightbox-img").src = src;
  const im2 = $("#lightbox-img2"); if (im2) im2.hidden = true;
  $("#lightbox").hidden = false;
}

// comparaison côte à côte (photo du site « avant » vs insertion « après »)
function ouvrirComparaison(avant, apres) {
  const im2 = $("#lightbox-img2");
  if (!im2) { ouvrirLightbox(apres); return; }
  $("#lightbox").classList.add("compare");
  $("#lightbox-img").src = avant;
  im2.src = apres;
  im2.hidden = false;
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
    ? "modifié" : "automatique";
}

// contrôle géométrique : verdict de recouvrement de l'emprise tracée
const CONTROLE_LIB = {
  ok: { cls: "ok", txt: "Emprise bien couverte" },
  partiel: { cls: "warn", txt: "Ombrière raccourcie / partielle" },
  faible: { cls: "err", txt: "Placement hors emprise" },
  deborde: { cls: "warn", txt: "Structure étendue au-delà du volume" },
};
function badgeControle(controle) {
  if (!controle || !controle.verdict) return "";
  const d = CONTROLE_LIB[controle.verdict] || CONTROLE_LIB.partiel;
  const pct = Math.round((controle.couverture || 0) * 100);
  return `<span class="chip ${d.cls}" title="Part de l'emprise tracée réellement couverte par la structure générée">${d.txt} · ${pct}%</span>`;
}
// ligne de dépense IA : « N images ce projet · M au total (~X €) »
function depenseIA(s) {
  if (!s || s.images_global == null) return "";
  const cout = (s.images_global * (s.cout_image_eur || 0)).toFixed(2).replace(".", ",");
  const proj = s.images_projet != null ? `${s.images_projet} image${s.images_projet > 1 ? "s" : ""} ce projet · ` : "";
  return `${proj}${s.images_global} au total (~${cout} €)`;
}

async function majDepenseIA() {
  const el = $("#ins-depense");
  if (!el || !state.projet?.id) return;
  try {
    const s = await api(`/api/projets/${state.projet.id}/insertion/statut`);
    el.textContent = depenseIA(s);
  } catch { /* statut momentanément indisponible : on garde l'affichage actuel */ }
}

function renderInsertionAtelier(s) {
  const ins = state.projet.insertion || {};
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
      <div class="hd"><span class="ti-title">2 · Type &amp; placement</span>
        <span class="hint" id="ins-pose-etat"></span></div>
      <div class="bd">
        <div class="type-select" id="ins-type"></div>
        <div id="ins-pose-body" style="margin-top:14px"></div>
      </div>
    </div>

    <div class="card" style="margin-top:16px">
      <div class="hd"><span class="ti-title">3 · Générer</span>
        <span class="hint" id="ins-depense" title="Compteur local (l'API Gemini n'expose pas de solde) : nb d'images générées x coût unitaire">${depenseIA(s)}</span></div>
      <div class="bd">
        <div class="payload-strip" id="ins-payload"></div>
        <div class="field" style="margin-top:12px"><label>Consignes complémentaires</label>
          <textarea class="input notice-ta" id="ins-consignes" rows="2"
            placeholder="Ex. : garder le mât d'éclairage">${esc(ins.consignes || "")}</textarea></div>
        <details class="foldable" id="ins-prompt-fold" style="margin-top:10px"><summary>Prompt</summary>
          <div class="bd">
            <textarea class="input prompt-ta" id="ins-prompt" rows="12" spellcheck="false"></textarea>
            <div class="actionsrow" style="margin-top:8px">
              <button class="btn" id="ins-prompt-auto">↺ Prompt auto</button>
              <span class="hint" id="ins-prompt-etat"></span>
            </div>
          </div>
        </details>
        <button class="btn primary" id="ins-generer-api" style="width:100%;margin-top:12px">Générer l'insertion</button>
        <div id="ins-progress" class="sub" style="margin-top:6px;text-align:center;min-height:18px"></div>
      </div>
    </div>

    <div class="card" style="margin-top:16px">
      <div class="hd"><span class="ti-title">Insertions générées</span></div>
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

  renderTypeSelector();
  renderPhotosInsertion();
  renderGalerie();
  reprendreGenerationSiEnCours();   // job encore en cours après un refresh ?
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

function renderAlertePhoto() {
  const box = $("#ins-alerte-photo");
  if (box) box.innerHTML = "";
}

// (vue aérienne du flux v5 retirée le 19/07/2026 : jamais branchée dans
//  le wizard actuel ; code serveur archivé dans app/_archive/)

// ---- Type d'ombrière (Mono Bas / Mono Haut / Double) ----
const TYPES_OMBRIERE = [
  { famille: "START PLAINE Bas", libelle: "Mono Bas", desc: "poteau côté haut" },
  { famille: "START PLAINE Haut", libelle: "Mono Haut", desc: "poteau côté bas" },
  { famille: "START PLAINE Double", libelle: "Double", desc: "poteau central, profil en T" },
];

// schémas de profil (vus de bout) : rampant + poteau, à la manière de la coupe
const GLYPHES_TYPE = {
  "START PLAINE Bas": `<line x1="8" y1="10" x2="52" y2="16"/><line x1="48" y1="16" x2="48" y2="30"/>`,
  "START PLAINE Haut": `<line x1="8" y1="16" x2="52" y2="10"/><line x1="12" y1="10" x2="12" y2="30"/>`,
  "START PLAINE Double": `<line x1="8" y1="10" x2="52" y2="10"/><line x1="30" y1="10" x2="30" y2="30"/>`,
};
function glypheType(famille) {
  return `<svg width="60" height="34" viewBox="0 0 60 34" fill="none" stroke="var(--gv-violet)"
    stroke-width="2" stroke-linecap="round"><rect x="8" y="6" width="44" height="4" rx="1"
    fill="#c7c0ff" stroke="none"/>${GLYPHES_TYPE[famille] || ""}</svg>`;
}

function renderTypeSelector() {
  const box = $("#ins-type");
  if (!box) return;
  const cur = state.projet.ombriere?.famille || "START PLAINE Bas";
  box.innerHTML = TYPES_OMBRIERE.map((t) => `
    <button class="type-chip ${t.famille === cur ? "actif" : ""}" data-type="${esc(t.famille)}">
      <span class="type-glyph">${glypheType(t.famille)}</span>
      <b>${esc(t.libelle)}</b><span class="type-sub">${esc(t.desc)}</span>
    </button>`).join("");
  box.querySelectorAll("[data-type]").forEach((b) => b.addEventListener("click", async () => {
    if (b.dataset.type === (state.projet.ombriere?.famille || "")) return;
    const d = await api(`/api/projets/${state.projet.id}/insertion/type`, {
      method: "PUT", body: JSON.stringify({ famille: b.dataset.type }),
    });
    state.projet = d.projet;
    renderTypeSelector();
    rafraichirPayloadEtPrompt();
  }));
}

// ---- Placement « un geste » : un cliqué-glissé = une ombrière ----
const poseUI = { img: null, ombrieres: [], planDims: [], volumes: [],
                 horizon: null, horizonAjuste: false, hauteurVue: 1.6,
                 diagnostic: null, cam: null, sel: null, drag: null,
                 volumesLocaux: {}, pressePx: null };

function posePhoto() {
  return (state.projet.insertion || {}).photo || null;
}

function poseOmbrieres() {
  const ins = state.projet.insertion || {};
  const p = (ins.poses || {})[ins.photo] || {};
  if (Array.isArray(p.ombrieres)) return p.ombrieres.map((o) => ({ ...o }));
  if (p.bord_avant) return [{ bord_avant: p.bord_avant }];   // ancien format
  return [];
}

function majEtatPose() {
  const el = $("#ins-pose-etat");
  if (!el) return;
  const n = poseUI.ombrieres.length;
  el.textContent = n
    ? `(${n} ombrière${n > 1 ? "s" : ""} tracée${n > 1 ? "s" : ""})`
    : "(rien tracé — Gemini placera seul)";
}

function sauverPose() {
  const photo = posePhoto();
  if (!photo) return;
  const corps = { photo, ombrieres: poseUI.ombrieres, hauteur_vue: poseUI.hauteurVue };
  if (poseUI.horizonAjuste && poseUI.horizon != null) corps.horizon = poseUI.horizon;
  api(`/api/projets/${state.projet.id}/insertion/pose`, {
    method: "PUT", body: JSON.stringify(corps),
  }).then((d) => {
    state.projet = d.projet;
    poseUI.ombrieres = poseOmbrieres();      // récupère cotes pré-remplies + tri
    appliquerVolumes(d.volumes);             // filaire = la géométrie du serveur
    majEtatPose();
    renderTableauOmbrieres();
    dessinerPose();
    rafraichirPayloadEtPrompt();
  }).catch(() => {});
}

// le filaire affiché est EXACTEMENT la géométrie que le serveur enverra à
// Gemini (même moteur de perspective) : jamais de double calcul côté client
function appliquerVolumes(v) {
  poseUI.volumes = (v && v.volumes) || [];
  poseUI.cam = cameraJS(v && v.camera);
  poseUI.volumesLocaux = {};
  if (v && v.horizon != null) poseUI.horizon = v.horizon;
  if (v) {
    poseUI.horizonAjuste = !!v.horizon_ajuste;
    if (v.hauteur_vue != null) poseUI.hauteurVue = v.hauteur_vue;
    poseUI.diagnostic = v.diagnostic || null;
  }
  majDiagnosticPose();
}

// alerte de cohérence : hauteur de prise de vue déclarée vs celle qu'implique
// la géométrie. Un écart franc = longueur du tracé fausse (cause n°1 des
// volumes aberrants, constatée le 19/07/2026).
function majDiagnosticPose() {
  const el = $("#p-diagnostic");
  if (!el) return;
  const d = poseUI.diagnostic;
  if (!d) { el.innerHTML = ""; return; }
  if (d.message) {
    el.innerHTML = `<div class="alerte-pose">⚠ ${esc(d.message)}</div>`;
  } else if (d.distance_m != null) {
    el.innerHTML = `<span class="sub">Ombrière à ~${esc(String(d.distance_m))} m de l'objectif · `
      + `prise de vue ${esc(String(d.hauteur_declaree))} m · cohérent.</span>`;
  } else {
    el.innerHTML = "";
  }
}

async function chargerVolumes() {
  try {
    appliquerVolumes(await api(`/api/projets/${state.projet.id}/insertion/volumes`));
  } catch { appliquerVolumes(null); }
  dessinerPose();
}

async function renderPose() {
  const body = $("#ins-pose-body");
  if (!body) return;
  const photo = posePhoto();
  if (!photo) {
    poseUI.ombrieres = [];
    majEtatPose();
    body.innerHTML = `<p class="sub" style="padding:10px 14px">Choisis d'abord une photo du site.</p>`;
    return;
  }
  poseUI.ombrieres = poseOmbrieres();
  poseUI.drag = null;
  poseUI.sel = null;
  poseUI.volumesLocaux = {};
  majEtatPose();
  try { poseUI.planDims = (await api(`/api/projets/${state.projet.id}/insertion/plan-dims`)).dims_m || []; }
  catch { poseUI.planDims = []; }

  body.innerHTML = `
    <div class="mesure-tools">
      <span class="hint">Clique sur le parking pour poser · glisse pour déplacer · coins : taille · rond : rotation</span>
      <label class="hint" style="display:flex;align-items:center;gap:5px">Photo prise à
        <select class="input mini-select" id="p-hauteur">
          <option value="1.6">1,6 m (debout)</option>
          <option value="1.2">1,2 m (accroupi)</option>
          <option value="2.5">2,5 m (véhicule, escabeau)</option>
          <option value="10">10 m (étage, talus)</option>
          <option value="40">40 m (drone)</option>
        </select>
      </label>
      <span style="flex:1"></span>
      <button class="btn" id="p-annuler">Annuler la dernière</button>
      <button class="btn" id="p-effacer">Tout effacer</button>
    </div>
    <div class="mesure-canvas-wrap"><canvas id="p-canvas"></canvas></div>
    <div id="p-diagnostic" style="margin:6px 2px"></div>
    <div id="p-tableau"></div>`;

  const selHauteur = $("#p-hauteur");
  selHauteur.value = String(poseUI.hauteurVue ?? 1.6);
  selHauteur.addEventListener("change", () => {
    poseUI.hauteurVue = parseFloat(selHauteur.value);
    // la hauteur pilote la perspective : l'horizon est recalculé, on lève
    // donc le verrouillage manuel éventuel
    poseUI.horizonAjuste = false;
    sauverPose();
  });

  const canvas = $("#p-canvas");
  const img = new Image();
  img.onload = () => {
    // canvas a la LARGEUR DISPONIBLE (retour Florent : 760 px etait trop
    // petit, et un grand canvas rend chaque geste moins sensible : moins de
    // metres par pixel d'ecran)
    const dispo = Math.min(1400, (body.clientWidth || 760) - 4);
    const scale = Math.min(1, dispo / img.naturalWidth,
                           (window.innerHeight * 0.78) / img.naturalHeight);
    canvas.width = Math.round(img.naturalWidth * scale);
    canvas.height = Math.round(img.naturalHeight * scale);
    poseUI.img = img;
    dessinerPose();
    chargerVolumes();          // filaire initial + caméra (géométrie serveur)
  };
  img.src = urlInsertion(photo);

  // ---- GIZMO (20/07/2026) : on ne trace plus, on POSE puis on manipule ----
  // Un clic pose l'ombrière ; on la déplace en la saisissant, on la
  // redimensionne par les coins, on la tourne par la poignée ronde. Toute la
  // manipulation se fait en coordonnées SOL via la caméra calibrée (fluide,
  // aucun aller-retour serveur pendant le geste) ; la sauvegarde reconvertit
  // en bord_avant, donc le backend est inchangé.

  const pt = (e) => {
    const r = canvas.getBoundingClientRect();
    return [
      Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)),
      Math.min(1, Math.max(0, (e.clientY - r.top) / r.height)),
    ];
  };
  const px = (e) => {                      // en pixels CANVAS
    const [nx, ny] = pt(e);
    return [nx * canvas.width, ny * canvas.height];
  };
  const surHorizon = (e) => {
    if (poseUI.horizon == null || !poseUI.ombrieres.length) return false;
    const r = canvas.getBoundingClientRect();
    return Math.abs((e.clientY - r.top) - poseUI.horizon * r.height) < 10;
  };

  canvas.addEventListener("pointerdown", (e) => {
    try { canvas.setPointerCapture(e.pointerId); } catch { /* non capturable */ }
    poseUI.pressePx = px(e);
    if (surHorizon(e)) { poseUI.drag = { mode: "horizon" }; return; }
    const prise = gizmoSaisir(px(e), canvas);
    if (prise) { poseUI.drag = prise; poseUI.sel = prise.i; dessinerPose(); return; }
    poseUI.drag = { mode: "attente" };      // clic court = pose, sinon rien
  });

  canvas.addEventListener("pointermove", (e) => {
    const d = poseUI.drag;
    if (!d || d.mode === "attente") {
      if (!d) canvas.style.cursor = surHorizon(e) ? "ns-resize"
        : (gizmoSaisir(px(e), canvas) || {}).curseur || "copy";
      return;
    }
    if (d.mode === "horizon") {
      poseUI.horizon = Math.min(0.9, Math.max(0.05, pt(e)[1]));
      poseUI.horizonAjuste = true;
      dessinerPose();
      return;
    }
    // zone morte : un clic legerement tremble ne doit pas deplacer l'ombriere
    if (!d.arme) {
      const [x0, y0] = poseUI.pressePx || px(e);
      if (Math.hypot(px(e)[0] - x0, px(e)[1] - y0) < 6) return;
      d.arme = true;
    }
    gizmoBouger(d, px(e), canvas);
    dessinerPose();
  });

  canvas.addEventListener("pointerup", (e) => {
    const d = poseUI.drag;
    poseUI.drag = null;
    if (!d) return;
    if (d.mode === "horizon") { sauverPose(); return; }
    const [x0, y0] = poseUI.pressePx || px(e);
    const court = Math.hypot(px(e)[0] - x0, px(e)[1] - y0) < 8;
    if (d.mode === "attente") {
      if (court) gizmoPoser(px(e), canvas);
      return;
    }
    if (court) { dessinerPose(); return; }   // simple clic de sélection
    gizmoCommettre(d);
  });

  canvas.addEventListener("dblclick", () => {
    if (poseUI.sel != null) {
      poseUI.ombrieres.splice(poseUI.sel, 1);
      poseUI.sel = null;
      sauverPose();
    }
  });

  $("#p-annuler").addEventListener("click", () => {
    if (!poseUI.ombrieres.length) return;
    poseUI.ombrieres.pop();
    poseUI.sel = null;
    sauverPose();
  });
  $("#p-effacer").addEventListener("click", () => {
    poseUI.ombrieres = [];
    poseUI.sel = null;
    sauverPose();
  });
  renderTableauOmbrieres();
}

// ---------------- gizmo : caméra et géométrie sol ----------------

// réplique JS EXACTE de perspective.Camera (Python) : mêmes formules, pour
// manipuler au sol à 60 fps sans aller-retour serveur pendant le geste
function cameraJS(c) {
  if (!c) return null;
  const cx = c.W / 2, cy = c.H / 2;
  const theta = Math.atan2(cy - c.y_h, c.f);
  const s = Math.sin(theta), co = Math.cos(theta);
  return {
    W: c.W, H: c.H,
    imageVersSol(x, y) {
      const a = (y - cy) / c.f;
      const den = a * co + s;
      if (den <= 1e-6) return null;
      const Z = c.h_cam * (co - a * s) / den;
      const fwd = c.h_cam * s + Z * co;
      return [(x - cx) / c.f * fwd, Z];
    },
    solVersImage(X, Z, h = 0) {
      const ym = h - c.h_cam;
      const fwd = -ym * s + Z * co;
      if (fwd <= 1e-6) return null;
      return [cx + c.f * X / fwd, cy - c.f * (ym * co + Z * s) / fwd];
    },
  };
}

// état SOL d'une ombrière : centre, direction du bord (u), fuite (v), L, P
function gizmoEtat(i) {
  const cam = poseUI.cam;
  const o = poseUI.ombrieres[i];
  if (!cam || !o) return null;
  const A = cam.imageVersSol(o.bord_avant[0][0] * cam.W, o.bord_avant[0][1] * cam.H);
  const B = cam.imageVersSol(o.bord_avant[1][0] * cam.W, o.bord_avant[1][1] * cam.H);
  if (!A || !B) return null;
  let ux = B[0] - A[0], uz = B[1] - A[1];
  const L = Math.hypot(ux, uz) || 1;
  ux /= L; uz /= L;
  let vx = -uz, vz = ux;
  const mx = (A[0] + B[0]) / 2, mz = (A[1] + B[1]) / 2;
  // v pointe du côté qui S'ÉLOIGNE de la caméra (même convention que le serveur)
  if (Math.hypot(mx + vx, mz + vz) < Math.hypot(mx, mz)) { vx = -vx; vz = -vz; }
  const P = o.profondeur_m || 5;
  const vol = poseUI.volumes[i] || {};
  return { i, ux, uz, vx, vz, L, P,
           cx: mx + vx * P / 2, cz: mz + vz * P / 2,
           hAvant: vol.h_avant ?? 2.5, hFond: vol.h_fond ?? 3.5 };
}

function gizmoCoinsSol(st) {
  const u2 = st.L / 2, v2 = st.P / 2;
  return [                                  // avG, avD, fondD, fondG
    [st.cx - st.ux * u2 - st.vx * v2, st.cz - st.uz * u2 - st.vz * v2],
    [st.cx + st.ux * u2 - st.vx * v2, st.cz + st.uz * u2 - st.vz * v2],
    [st.cx + st.ux * u2 + st.vx * v2, st.cz + st.uz * u2 + st.vz * v2],
    [st.cx - st.ux * u2 + st.vx * v2, st.cz - st.uz * u2 + st.vz * v2],
  ];
}

function gizmoVolume(st) {
  const cam = poseUI.cam;
  if (!cam || !st) return null;
  const coins = gizmoCoinsSol(st);
  const hs = [st.hAvant, st.hAvant, st.hFond, st.hFond];
  const sol = coins.map((p) => cam.solVersImage(p[0], p[1], 0));
  const toit = coins.map((p, k) => cam.solVersImage(p[0], p[1], hs[k]));
  if (sol.some((p) => !p) || toit.some((p) => !p)) return null;
  const N = (p) => [p[0] / cam.W, p[1] / cam.H];
  return { sol: sol.map(N), toit: toit.map(N),
           h_avant: st.hAvant, h_fond: st.hFond };
}

// position de la poignée de rotation : dans l'axe du bord, 2 m au-delà du côté droit
function gizmoPoigneeRotation(st) {
  return [st.cx + st.ux * (st.L / 2 + 2.0), st.cz + st.uz * (st.L / 2 + 2.0)];
}

function _dansPolygone(p, poly) {
  let dedans = false;
  for (let a = 0, b = poly.length - 1; a < poly.length; b = a++) {
    const [xa, ya] = poly[a], [xb, yb] = poly[b];
    if ((ya > p[1]) !== (yb > p[1])
        && p[0] < (xb - xa) * (p[1] - ya) / (yb - ya) + xa) dedans = !dedans;
  }
  return dedans;
}

// que saisit-on à cette position (px canvas) ? poignées de la sélection
// d'abord, puis corps de n'importe quelle ombrière
function gizmoSaisir(p, canvas) {
  const cam = poseUI.cam;
  if (!cam) return null;
  const ech = canvas.width / cam.W;             // px image -> px canvas
  const C = (q) => [q[0] * ech, q[1] * ech];
  const RAYON = 12;

  if (poseUI.sel != null) {
    const st = gizmoEtat(poseUI.sel);
    if (st) {
      const rot = cam.solVersImage(...gizmoPoigneeRotation(st), 0);
      if (rot && Math.hypot(...C(rot).map((v, k) => v - p[k])) < RAYON + 2) {
        return { mode: "rotation", i: poseUI.sel, st, curseur: "grab" };
      }
      const coins = gizmoCoinsSol(st).map((q) => cam.solVersImage(q[0], q[1], 0));
      for (let k = 0; k < 4; k++) {
        if (coins[k] && Math.hypot(...C(coins[k]).map((v, j) => v - p[j])) < RAYON) {
          return { mode: "coin", i: poseUI.sel, st, coin: k, curseur: "nwse-resize" };
        }
      }
    }
  }
  for (let i = 0; i < poseUI.ombrieres.length; i++) {
    const st = gizmoEtat(i);
    if (!st) continue;
    const sol = gizmoCoinsSol(st).map((q) => cam.solVersImage(q[0], q[1], 0));
    if (sol.every(Boolean) && _dansPolygone(p, sol.map(C))) {
      const prise = cam.imageVersSol(p[0] / ech, p[1] / ech);
      return { mode: "corps", i, st,
               decal: prise ? [st.cx - prise[0], st.cz - prise[1]] : [0, 0],
               curseur: "move" };
    }
  }
  return null;
}

function gizmoBouger(d, p, canvas) {
  const cam = poseUI.cam;
  if (!cam || !d.st) return;
  const sol = cam.imageVersSol(p[0] * cam.W / canvas.width, p[1] * cam.H / canvas.height);
  if (!sol) return;
  const st = d.st;
  if (d.mode === "corps") {
    st.cx = sol[0] + d.decal[0];
    st.cz = sol[1] + d.decal[1];
  } else if (d.mode === "rotation") {
    const ang = Math.atan2(sol[1] - st.cz, sol[0] - st.cx);
    const pas = Math.PI / 36;                      // crans de 5°
    const a = Math.round(ang / pas) * pas;
    st.ux = Math.cos(a); st.uz = Math.sin(a);
    st.vx = -st.uz; st.vz = st.ux;
    if (Math.hypot(st.cx + st.vx, st.cz + st.vz) < Math.hypot(st.cx, st.cz)) {
      st.vx = -st.vx; st.vz = -st.vz;             // v reste côté fuite
    }
  } else if (d.mode === "coin") {
    // demi-dimensions = projection du coin saisi sur les axes u et v,
    // arrondies au demi-mètre (crans nets, comme SketchUp)
    const dx = sol[0] - st.cx, dz = sol[1] - st.cz;
    const suivantU = Math.abs(dx * st.ux + dz * st.uz);
    const suivantV = Math.abs(dx * st.vx + dz * st.vz);
    st.L = Math.min(120, Math.max(5, Math.round(suivantU * 2 / 0.5) * 0.5));
    st.P = Math.min(40, Math.max(3, Math.round(suivantV * 2 / 0.5) * 0.5));
  }
  poseUI.volumesLocaux[d.i] = gizmoVolume(st);
}

// fin de geste : reconvertit l'état sol en bord_avant + cotes, puis sauvegarde
function gizmoCommettre(d) {
  const cam = poseUI.cam;
  const o = poseUI.ombrieres[d.i];
  if (!cam || !o || !d.st) return;
  const st = d.st;
  const coins = gizmoCoinsSol(st);
  const A = cam.solVersImage(coins[0][0], coins[0][1], 0);
  const B = cam.solVersImage(coins[1][0], coins[1][1], 0);
  if (!A || !B) { poseUI.volumesLocaux = {}; dessinerPose(); return; }
  o.bord_avant = [
    [Math.min(1, Math.max(0, A[0] / cam.W)), Math.min(1, Math.max(0, A[1] / cam.H))],
    [Math.min(1, Math.max(0, B[0] / cam.W)), Math.min(1, Math.max(0, B[1] / cam.H))],
  ];
  // cotes recalées sur la géométrie réelle : plus JAMAIS d'écart entre le
  // tracé et la valeur saisie (l'incohérence n°1 du diagnostic)
  o.longueur_m = Math.round(st.L * 10) / 10;
  o.profondeur_m = Math.round(st.P * 10) / 10;
  poseUI.volumesLocaux = {};
  sauverPose();
}

// clic sur une zone libre : pose une nouvelle ombrière centrée là
function gizmoPoser(p, canvas) {
  const cam = poseUI.cam;
  const famille = state.projet.ombriere?.famille || "START PLAINE Bas";
  const dims = poseUI.planDims[poseUI.ombrieres.length];
  const L = dims ? Math.round(dims.longueur_m * 10) / 10 : 20;
  const P = dims ? Math.round(dims.largeur_m * 10) / 10
                 : (famille === "START PLAINE Double" ? 10 : 5);

  if (cam) {
    const sol = cam.imageVersSol(p[0] * cam.W / canvas.width, p[1] * cam.H / canvas.height);
    if (!sol) { toast("Clique sous la ligne d'horizon (sur le sol).", "err"); return; }
    const st = { cx: sol[0], cz: sol[1], ux: 1, uz: 0, vx: 0, vz: 1, L, P };
    // bord perpendiculaire au regard : u = perpendiculaire de la direction caméra->point
    const n = Math.hypot(sol[0], sol[1]) || 1;
    st.vx = sol[0] / n; st.vz = sol[1] / n;        // fuite = s'éloigner
    st.ux = -st.vz; st.uz = st.vx;
    const A = cam.solVersImage(st.cx - st.ux * L / 2 - st.vx * P / 2,
                               st.cz - st.uz * L / 2 - st.vz * P / 2, 0);
    const B = cam.solVersImage(st.cx + st.ux * L / 2 - st.vx * P / 2,
                               st.cz + st.uz * L / 2 - st.vz * P / 2, 0);
    if (!A || !B) { toast("Trop près du bord : clique plus au centre.", "err"); return; }
    poseUI.ombrieres.push({
      bord_avant: [[A[0] / cam.W, A[1] / cam.H], [B[0] / cam.W, B[1] / cam.H]],
      famille, longueur_m: L, profondeur_m: P, pente_vers: "fond",
    });
  } else {
    // toute première pose : pas encore de caméra. Bord horizontal centré sur
    // le clic ; le serveur calibre la perspective dès la sauvegarde.
    const [nx, ny] = [p[0] / canvas.width, p[1] / canvas.height];
    const demi = 0.2;
    poseUI.ombrieres.push({
      bord_avant: [[Math.max(0, nx - demi), ny], [Math.min(1, nx + demi), ny]],
      famille, longueur_m: L, profondeur_m: P, pente_vers: "fond",
    });
  }
  poseUI.sel = poseUI.ombrieres.length - 1;
  sauverPose();
  toast("Ombrière posée : glisse-la, tourne-la, ajuste ses coins.", "ok");
}

// tableau : par ombrière, son type et ses cotes (auto du plan, modifiables)
function renderTableauOmbrieres() {

  const box = $("#p-tableau");
  if (!box) return;
  if (!poseUI.ombrieres.length) { box.innerHTML = ""; return; }
  const opts = (sel) => TYPES_OMBRIERE.map((t) =>
    `<option value="${esc(t.famille)}" ${t.famille === sel ? "selected" : ""}>${esc(t.libelle)}</option>`).join("");
  box.innerHTML = `<div class="cotes-grid">` + poseUI.ombrieres.map((o, i) => `
    <div class="cote-row">
      <b>Ombrière ${i + 1}</b>
      <select class="input mini-select" data-champ="famille" data-i="${i}">${opts(o.famille)}</select>
      <label>L <input class="input mini-m" type="number" step="0.1" data-champ="longueur_m" data-i="${i}" value="${o.longueur_m ?? ""}" /> m</label>
      <label>prof. <input class="input mini-m" type="number" step="0.1" data-champ="profondeur_m" data-i="${i}" value="${o.profondeur_m ?? ""}" /> m</label>
      <button class="btn btn-sm pente" data-pente="${i}"
        title="Sens de la pente : de quel côté se trouve le point haut">${
          (o.pente_vers || "fond") === "fond" ? "↗ haut au fond" : "↘ haut devant"}</button>
      <button class="btn btn-sm" data-suppr-omb="${i}" title="Retirer cette ombrière">✕</button>
    </div>`).join("") + `</div>`;

  box.querySelectorAll("[data-champ]").forEach((el) => el.addEventListener("change", () => {
    const o = poseUI.ombrieres[+el.dataset.i];
    if (el.dataset.champ === "famille") o.famille = el.value;
    else {
      const v = parseFloat((el.value || "").replace(",", "."));
      o[el.dataset.champ] = v > 0 ? v : null;
    }
    sauverPose();
  }));
  box.querySelectorAll("[data-pente]").forEach((b) => b.addEventListener("click", () => {
    const o = poseUI.ombrieres[+b.dataset.pente];
    o.pente_vers = (o.pente_vers || "fond") === "fond" ? "avant" : "fond";
    sauverPose();
  }));
  box.querySelectorAll("[data-suppr-omb]").forEach((b) => b.addEventListener("click", () => {
    poseUI.ombrieres.splice(+b.dataset.supprOmb, 1);
    sauverPose();
  }));
}

function dessinerPose() {
  const canvas = $("#p-canvas");
  if (!canvas || !poseUI.img) return;
  const dr = canvas.getContext("2d");
  dr.clearRect(0, 0, canvas.width, canvas.height);
  dr.drawImage(poseUI.img, 0, 0, canvas.width, canvas.height);

  const X = (p) => [p[0] * canvas.width, p[1] * canvas.height];
  const poly = (pts, coul, larg, pointille, remplir) => {
    dr.strokeStyle = coul; dr.lineWidth = larg;
    dr.setLineDash(pointille ? [6, 5] : []);
    dr.beginPath();
    pts.forEach((p, i) => { const [x, y] = X(p); i ? dr.lineTo(x, y) : dr.moveTo(x, y); });
    dr.closePath();
    if (remplir) { dr.fillStyle = remplir; dr.fill(); }
    dr.stroke();
    dr.setLineDash([]);
  };

  // volumes : version LOCALE pendant un geste (fluide), serveur sinon
  poseUI.ombrieres.forEach((o, i) => {
    const vol = poseUI.volumesLocaux[i] || poseUI.volumes[i];
    const actif = i === poseUI.sel;
    if (vol) {
      poly(vol.sol, actif ? "rgba(255,0,200,0.95)" : "rgba(255,0,200,0.6)",
           actif ? 2.5 : 2, true,
           actif ? "rgba(255,0,200,0.08)" : null);
      poly(vol.toit, actif ? "rgba(119,109,248,1)" : "rgba(119,109,248,0.7)",
           actif ? 3 : 2, false,
           actif ? "rgba(119,109,248,0.14)" : "rgba(119,109,248,0.08)");
      dr.strokeStyle = "rgba(119,109,248,0.7)"; dr.lineWidth = 1.5;
      for (let k = 0; k < 4; k++) {
        const [xs, ys] = X(vol.sol[k]), [xt, yt] = X(vol.toit[k]);
        dr.beginPath(); dr.moveTo(xs, ys); dr.lineTo(xt, yt); dr.stroke();
      }
      const [nx, ny] = X(vol.toit[0]);
      dr.fillStyle = "#002455"; dr.font = "bold 14px sans-serif";
      dr.fillText(String(i + 1), nx + 6, ny - 6);
    } else {
      // pas de volume calculable : on montre au moins le bord avant stocké
      const A = X(o.bord_avant[0]), B = X(o.bord_avant[1]);
      dr.strokeStyle = "#FF00C8"; dr.lineWidth = 4;
      dr.beginPath(); dr.moveTo(A[0], A[1]); dr.lineTo(B[0], B[1]); dr.stroke();
    }
  });

  // poignées de la sélection : coins (carrés) + rotation (rond violet)
  if (poseUI.sel != null && poseUI.cam) {
    const st = (poseUI.drag && poseUI.drag.i === poseUI.sel && poseUI.drag.st)
      ? poseUI.drag.st : gizmoEtat(poseUI.sel);
    if (st) {
      const cam = poseUI.cam;
      const ech = canvas.width / cam.W;
      const C = (q) => [q[0] * ech, q[1] * ech];
      for (const coin of gizmoCoinsSol(st)) {
        const q = cam.solVersImage(coin[0], coin[1], 0);
        if (!q) continue;
        const [x, y] = C(q);
        dr.fillStyle = "#FFFFFF"; dr.strokeStyle = "#FF00C8"; dr.lineWidth = 2.5;
        dr.beginPath(); dr.rect(x - 6, y - 6, 12, 12); dr.fill(); dr.stroke();
      }
      const rot = cam.solVersImage(...gizmoPoigneeRotation(st), 0);
      const bord = cam.solVersImage(st.cx + st.ux * st.L / 2, st.cz + st.uz * st.L / 2, 0);
      if (rot && bord) {
        const [rx, ry] = C(rot), [bx, by] = C(bord);
        dr.strokeStyle = "rgba(119,109,248,0.8)"; dr.lineWidth = 2;
        dr.setLineDash([4, 4]);
        dr.beginPath(); dr.moveTo(bx, by); dr.lineTo(rx, ry); dr.stroke();
        dr.setLineDash([]);
        dr.fillStyle = "#776DF8"; dr.strokeStyle = "#FFFFFF"; dr.lineWidth = 2.5;
        dr.beginPath(); dr.arc(rx, ry, 9, 0, 7); dr.fill(); dr.stroke();
        dr.fillStyle = "#FFFFFF"; dr.font = "bold 11px sans-serif";
        dr.fillText("↻", rx - 4, ry + 4);
      }
      // cotes vivantes pendant le geste : L x P au centre
      const centre = cam.solVersImage(st.cx, st.cz, 0);
      if (centre && poseUI.drag && poseUI.drag.st) {
        const [mx, my] = C(centre);
        const txt = `${String(st.L).replace(".", ",")} m × ${String(st.P).replace(".", ",")} m`;
        dr.font = "bold 13px sans-serif";
        const w = dr.measureText(txt).width + 12;
        dr.fillStyle = "rgba(0,36,85,0.85)";
        dr.fillRect(mx - w / 2, my - 12, w, 22);
        dr.fillStyle = "#FFFFFF";
        dr.fillText(txt, mx - w / 2 + 6, my + 4);
      }
    }
  }

  // ligne d'horizon (poignée) : visible dès qu'une ombrière est posée
  if (poseUI.horizon != null && poseUI.ombrieres.length) {
    const yh = poseUI.horizon * canvas.height;
    dr.strokeStyle = "rgba(0,140,255,0.85)"; dr.lineWidth = 2;
    dr.setLineDash([10, 7]);
    dr.beginPath(); dr.moveTo(0, yh); dr.lineTo(canvas.width, yh); dr.stroke();
    dr.setLineDash([]);
    dr.fillStyle = "rgba(0,140,255,0.9)";
    dr.beginPath(); dr.arc(canvas.width - 16, yh, 6, 0, 7); dr.fill();
    dr.fillStyle = "#004a80"; dr.font = "11px sans-serif";
    dr.fillText("horizon", canvas.width - 68, yh - 6);
  }
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
    html += `<div class="photo-choices">${data.photos.map((p) => `
        <div class="photo-choice ${p.chemin === data.active ? "actif" : ""}" data-active="${esc(p.chemin)}">
          <img src="${esc(p.url)}&t=${Date.now()}" alt="photo site" />
          <button class="photo-suppr" data-suppr="${esc(p.chemin)}" title="Retirer">✕</button>
        </div>`).join("")}</div>`;
  } else {
    html += `<div class="placeholder" style="padding:18px"><b>Aucune photo du site</b></div>`;
  }
  if (data.reutilisables.length) {
    html += `<div class="hint" style="margin:12px 0 6px">Pièces BE</div>
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
    renderPose();
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

// génération via l'API Gemini — en TÂCHE DE FOND côté serveur : le POST rend
// la main tout de suite, on suit l'avancement par polling. Fermer/quitter la
// page ne perd plus l'image (elle est rangée côté serveur à la fin du job).
let pollGeneration = null;

async function genererInsertionAPI() {
  const ins = state.projet.insertion || {};
  if (!ins.photo) { toast("Ajoute d'abord une photo du site (ou reprends une pièce BE).", "err"); return; }
  try {
    const promptEdite = state.promptEdite ? ($("#ins-prompt")?.value || "") : "";
    await api(`/api/projets/${state.projet.id}/insertion/generer`, {
      method: "POST", body: JSON.stringify(promptEdite ? { prompt: promptEdite } : {}),
    });
  } catch { return; }  // 400/409 déjà affichés en toast par api()
  majProgressGeneration(true);
  suivreGeneration();
}

function majProgressGeneration(enCours) {
  const btn = $("#ins-generer-api"), prog = $("#ins-progress");
  if (btn) btn.disabled = enCours;
  if (prog) prog.textContent = enCours
    ? "Génération en cours (10 à 30 s)… tu peux continuer à travailler, l'image arrivera ici."
    : "";
}

function suivreGeneration() {
  clearInterval(pollGeneration);
  const pid = state.projet?.id;
  if (!pid) return;
  let echecs = 0;
  pollGeneration = setInterval(async () => {
    if (!state.projet || state.projet.id !== pid) { clearInterval(pollGeneration); return; }
    let s;
    try {
      const resp = await fetch(`/api/projets/${pid}/insertion/generer/statut`);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      s = await resp.json();
      echecs = 0;
    } catch {
      // réseau/serveur momentanément indisponible : on réessaie, mais pas
      // indéfiniment (sinon bouton désactivé + « en cours… » figés à vie)
      if (++echecs >= 24) {   // ~1 min d'échecs consécutifs
        clearInterval(pollGeneration);
        pollGeneration = null;
        majProgressGeneration(false);
        toast("Suivi de la génération interrompu (serveur injoignable) : recharge la page.", "err");
      }
      return;
    }
    if (s.etat === "en_cours") return;
    clearInterval(pollGeneration);
    pollGeneration = null;
    majProgressGeneration(false);
    if (s.etat === "erreur") { toast(s.erreur || "Génération échouée.", "err"); return; }
    if (s.etat !== "prete") return;
    try {
      const d = await api(`/api/projets/${pid}`);
      if (state.projet && state.projet.id === pid) {
        // FUSION, pas remplacement : l'utilisateur a pu saisir des champs
        // pendant la génération (« tu peux continuer à travailler ») — on ne
        // reprend du serveur que la partie insertion et la date (le verrou
        // optimiste accepterait sinon un autosave à date périmée -> 409).
        state.projet.insertion = d.projet.insertion;
        state.projet.date_modification = d.projet.date_modification;
        state.projet.modifie_par = d.projet.modifie_par;
        state.evaluation = d.evaluation;
        renderChrome();
      }
    } catch { return; }
    const ctrl = s.image?.controle;
    if (ctrl && ctrl.verdict === "faible") {
      toast("Placement raté (ombrière hors emprise) : régénère.", "err");
    } else if (ctrl && ctrl.verdict === "deborde") {
      toast("Le modèle a construit au-delà du volume demandé : vérifie le rendu, régénère au besoin.", "warn");
    } else if (ctrl && ctrl.verdict === "partiel") {
      toast("Ombrière partiellement placée : à vérifier ou régénérer.", "warn");
    } else {
      toast("Insertion générée.", "ok");
    }
    renderGalerie();
    majDepenseIA();
    const fiche = $("#ins-fiche"); if (fiche) fiche.disabled = !state.projet.insertion?.retenue;
  }, 2500);
}

// au retour sur l'étape (ou après un refresh) : reprendre le suivi d'un job
// encore en cours côté serveur
async function reprendreGenerationSiEnCours() {
  if (!state.projet?.id) return;
  try {
    const resp = await fetch(`/api/projets/${state.projet.id}/insertion/generer/statut`);
    const s = await resp.json();
    if (s.etat === "en_cours") { majProgressGeneration(true); suivreGeneration(); }
  } catch { /* statut indisponible : rien à reprendre */ }
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
  const photoAvant = ins.photo ? urlInsertion(ins.photo) : null;
  for (const v of images) {
    const retenue = ins.retenue === v.fichier;
    const incluse = dansDossier.has(v.fichier);
    const quand = (v.date || "").replace("T", " ").slice(5, 16);   // MM-JJ HH:MM
    const essais = Number(v.essais || 1);
    const div = document.createElement("div");
    div.className = "ins-vignette" + (retenue ? " retenue" : "");
    div.innerHTML = `
      <div class="ins-visuel">
        <img class="zoomable" src="${urlInsertion(v.fichier)}" alt="Insertion IA" />
        ${badgeControle(v.controle)}
        ${retenue ? `<span class="ins-retenue">Retenue</span>` : ""}
      </div>
      <div class="sub" style="margin:4px 2px 0">${esc(quand)}${essais > 1 ? ` · ${essais} appels` : ""}${v.modele ? ` · ${esc(v.modele)}` : ""}</div>
      <div class="ins-actions">
        <label class="ins-check" title="Inclure cette insertion au dossier DP exporté">
          <input type="checkbox" data-dossier="${esc(v.fichier)}" ${incluse ? "checked" : ""} />Dossier DP
        </label>
        ${photoAvant ? `<button class="btn btn-sm" data-comparer="${esc(v.fichier)}" title="Photo du site et insertion côte à côte">Avant/après</button>` : ""}
        ${v.prompt ? `<button class="btn btn-sm" data-prompt="${esc(v.fichier)}" title="Voir et réutiliser le prompt de cette image">Prompt</button>` : ""}
        <a class="btn btn-sm" href="/api/projets/${state.projet.id}/insertion/image.jpg?chemin=${encodeURIComponent(v.fichier)}" download>JPEG</a>
        <button class="btn btn-sm danger" data-suppr="${esc(v.fichier)}">Retirer</button>
        <button class="btn btn-sm ${retenue ? "primary" : "navy"}" data-retenue="${esc(v.fichier)}">${retenue ? "Retenue" : "Retenir"}</button>
      </div>
      <div class="ins-prompt-detail" data-detail="${esc(v.fichier)}" hidden style="margin-top:6px">
        <textarea class="input prompt-ta" rows="6" readonly>${esc(v.prompt || "")}</textarea>
        <button class="btn btn-sm" data-reprendre="${esc(v.fichier)}" style="margin-top:4px">↪ Reprendre ce prompt pour la prochaine génération</button>
      </div>`;
    div.querySelector(".zoomable").addEventListener("click", () => ouvrirLightbox(urlInsertion(v.fichier)));
    box.appendChild(div);
  }
  const parFichier = Object.fromEntries(images.map((v) => [v.fichier, v]));
  box.querySelectorAll("[data-comparer]").forEach((b) => b.addEventListener("click", () =>
    ouvrirComparaison(photoAvant, urlInsertion(b.dataset.comparer))));
  box.querySelectorAll("[data-prompt]").forEach((b) => b.addEventListener("click", () => {
    const d = box.querySelector(`[data-detail="${CSS.escape(b.dataset.prompt)}"]`);
    if (d) d.hidden = !d.hidden;
  }));
  box.querySelectorAll("[data-reprendre]").forEach((b) => b.addEventListener("click", () => {
    const v = parFichier[b.dataset.reprendre];
    const ta = $("#ins-prompt");
    if (!v || !ta) return;
    ta.value = v.prompt || "";
    state.promptEdite = true;
    majEtatPrompt();
    const fold = $("#ins-prompt-fold"); if (fold) fold.open = true;
    toast("Prompt repris : modifie-le puis génère.", "ok");
    ta.scrollIntoView({ behavior: "smooth", block: "center" });
  }));
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
      <button class="btn primary" id="btn-depot" title="Contrôle bloquant : refuse tant que toutes les pièces ne sont pas prêtes">Assembler pour dépôt</button>
      <button class="btn" id="btn-pdf">Exporter en PDF</button>
      <button class="btn" id="btn-nettoyer" title="Supprime les fichiers régénérables (caches, images non retenues) du dossier projet">🧹 Nettoyer</button>
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
  // opérations longues : bouton désactivé pendant l'appel (un double-clic
  // lançait deux assemblages/exports concurrents) + message remis à zéro en
  // cas d'échec (« en cours… » restait figé après une erreur).
  const actionLongue = (btn, fn) => async () => {
    if (btn.disabled) return;
    btn.disabled = true;
    try {
      await fn();
    } catch (e) {
      liens.textContent = "";   // l'erreur est déjà affichée en toast par api()
    } finally {
      btn.disabled = false;
    }
  };
  const btnCerfa = $("#btn-cerfa"), btnPptx = $("#btn-pptx"), btnPdf = $("#btn-pdf");
  const btnDepot = $("#btn-depot"), btnNettoyer = $("#btn-nettoyer");
  btnDepot.addEventListener("click", actionLongue(btnDepot, async () => {
    liens.textContent = "Contrôle de complétude puis assemblage…";
    const data = await api(`/api/projets/${state.projet.id}/dossier?depot=1`, { method: "POST" });
    telechargerFichier(data.telechargement, data.fichier);
    liens.innerHTML = `Dossier PRÊT AU DÉPÔT téléchargé : <b>${esc(data.fichier)}</b> — `
      + `<a href="${esc(data.telechargement)}" download>relancer</a>`
      + (data.avertissements.length ? `<br />Avertissements : ${esc(data.avertissements.join(" ; "))}` : "");
    toast("Dossier complet assemblé.", "ok");
  }));
  btnNettoyer.addEventListener("click", actionLongue(btnNettoyer, async () => {
    const d = await api(`/api/projets/${state.projet.id}/nettoyer`, { method: "POST" });
    toast(`Nettoyage : ${d.fichiers_supprimes} fichiers, ${d.mo_liberes} Mo libérés.`, "ok");
  }));
  btnCerfa.addEventListener("click", actionLongue(btnCerfa, async () => {
    const data = await api(`/api/projets/${state.projet.id}/cerfa`, { method: "POST" });
    state.projet = data.projet; state.evaluation = data.evaluation;
    renderChrome();
    liens.innerHTML = `Cerfa ${esc(data.cerfa)} pré-rempli (${data.champs_remplis} champs) —
      <a href="/api/projets/${state.projet.id}/cerfa.pdf" target="_blank">ouvrir le PDF</a> (brouillon à relire).`
      + ((data.avertissements || []).length ? `<br />Avertissements : ${esc(data.avertissements.join(" ; "))}` : "");
    toast("Cerfa pré-rempli.", "ok");
  }));
  btnPptx.addEventListener("click", actionLongue(btnPptx, async () => {
    liens.textContent = "Assemblage en cours (génération des planches)…";
    const data = await api(`/api/projets/${state.projet.id}/dossier`, { method: "POST" });
    telechargerFichier(data.telechargement, data.fichier);
    liens.innerHTML = `Dossier téléchargé : <b>${esc(data.fichier)}</b> (dossier Téléchargements) — `
      + `<a href="${esc(data.telechargement)}" download>relancer</a>`
      + (data.avertissements.length ? `<br />Avertissements : ${esc(data.avertissements.join(" ; "))}` : "");
    toast("Dossier PPTX téléchargé.", "ok");
  }));
  btnPdf.addEventListener("click", actionLongue(btnPdf, async () => {
    liens.textContent = "Export PDF en cours…";
    const data = await api(`/api/projets/${state.projet.id}/dossier/pdf`, { method: "POST" });
    telechargerFichier(data.telechargement, data.fichier);
    liens.innerHTML = `PDF téléchargé : <b>${esc(data.fichier)}</b> (dossier Téléchargements) — `
      + `<a href="${esc(data.telechargement)}" download>relancer</a>`;
    toast("PDF téléchargé.", "ok");
  }));
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
// saisie non sauvegardée (autosave débouncé à 900 ms) : avertir avant de
// fermer/rafraîchir l'onglet plutôt que de perdre les derniers champs
window.addEventListener("beforeunload", (e) => {
  if (state.dirty) { e.preventDefault(); e.returnValue = ""; }
});
$("#btn-accueil").addEventListener("click", () => { state.etape = 0; render(); });
$("#lightbox").addEventListener("click", () => { $("#lightbox").hidden = true; });
document.addEventListener("keydown", (e) => { if (e.key === "Escape") $("#lightbox").hidden = true; });

render();
