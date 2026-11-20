/* GV_DP — dossier lié : en ligne, les projets restent sur le POSTE.

   Pourquoi (20/11/2026). Le mode web ne conserve rien côté serveur, ce qui
   rend l'hébergement gratuit possible, mais un onglet fermé ou une mise en
   veille de l'hébergeur faisait perdre le travail. Edge et Chrome savent,
   avec l'accord de l'utilisateur, lire et écrire dans un dossier du poste :
   l'utilisateur y désigne son dossier PROJETS (le dossier OneDrive partagé),
   et c'est LUI la référence.

   Le serveur ne garde qu'une copie de travail, le temps des calculs :
     - à l'ouverture, on lui envoie les fichiers du projet (seulement ceux qui
       diffèrent de sa copie, comparés par taille et date) ;
     - après chaque écriture du serveur, on relit son état, on rapatrie ce
       qui a changé et on l'écrit dans le dossier du poste ;
     - si sa copie a disparu (veille, redémarrage), on la renvoie.

   Garde-fou : avant d'écraser un fichier du poste, on vérifie qu'il n'a pas
   changé depuis qu'on l'a lu (synchro OneDrive d'un collègue, par exemple).
   S'il a changé, on n'écrit pas, et on demande de rouvrir le projet : c'est
   le fichier du poste qui fait foi.

   Toutes les opérations passent par une file unique : jamais deux échanges
   en même temps sur la table des fichiers connus. Voir app/api/routes_synchro.py. */
"use strict";

