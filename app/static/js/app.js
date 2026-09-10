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
  { n: 3, titre: "Caractéristiques", sous: "Type d'ombrière, cotes" },
  { n: 4, titre: "Notice + Cerfa", sous: "Notice, maître d'ouvrage" },
  { n: 5, titre: "Aperçu & export", sous: "PPTX, PDF, Cerfa" },
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
  setTimeout(() => el.classList.add("sort"), 3900);   // fondu de sortie (CSS)
  setTimeout(() => el.remove(), 4200);
}

// Relance une animation CSS sur un élément PERSISTANT (la retirer puis la
// remettre dans la même frame ne suffit pas : le reflow force le redémarrage).
function rejouer(el, classe) {
  el.classList.remove(classe);
  void el.offsetWidth;
  el.classList.add(classe);
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
// Ce panneau est reconstruit à chaque sauvegarde automatique : pour n'animer
// que ce qui VIENT de changer, on garde l'état du rendu précédent. Il est remis
// à zéro à chaque changement de projet (ouvrir un dossier n'est pas une
// progression, rien ne doit « popper »).
let chromePrecedent = { projetId: undefined, faites: null, statuts: null, pretes: null };

function renderChrome() {
  const p = state.projet;
  $("#proj-badge").hidden = !p;
  if (p) $("#proj-nom").textContent = p.nom;
  // accueil : sidebar et panneau complétude masqués (CSS .accueil)
  document.body.classList.toggle("accueil", state.etape === 0);

  if (chromePrecedent.projetId !== p?.id) {
    chromePrecedent = { projetId: p?.id, faites: null, statuts: null, pretes: null };
  }
  const avant = chromePrecedent;

  // stepper
  const nav = $("#stepper");
  nav.innerHTML = "";
  const faites = new Set();
  for (const s of STEPS) {
    const btn = document.createElement("button");
    const faite = etapeFaite(s.n);
    if (faite) faites.add(s.n);
    btn.className = "step" + (state.etape === s.n ? " active" : "") +
      (faite ? " done" : "") +
      (faite && avant.faites && !avant.faites.has(s.n) ? " vient-de-finir" : "");
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
  const statuts = {};
  // style (et non attribut) : c'est ce qui déclenche la transition CSS de l'arc
  if (ev) {
    const c = ev.completude;
    arc.style.strokeDasharray = `${(c.pretes / c.total * CIRC).toFixed(1)} ${CIRC.toFixed(1)}`;
    if (avant.pretes != null && c.pretes > avant.pretes) rejouer($(".ring"), "pulse");
    $("#ring-txt").textContent = c.pretes;
    $("#ring-tot").textContent = `/${c.total}`;
    $("#prog-label").textContent = "Pièces prêtes";
    $("#prog-sub").textContent = `${c.pretes} sur ${c.total}`;
    for (const piece of c.pieces) {
      const ok = piece.statut === "prete";
      statuts[piece.titre] = piece.statut;
      const vientDeFinir = ok && avant.statuts && avant.statuts[piece.titre] !== "prete";
      const div = document.createElement("div");
      div.className = "piece" + (ok ? " ok" : "") + (vientDeFinir ? " vient-de-finir" : "");
      div.innerHTML = `<span class="st ${esc(piece.statut)}">${ok ? "✓" : ""}</span>
        <span class="nm">${esc(piece.titre)}</span>`;
      list.appendChild(div);
    }
    chromePrecedent = { projetId: p?.id, faites, statuts, pretes: c.pretes };
  } else {
    arc.style.strokeDasharray = `0 ${CIRC.toFixed(1)}`;
    $("#ring-txt").textContent = "—";
    $("#ring-tot").textContent = "";
    $("#prog-label").textContent = "Aucun projet ouvert";
    $("#prog-sub").textContent = "";
  }

  renderCoherence(ev);
}

// Anomalies de cohérence : compter les pièces ne suffit pas à dire qu'un
// dossier est bon. Un dossier réel affichait « 11/11 pièces prêtes » avec
// deux adresses différentes selon la planche, des parcelles de deux communes
// distantes de 100 km, et un fichier DP6 disparu du disque.
const LIBELLE_GRAVITE = {
  bloquante: "À corriger avant dépôt",
  serieuse: "Incohérence",
  attention: "À compléter",
};

function renderCoherence(ev) {
  const box = $("#coherence-box");
  if (!box) return;
  const anomalies = ev?.coherence || [];
  if (!anomalies.length) {
    box.innerHTML = "";
    return;
  }
  const bloquantes = anomalies.filter((a) => a.gravite === "bloquante").length;
  box.innerHTML = `
    <div class="rp-title" style="margin-top:18px">Cohérence
      ${bloquantes ? `<span class="coh-compte">${bloquantes}</span>` : ""}</div>
    ${anomalies.map((a) => `
      <div class="coh coh-${esc(a.gravite)}">
        <b>${esc(LIBELLE_GRAVITE[a.gravite] || a.gravite)}</b>
        <span>${esc(a.message)}</span>
        <small>${esc(a.ou)}</small>
      </div>`).join("")}`;
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
// Animation d'entrée : seulement quand l'étape CHANGE. render() est aussi
// rappelé dans une même étape (dépôt d'un fichier, choix du type) et tout
// rejouer à chaque fois serait pénible. La classe est retirée après coup,
// sinon tout contenu réinjecté plus tard dans #main s'animerait à nouveau.
let etapeAffichee = null;
let finEntree = null;

function render() {
  renderChrome();
  const main = $("#main");
  if (etapeAffichee !== state.etape) {
    etapeAffichee = state.etape;
    rejouer(main, "entree");
    clearTimeout(finEntree);
    finEntree = setTimeout(() => main.classList.remove("entree"), 800);
  }
  if (state.etape === 0) return renderAccueil(main);
  if (state.etape === 1) return renderEtapeLocalisation(main);
  if (state.etape === 2) return renderEtapePieces(main);
  if (state.etape === 3) return renderEtapeCaracteristiques(main);
  if (state.etape === 4) return renderEtapeNotice(main);
  if (state.etape === 5) return renderEtapeExport(main);
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
    for (const [i, pr] of data.projets.entries()) {
      const c = pr.completude || { pretes: 0, total: 1 };
      const pct = Math.round((c.pretes / c.total) * 100);
      const complet = c.pretes >= c.total;
      const teinte = complet ? "var(--gv-green)" : "var(--gv-violet)";
      const div = document.createElement("div");
      div.className = "home-item";
      div.style.setProperty("--i", Math.min(i, 12));   // rang dans la cascade (CSS)
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
    <div class="crumb">Étape 1 / 5</div>
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

// ---- Type d'ombrière (Mono Bas / Mono Haut / Double) ----
// Rapatrié ici le 01/09/2026 avec le retrait du module Insertion : ce
// sélecteur vivait dans l'écran Insertion alors qu'il écrit `ombriere.famille`,
// dont dépendent la coupe DP3 de secours, le libellé de la notice et le
// descriptif du Cerfa. Il n'y a AUCUNE présélection : tant que rien n'est
// choisi, le catalogue n'a pas à souffler ses cotes dans un formulaire
// officiel (doctrine « brouillon avec trous signalés »).
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
  const box = $("#omb-type");
  if (!box) return;
  const cur = state.projet.ombriere?.famille || "";
  box.innerHTML = TYPES_OMBRIERE.map((t) => `
    <button class="type-chip ${t.famille === cur ? "actif" : ""}" data-type="${esc(t.famille)}">
      <span class="type-glyph">${glypheType(t.famille)}</span>
      <b>${esc(t.libelle)}</b><span class="type-sub">${esc(t.desc)}</span>
    </button>`).join("");
  box.querySelectorAll("[data-type]").forEach((b) => b.addEventListener("click", async () => {
    if (b.dataset.type === cur) return;
    // La route renvoie le projet RELU DU DISQUE. Sans cette sauvegarde
    // préalable, une valeur en cours de frappe dans le formulaire juste
    // au-dessus (l'autosave n'ayant pas encore couru, il attend 900 ms)
    // disparaissait à l'écran au clic sur une vignette. Ce risque n'existait
    // pas quand le sélecteur vivait sur un écran isolé.
    if (state.dirty) { try { await sauvegarder(); } catch (e) { return; } }
    const d = await api(`/api/projets/${state.projet.id}/ombriere/type`, {
      method: "PUT", body: JSON.stringify({ famille: b.dataset.type }),
    });
    state.projet = d.projet;
    state.evaluation = d.evaluation || state.evaluation;
    render();
  }));
}

// Destination de l'électricité produite. Les clés DOIVENT rester alignées sur
// DESTINATIONS dans app/notice.py : elles pilotent la phrase de la notice et la
// case « destination de l'énergie » du Cerfa.
const DESTINATIONS_ENERGIE = [
  { cle: "autoconsommation_totale", libelle: "Autoconsommation totale" },
  { cle: "autoconsommation_surplus", libelle: "Autoconsommation avec vente du surplus" },
  { cle: "vente_totale", libelle: "Vente totale" },
];

// ---------------- étape 2 : caractéristiques ----------------
// (la coupe du projet = pièce DP3 déposée par le BE, plus de coupe type)
function renderEtapeCaracteristiques(main) {
  const lecture = state.projet.meta?.plan_lecture;
  const proposes = state.projet.meta?.plan_champs_proposes || [];
  main.innerHTML = `
    <div class="crumb">Étape 3 / 5</div>
    <h1>Caractéristiques de l'ombrière</h1>
    ${lecture ? `<div class="note plan-note">Lu sur le plan de masse${lecture.reference_plan ? ` (${esc(lecture.reference_plan)})` : ""} :
      ${esc(resumeLecturePlan(lecture))} <span class="link" id="btn-plan-appliquer">Tout appliquer</span></div>` : ""}
    <h2 style="font-size:17px;color:var(--gv-navy);margin:20px 0 8px">Type d'ombrière</h2>
    <div id="omb-type" class="type-select"></div>
    ${state.projet.ombriere?.famille ? "" :
      `<div class="note" style="margin-top:8px">Type non choisi. Tant qu'il ne l'est pas,
        la coupe de secours, la notice et le Cerfa laisseront ce point vide plutôt que
        d'avancer des cotes catalogue.</div>`}

    <div class="formgrid" style="margin-top:20px">
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

    <h2 style="font-size:17px;color:var(--gv-navy);margin:22px 0 8px">Destination de l'électricité</h2>
    <select class="input" id="omb-destination" style="max-width:420px">
      <option value="">— à choisir —</option>
      ${DESTINATIONS_ENERGIE.map((d) => `<option value="${esc(d.cle)}"
        ${state.projet.ombriere?.destination_energie === d.cle ? "selected" : ""}>${esc(d.libelle)}</option>`).join("")}
    </select>
    ${state.projet.ombriere?.destination_energie ? "" :
      `<div class="note" style="margin-top:8px">Tant qu'elle n'est pas choisie, la notice
        et le Cerfa laisseront ce point à préciser plutôt que d'affirmer une injection réseau.</div>`}

    <div class="actionsrow">
      <button class="btn navy" id="btn-suivant">Continuer vers la notice</button>
    </div>`;
  renderTypeSelector();
  $("#omb-destination")?.addEventListener("change", (e) => {
    state.projet.ombriere = state.projet.ombriere || {};
    state.projet.ombriere.destination_energie = e.target.value || null;
    sauvegarderBientot();
  });
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

// ---------------- étape 3 : pièces du BE (uploads, glisser-déposer) ----------------
// La FVE n'est PAS une pièce du dossier : document source du BE, elle ne
// compte pas dans la complétude (voir CODE_FVE dans routes_documents.py).
const PIECE_FVE = { code: "fve", titre: "FVE · Fiche de validation d'emprises",
                    note: "PowerPoint du BE : pré-remplit l'étape 3 et fournit la DP6" };

const PIECES_UPLOAD = [
  { code: "dp2", titre: "DP2 · Plan de masse", note: "" },
  { code: "dp3", titre: "DP3 · Coupe du projet", note: "coupe cotée fournie par le BE" },
  { code: "dp6", titre: "DP6 · Photomontage d'insertion", note: "état projeté ; l'état existant est repris de la DP7" },
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
  const deposees = data.fve_images || [];
  if (code === "fve") {
    const bits = [];
    if (data.plan_champs_proposes?.length) bits.push(`${data.plan_champs_proposes.length} champs pré-remplis`);
    if (deposees.length) bits.push(`${deposees.map((c) => c.toUpperCase()).join(" et ")} déposée${deposees.length > 1 ? "s" : ""}`);
    toast(bits.length ? `FVE lue : ${bits.join(", ")}. À relire.` : "FVE enregistrée, aucune valeur reconnue.",
          bits.length ? "ok" : "warn");
  } else if (data.plan_champs_proposes?.length) {
    toast(`Plan lu : ${data.plan_champs_proposes.length} champs pré-remplis à l'étape 3.`, "ok");
  } else {
    toast("Pièce enregistrée.", "ok");
  }
  render();
}

function renderEtapePieces(main) {
  main.innerHTML = `
    <div class="crumb">Étape 2 / 5</div>
    <h1>Pièces du bureau d'études</h1>

    <h2 style="font-size:17px;color:var(--gv-navy);margin:4px 0 8px">Fiche de validation d'emprises</h2>
    <div class="sub" style="margin-bottom:10px">Le PowerPoint du BE. Il pré-remplit les
      caractéristiques et fournit le photomontage d'insertion. Ce n'est pas une pièce du
      dossier : rien n'est imposé, tout reste modifiable.</div>
    <div class="home-list" id="slot-fve"></div>

    <h2 style="font-size:17px;color:var(--gv-navy);margin:24px 0 8px">Pièces du dossier</h2>
    <div class="home-list" id="slots"></div>

    <div class="actionsrow">
      <button class="btn navy" id="btn-suivant-pieces">Continuer vers les caractéristiques</button>
    </div>`;
  const box = $("#slots");
  $("#btn-suivant-pieces").addEventListener("click", () => allerEtape(3));
  for (const piece of [PIECE_FVE, ...PIECES_UPLOAD]) {
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
          <input type="file" accept=".pdf,.png,.jpg,.jpeg,.pptx" data-code="${piece.code}" hidden /></label>
      </div>`;
    (piece.code === "fve" ? $("#slot-fve") : box).appendChild(div);
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
    <div class="crumb">Étape 4 / 5</div>
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
    <div class="crumb">Étape 5 / 5</div>
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
    // .chargee coupe le reflet d'attente et joue le fondu (CSS). Gestionnaires
    // posés AVANT src : une image en cache peut arriver immédiatement.
    img.classList.remove("chargee");
    img.style.opacity = "";
    img.onload = () => img.classList.add("chargee");
    img.onerror = () => { img.alt = "Planche indisponible (complétez la localisation et les caractéristiques)"; img.style.opacity = 0.25; img.classList.add("chargee"); };
    img.src = `/api/projets/${state.projet.id}/planches/${code}.png?regen=${regen ? 1 : 0}&t=${Date.now()}`;
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
    btn.classList.add("charge");   // spinner (CSS)
    try {
      await fn();
    } catch (e) {
      liens.textContent = "";   // l'erreur est déjà affichée en toast par api()
    } finally {
      btn.disabled = false;
      btn.classList.remove("charge");
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
// Bandeau de session : affiché seulement sur une instance partagée. En local,
// il n'y a personne à déconnecter, la topbar reste inchangée.
(async () => {
  try {
    const moi = await (await fetch("/api/moi")).json();
    if (moi.ephemere) {
      // Mode web : rien n'est conserve cote serveur. Le dire AVANT que
      // quelqu'un ne monte un dossier complet puis ferme son onglet.
      $("#bandeau-texte").textContent =
        `Vos dossiers ne sont pas conservés sur le serveur et disparaissent après `
        + `${moi.duree_vie_h} h d'inactivité.`;
      $("#bandeau-ephemere").hidden = false;
    }
    if (!moi.utilisateur) return;
    $("#session-nom").textContent = moi.utilisateur;
    $("#session-box").hidden = false;
  } catch { /* instance locale ou serveur muet : rien à afficher */ }
})();

$("#btn-accueil").addEventListener("click", () => { state.etape = 0; render(); });
$("#lightbox").addEventListener("click", () => { $("#lightbox").hidden = true; });
document.addEventListener("keydown", (e) => { if (e.key === "Escape") $("#lightbox").hidden = true; });

render();