const DossierLie = (() => {
  const PRIS_EN_CHARGE = typeof window.showDirectoryPicker === "function";
  const IDB_BASE = "gvdp", IDB_TABLE = "poignees", IDB_CLE = "projets";
  const ENVOIS_PARALLELES = 3;
  const RESUMES_PAR_LOT = 200;

  let modeWeb = false;
  let racine = null;        // dossier du poste, autorisation accordée
  let enAttente = null;     // dossier mémorisé, autorisation à redemander
  let projetOuvert = null;  // projet dont la copie serveur est suivie
  // chemin -> { taille, mtime } de la copie serveur, et { size, lastModified }
  // du fichier du poste, tels que connus au dernier échange
  let connu = new Map();
  const resumes = new Map();  // nom de fiche -> { cle, resume }
  const conflits = new Set();
  let etat = { code: "", date: null, message: "" };
  const ecouteurs = [];

  // ------------------------------------------------------------ file unique
  let file = Promise.resolve();
  let enCoursOuPrevu = 0;
  function enFile(fn) {
    enCoursOuPrevu += 1;
    const p = file.catch(() => {}).then(fn).finally(() => { enCoursOuPrevu -= 1; });
    file = p.catch(() => {});
    return p;
  }

  function signaler(code, message = "") {
    etat = { code, message, date: code === "enregistre" ? new Date() : etat.date };
    for (const cb of ecouteurs) { try { cb(etat); } catch { /* affichage seulement */ } }
  }

  // ----------------------------------------------- mémoire du dossier choisi
  // La poignée du dossier se garde dans IndexedDB (elle ne se sérialise pas
  // ailleurs) : on n'a pas à redésigner le dossier à chaque visite, seulement
  // à réautoriser l'accès d'un clic quand le navigateur l'exige.
  function idb(mode, operation) {
    return new Promise((resolve, reject) => {
      const ouverture = indexedDB.open(IDB_BASE, 1);
      ouverture.onupgradeneeded = () => ouverture.result.createObjectStore(IDB_TABLE);
      ouverture.onerror = () => reject(ouverture.error);
      ouverture.onsuccess = () => {
        const db = ouverture.result;
        const tx = db.transaction(IDB_TABLE, mode);
        const req = operation(tx.objectStore(IDB_TABLE));
        tx.oncomplete = () => { db.close(); resolve(req.result); };
        tx.onerror = () => { db.close(); reject(tx.error); };
      };
    });
  }
  const memoriser = (h) => idb("readwrite", (s) => s.put(h, IDB_CLE)).catch(() => {});
  const relire = () => idb("readonly", (s) => s.get(IDB_CLE)).catch(() => null);
  const oublierPoignee = () => idb("readwrite", (s) => s.delete(IDB_CLE)).catch(() => {});

  // --------------------------------------------------- fichiers du poste
  // Même règle que le serveur (routes_synchro.ignore).
  function ignore(nom) {
    const bas = nom.toLowerCase();
    return bas.endsWith(".tmp") || nom.startsWith("~$")
      || bas === "desktop.ini" || bas === "thumbs.db" || bas === ".ds_store";
  }

  const absent = (e) => e && (e.name === "NotFoundError" || e.name === "TypeMismatchError");
  const meta = (f) => ({ size: f.size, lastModified: f.lastModified });
  const memeLocal = (f, m) => Boolean(m) && f.size === m.size && f.lastModified === m.lastModified;

  async function sousDossier(parties, creer = false) {
    let d = racine;
    for (const p of parties) d = await d.getDirectoryHandle(p, { create: creer });
    return d;
  }

  async function lireLocal(chemin) {
    const parties = chemin.split("/");
    try {
      const d = await sousDossier(parties.slice(0, -1));
      return await (await d.getFileHandle(parties.at(-1))).getFile();
    } catch (e) {
      if (absent(e)) return null;
      throw e;
    }
  }

  async function ecrireLocal(chemin, blob) {
    const parties = chemin.split("/");
    const d = await sousDossier(parties.slice(0, -1), true);
    const h = await d.getFileHandle(parties.at(-1), { create: true });
    // createWritable écrit dans un fichier d'échange, substitué à la
    // fermeture : un fichier du poste n'est jamais laissé à moitié écrit.
    const w = await h.createWritable();
    try {
      await w.write(blob);
      await w.close();
    } catch (e) {
      try { await w.abort(); } catch { /* déjà fermé */ }
      throw e;
    }
    return h.getFile();
  }

  async function supprimerLocal(chemin, recursif = false) {
    const parties = chemin.split("/");
    try {
      const d = await sousDossier(parties.slice(0, -1));
      await d.removeEntry(parties.at(-1), { recursive: recursif });
    } catch (e) {
      if (!absent(e)) throw e;
    }
  }

  async function parcourir(d, prefixe, sortie) {
    for await (const [nom, h] of d.entries()) {
      if (ignore(nom)) continue;
      const chemin = `${prefixe}/${nom}`;
      if (h.kind === "directory") await parcourir(h, chemin, sortie);
      else sortie.set(chemin, await h.getFile());
    }
  }

  async function fichiersDuProjet(id) {
    const sortie = new Map();
    for (const nom of [`${id}.json`, `${id}.lock`]) {
      const f = await lireLocal(nom);
      if (f) sortie.set(nom, f);
    }
    let assets = null;
    try { assets = await racine.getDirectoryHandle(`${id}.assets`); } catch (e) { if (!absent(e)) throw e; }
    if (assets) await parcourir(assets, `${id}.assets`, sortie);
    return sortie;
  }

  // ------------------------------------------------------ copie serveur
  const url = (id, chemin, extra = "") =>
    `/api/synchro/${id}/fichier?chemin=${encodeURIComponent(chemin)}${extra}`;

  async function appel(adresse, options = {}) {
    let r;
    try { r = await fetch(adresse, options); }
    catch { throw new Error("serveur injoignable"); }
    if (!r.ok) {
      let detail = `erreur ${r.status}`;
      try { detail = (await r.json()).detail || detail; } catch { /* brut */ }
      throw new Error(detail);
    }
    return r;
  }

  const json = (corps) => ({
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(corps),
  });

  async function etatServeur(id) {
    const { fichiers } = await (await appel(`/api/synchro/${id}`)).json();
    return new Map(fichiers.map((f) => [f.chemin, f]));
  }

  async function envoyer(id, chemin, f) {
    return (await appel(url(id, chemin, `&mtime=${f.lastModified}`), { method: "PUT", body: f })).json();
  }

  async function enParallele(taches, n, fn) {
    let i = 0;
    const ouvriers = Array.from({ length: Math.min(n, taches.length) }, async () => {
      while (i < taches.length) await fn(taches[i++]);
    });
    await Promise.all(ouvriers);
  }

  // Poste -> serveur : la copie serveur devient le reflet du dossier du poste.
  async function remonter(id, progression) {
    const locaux = await fichiersDuProjet(id);
    const fiche = `${id}.json`;
    if (!locaux.has(fiche)) throw new Error("fiche du projet absente du dossier lié");
    const serveur = await etatServeur(id);

    const aEnvoyer = [...locaux].filter(([chemin, f]) => {
      const s = serveur.get(chemin);
      return !(s && s.taille === f.size && s.mtime === f.lastModified);
    });
    const total = aEnvoyer.reduce((t, [, f]) => t + f.size, 0);
    let fait = 0;
    progression?.(0, total);
    // La fiche part EN DERNIER : tant qu'elle manque, le serveur répond
    // « projet introuvable » et l'envoi est repris en entier. Un envoi
    // interrompu ne laisse donc jamais un projet sans ses pièces.
    const pieces = aEnvoyer.filter(([chemin]) => chemin !== fiche);
    await enParallele(pieces, ENVOIS_PARALLELES, async ([chemin, f]) => {
      await envoyer(id, chemin, f);
      fait += f.size;
      progression?.(fait, total);
    });
    for (const [chemin] of serveur) {
      if (!locaux.has(chemin)) await appel(url(id, chemin), { method: "DELETE" });
    }
    if (aEnvoyer.some(([chemin]) => chemin === fiche)) await envoyer(id, fiche, locaux.get(fiche));
    progression?.(total, total);

    connu = new Map([...locaux].map(([chemin, f]) => [
      chemin, { taille: f.size, mtime: f.lastModified, local: meta(f) }]));
    projetOuvert = id;
    conflits.clear();
    signaler("");     // le dossier du poste vient de faire foi : plus de conflit
  }

  // Serveur -> poste : on rapatrie ce que le serveur a écrit ou supprimé.
  async function rapatrier(id) {
    if (id !== projetOuvert) return;
    signaler("enregistrement");
    const serveur = await etatServeur(id);
    const dates = {};

    for (const [chemin, s] of serveur) {
      const k = connu.get(chemin);
      if (k && k.taille === s.taille && k.mtime === s.mtime) continue;
      const local = await lireLocal(chemin);
      if (local && !memeLocal(local, k?.local)) {
        // le fichier du poste a bougé depuis qu'on l'a lu : il fait foi
        conflits.add(chemin);
        continue;
      }
      const blob = await (await appel(url(id, chemin))).blob();
      const ecrit = await ecrireLocal(chemin, blob);
      connu.set(chemin, { taille: s.taille, mtime: s.mtime, local: meta(ecrit) });
      dates[chemin] = { taille: s.taille, mtime: s.mtime, vers: ecrit.lastModified };
    }

    for (const [chemin, k] of [...connu]) {
      if (serveur.has(chemin)) continue;
      const local = await lireLocal(chemin);
      if (local && !memeLocal(local, k.local)) conflits.add(chemin);
      else if (local) await supprimerLocal(chemin);
      connu.delete(chemin);
    }

    // Reporter la date locale sur la copie serveur évite de renvoyer ces
    // fichiers à la prochaine ouverture. Le serveur ne la reporte que si sa
    // copie est restée celle qu'on vient de lire.
    if (Object.keys(dates).length) {
      const { fichiers } = await (await appel(`/api/synchro/${id}/horodatage`, json({ dates }))).json();
      for (const e of fichiers) {
        const k = connu.get(e.chemin);
        if (k) { k.taille = e.taille; k.mtime = e.mtime; }
      }
    }
    signaler(conflits.size ? "conflit" : "enregistre");
  }

  let rapatriementPrevu = null;
  function tirer() {
    if (!racine || !projetOuvert) return Promise.resolve();
    if (rapatriementPrevu) return rapatriementPrevu;
    const id = projetOuvert;
    rapatriementPrevu = enFile(async () => {
      rapatriementPrevu = null;
      try { await rapatrier(id); }
      catch (e) { signaler("erreur", e.message); throw e; }
    });
    return rapatriementPrevu;
  }

  // ------------------------------------------------------------- API
  async function init(moi) {
    modeWeb = Boolean(moi?.ephemere);
    if (!modeWeb || !PRIS_EN_CHARGE) return;
    const h = await relire();
    if (!h) return;
    try {
      if ((await h.queryPermission({ mode: "readwrite" })) === "granted") racine = h;
      else enAttente = h;
    } catch { enAttente = h; }
  }

  function reinitialiser(h) {
    racine = h; enAttente = null; projetOuvert = null;
    connu = new Map(); resumes.clear(); conflits.clear();
    signaler("");
  }

  async function lier() {
    await file;     // ce qui était en cours d'écriture part d'abord
    let h = await window.showDirectoryPicker({ id: "gvdp-projets", mode: "readwrite" });
    // Le dossier GV_DP désigné à la place de PROJETS : on descend d'un cran.
    if (!(await contientDesFiches(h))) {
      try { h = await h.getDirectoryHandle("PROJETS"); } catch { /* dossier neuf : on le garde */ }
    }
    reinitialiser(h);
    await memoriser(h);
  }

  async function contientDesFiches(h) {
    for await (const [nom, e] of h.entries()) {
      if (e.kind === "file" && nom.endsWith(".json")) return true;
    }
    return false;
  }

  async function reconnecter() {
    if (!enAttente) return Boolean(racine);
    if ((await enAttente.requestPermission({ mode: "readwrite" })) !== "granted") return false;
    reinitialiser(enAttente);
    return true;
  }

  async function delier() {
    await file;
    reinitialiser(null);
    await oublierPoignee();
  }

  // Liste des projets du dossier du poste. Les fiches partent au serveur le
  // temps d'en calculer la ligne (régime, complétude), rien n'y est écrit ;
  // une fiche inchangée depuis la dernière liste n'est pas renvoyée.
  async function lister() {
    await file;
    const fiches = [];
    for await (const [nom, h] of racine.entries()) {
      if (h.kind === "file" && nom.endsWith(".json") && !ignore(nom)) fiches.push([nom, h]);
    }
    const lignes = [];
    const aCalculer = [];
    for (const [nom, h] of fiches) {
      const f = await h.getFile();
      const cle = `${f.size}:${f.lastModified}`;
      const c = resumes.get(nom);
      if (c && c.cle === cle) { if (c.resume) lignes.push(c.resume); continue; }
      try { aCalculer.push({ nom, cle, contenu: JSON.parse(await f.text()) }); }
      catch { resumes.set(nom, { cle, resume: null }); }
    }
    for (let i = 0; i < aCalculer.length; i += RESUMES_PAR_LOT) {
      const lot = aCalculer.slice(i, i + RESUMES_PAR_LOT);
      const r = await (await appel("/api/synchro/resumes", json({ projets: lot.map((x) => x.contenu) }))).json();
      lot.forEach((x, j) => {
        let resume = r.resumes[j];
        // copie de conflit OneDrive (« projet-POSTE.json ») ou fiche renommée :
        // elle porte l'identifiant d'un autre fichier, on ne la liste pas
        if (resume && `${resume.id}.json` !== x.nom) resume = null;
        resumes.set(x.nom, { cle: x.cle, resume });
        if (resume) lignes.push(resume);
      });
    }
    lignes.sort((a, b) => (b.date_modification || "").localeCompare(a.date_modification || ""));
    return { projets: lignes };
  }

  function ouvrir(id, progression) {
    return enFile(() => remonter(id, progression));
  }

  // Projet créé par le serveur : on suit sa copie dès maintenant.
  function suivre(id) {
    return enFile(async () => {
      projetOuvert = id; connu = new Map(); conflits.clear();
    }).then(tirer);
  }

  // Avant de rafraîchir la présence : le serveur doit voir celle que
  // OneDrive a pu déposer entre-temps (un collègue qui a pris la main).
  function pousserVerrou(id) {
    return enFile(async () => {
      if (!racine || id !== projetOuvert) return;
      const chemin = `${id}.lock`;
      const local = await lireLocal(chemin);
      const k = connu.get(chemin);
      if (local) {
        if (memeLocal(local, k?.local)) return;
        const e = await envoyer(id, chemin, local);
        connu.set(chemin, { taille: e.taille, mtime: e.mtime, local: meta(local) });
      } else if (k) {
        await appel(url(id, chemin), { method: "DELETE" });
        connu.delete(chemin);
      }
    });
  }

  // Le serveur répond « introuvable » pour le projet ouvert : si sa copie a
  // disparu (veille, redémarrage), on la renvoie et l'appelant réessaie.
  async function retablir(id) {
    if (!racine || id !== projetOuvert) return false;
    await file;
    if ((await etatServeur(id)).has(`${id}.json`)) return false;  // vrai 404
    await enFile(() => remonter(id));
    return true;
  }

  function supprimerProjet(id) {
    return enFile(async () => {
      // la fiche d'abord : le projet sort de la liste même si la suite échoue
      await supprimerLocal(`${id}.json`);
      await supprimerLocal(`${id}.lock`);
      await supprimerLocal(`${id}.assets`, true);
      resumes.delete(`${id}.json`);
      if (projetOuvert === id) { projetOuvert = null; connu = new Map(); }
      // copie serveur éventuelle : sans importance si elle n'existe pas
      fetch(`/api/projets/${id}`, { method: "DELETE" }).catch(() => {});
    });
  }

  return {
    get disponible() { return modeWeb && PRIS_EN_CHARGE; },
    get actif() { return modeWeb && racine !== null; },
    get aReconnecter() { return modeWeb && !racine && enAttente !== null; },
    get nom() { return (racine || enAttente)?.name || ""; },
    get etat() { return etat; },
    get conflits() { return [...conflits]; },
    enCours: () => enCoursOuPrevu > 0,
    attendre: () => file,
    onEtat: (cb) => ecouteurs.push(cb),
    init, lier, reconnecter, delier, lister, ouvrir, suivre, tirer, pousserVerrou,
    retablir, supprimerProjet,
  };
})();
