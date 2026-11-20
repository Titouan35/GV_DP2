"""Pré-remplissage du Cerfa 13824*04 — autorisation de travaux sur un ERP.

Le parking d'un Carrefour Market est un parc de stationnement dépendant d'un
établissement recevant du public : la DP est accompagnée de cette demande
d'autorisation de construire, d'aménager ou de modifier un ERP (articles
L.111-8 et D.111-19-34 du CCH), qui porte sur l'accessibilité et la sécurité
incendie. Gabarit officiel versionné dans app/gabarits/cerfa_13824.pdf
(AcroForm, 8 pages, 166 champs, pas d'XFA). Ajouté le 30/09/2026.

Ce formulaire n'a PAS de noms de champs codés comme le 16702 (D2D_…) : ce
sont des libellés en clair, parfois trompeurs. Trois pièges relevés en
surimprimant les rectangles sur le gabarit, à ne pas « corriger » d'après le
nom du champ :

- Cadre 4.5, les trois cases sont DÉCALÉES d'un cran : le champ nommé « Parcs
  de stationnement intégrés » est la case « Stationnement couvert », le champ
  « ou isolés » est la case « intégrés », et « undefined_2 » est « isolés ».
- Cadre 4.3, code de l'environnement : le champ « Non_2 » est la case OUI, et
  le champ au long nom « Cette demande fait l'objet… » est la case NON.
- Les cases des pages 5 et 6 (bordereau) portent les MÊMES noms partiels
  (« Case_à_cocher1[0] »…) : on les remplit page par page.

Même doctrine que le 16702 : brouillon à trous signalés. Les cases qui
engagent le déclarant (Ad'AP, code de l'environnement, dérogations,
stationnement couvert au sens ERP) ne sont jamais cochées à sa place.
"""
from __future__ import annotations

import logging
from datetime import date

from pypdf import PdfReader, PdfWriter

from . import config, signature
from .cerfa import CASES_ADRESSE_DEMANDEUR, _decouper_adresse, _telephone_cerfa

logger = logging.getLogger(__name__)

GABARIT = config.REPO_ROOT / "app" / "gabarits" / "cerfa_13824.pdf"
NUMERO_CERFA = "13824*04"

# --- noms EXACTS des champs du gabarit, groupés par page (index 0) ----------
# Chaque entrée : nom de champ -> rôle. Les rôles servent aussi au rapport.
P1 = {
    "raison_sociale": "Raison sociale et dénomination",
    "siret": "Siret",                       # « Siret1 » = date de naissance à défaut
    "rep_madame": "Représentant de la personne morale   Madame",
    "rep_monsieur": "Monsieur_2",
    "rep_nom": "Nom représentant de la personne morale",
    "rep_prenom": "Prénom représentant de la personne morale",
    "numero": "Adresse  Numéro",
    "voie": "Voie",
    "lieudit": "Lieudit",
    "localite": "Localité",
    "cp": "CP",
    "portable": "Portable_2",
    "fixe": "Téléphone fixe_1",
    "courriel_local": "courriel _1 nom",     # case de GAUCHE, avant le « @ » imprimé
    "courriel_domaine": "courriel_1",         # case de DROITE
}
# Cadre 3, auteur du projet / maître d'œuvre : Greenvolt Next France dessine
# ses propres plans, le cadre reprend donc le cadre 1 en personne morale
# (confirmé le 30/09/2026). Positions vérifiées : ici les noms sont fidèles.
P2_MOE = {
    "personne_morale": "Personne morale",
    "raison_sociale": "Raison sociale et dénomination de la personne morale le cas échéant",
    "siret": "siret",
    "numero": "Adresse Numéro",
    "voie": "Voie_2",
    "lieudit": "Lieudit_2",
    "localite": "Localité_2",
    "cp": "CP_2",
    "portable": "Téléphone portable_2",
    "fixe": "Téléphone fixe_2",
    "courriel_local": "courriel_2 nom",       # case de GAUCHE, avant le « @ »
    "courriel_domaine": "courriel_2",          # case de DROITE
}
P2 = {
    "etablissement": "Nom de létablissement",
    "numero": "Numéro",
    "voie": "Voie_3",
    "lieudit": "Lieudit_3",
    "localite": "Localité_3",
    "cp": "CP_3",
    "sections": "N° de section",
    "parcelles": "N de parcelle s",
}
# Cadre 4.2, même page. Colonne de GAUCHE = avant travaux, de DROITE = après
# travaux ; quatre lignes par rubrique, seule la première est remplie.
# Les champs « R 12319… » sont ceux du classement sécurité incendie (ils
# portent le nom de l'article R.123-19 du CCH imprimé au-dessus).
P2_ACTIVITE = {
    "activite_avant": "Activité principale exercée dans létablissement par étages 1",
    "activite_apres": "Activité principale par étages 1",
    "classement_avant": "R 12319 du code de la construction et de lhabitation 1",
    "classement_apres": "R 12319 du code de la construction et de lhabitation 1_2",
    "exploitant_avant": "Identité de lexploitant sil est connu au moment du projet 1",
    "exploitant_apres": "Identité de lexploitant 1",
}
# Les ERP de nos projets sont des Carrefour Market (demande du 28/10/2026) :
# nom de l'établissement et exploitant en dur, sans code postal ni commune,
# activité « supermarché » et classement type M (magasins de vente).
ETABLISSEMENT_ERP = "CARREFOUR MARKET"
ACTIVITE_ERP = "SUPERMARCHÉ"
CLASSEMENT_ERP = "Type M"
P3 = {
    "construction_neuve": "Construction neuve",
    # ATTENTION : le champ NOMMÉ « Parcs de stationnement intégrés » est, par
    # sa position, la case « Stationnement couvert » (voir en-tête du module).
    # Cochée systématiquement : une ombrière couvre le stationnement (demande
    # du 30/09/2026).
    "stationnement_couvert": "Parcs de stationnement intégrés",
    "places_avant": "Avant réalisation du projetNombre de places de stationnement",
    "places_apres": "Après réalisation du projetNombre de places de stationnement",
}
P4 = {"lieu": "à", "date": "Le",
      # « Si vous souhaitez vous opposer à ce que les informations nominatives
      # … soient utilisées à des fins commerciales, cochez la case ci-contre »
      # — cochée systématiquement (demande du 30/09/2026).
      "opposition_commerciale": "Check Box6"}
# Encadré « Signature du (des) demandeur(s) », page 4, en points PDF
# (x, y bas-gauche, largeur, hauteur). Relevé sur le rendu du gabarit.
ZONE_SIGNATURE_PT = (385, 672, 165, 70)
# Bordereau (pages 5 et 6, noms partiels identiques d'une page à l'autre).
P5 = {"imprime": "Case_à_cocher1[0]",       # cet imprimé lui-même
      "plan_situation": "Case_à_cocher1[1]",
      "plan_cote": "Case_à_cocher1[6]"}       # plan côté 3 dimensions = DP2

# Ce que l'outil ne remplira jamais, et pourquoi (rendu à l'interface).
HORS_PORTEE = [
    ("En-tête", "Suite à un Ad'AP approuvé : Oui / Non",
     "un agenda d'accessibilité programmée engage le déclarant"),
    ("Cadre 3", "Nom et prénom de l'auteur du projet, courriers à lui adresser",
     "le maître d'œuvre est rempli en personne morale (Greenvolt) ; la personne "
     "physique qui signe les plans et le choix d'y recevoir les courriers restent au BE"),
    ("Cadre 4.2", "Catégorie ERP (1re à 5e), activités annexes",
     "l'activité (supermarché), le type M et l'exploitant (Carrefour Market) "
     "sont pré-remplis ; la catégorie dépend de l'effectif, donnée de l'exploitant"),
    ("Cadre 4.3", "Surface de plancher, Ad'AP, code de l'environnement",
     "déclarations qui engagent le déclarant"),
    ("Cadre 4.4", "Effectifs par niveau",
     "calcul réglementaire du règlement incendie, hors outil"),
    ("Cadre 4.5", "Parc intégré ou isolé, places PMR, année du PC initial",
     "« Stationnement couvert » est coché d'office ; le reste n'est pas saisi"),
    ("Cadre 5", "Dérogations et modalités particulières",
     "à la main du déclarant"),
    ("Cadre 6", "Signature",
     "apposée automatiquement : signature scannée (app/gabarits/signature_mo.png) "
     "si elle est fournie, sinon un paraphe générique sans nom — à valider par "
     "le signataire"),
    ("Bordereau", "Notices sécurité et accessibilité, plans par niveau, plans avant travaux",
     "pièces propres à l'autorisation ERP : les deux notices sortent du bouton « Notices ERP » "
     "(app/notices_erp.py), les plans restent au BE"),
]


def _etats_coches(reader: PdfReader) -> dict[str, str]:
    """Nom qualifié -> valeur « coché » de chaque case (/On, /Oui ou /1)."""
    etats = {}
    for nom, champ in (reader.get_fields() or {}).items():
        for e in champ.get("/_States_") or []:
            if str(e) != "/Off":
                etats[nom] = str(e)
    return etats


def _representant(mo: dict) -> tuple[str, str, str]:
    """(civilité, nom, prénom) du représentant, découpés seulement si c'est le
    représentant par défaut de Greenvolt (config). Sinon tout va dans « Nom »."""
    rep = (mo.get("representant") or "").strip()
    if rep == config.MAITRE_OUVRAGE_DEFAUT["representant"]:
        r = config.REPRESENTANT_MO_CERFA
        return r["civilite"], r["nom"], r["prenom"]
    return "", rep, ""


def _coordonnees(mo: dict, cases: dict) -> dict:
    """Adresse, téléphone et courriel du maître d'ouvrage dans les cases
    données — mêmes règles pour le cadre 2 (demandeur) et le cadre 3 (maître
    d'œuvre), qui sont la même société."""
    champs = {}
    adresse = (mo.get("adresse") or "").strip()
    if adresse == config.MAITRE_OUVRAGE_DEFAUT["adresse"].strip():
        a = config.ADRESSE_MO_CERFA
        parts = {"numero": a["D3N_numero"], "voie": a["D3V_voie"], "lieudit": a["D3W_lieudit"],
                 "cp": a["D3C_code"], "localite": a["D3L_localite"]}
    else:
        d = _decouper_adresse(adresse, CASES_ADRESSE_DEMANDEUR)
        parts = {"numero": d.get("D3N_numero", ""), "voie": d.get("D3V_voie", ""),
                 "lieudit": "", "cp": d.get("D3C_code", ""), "localite": d.get("D3L_localite", "")}
    for role, valeur in parts.items():
        champs[cases[role]] = valeur

    tel = _telephone_cerfa(mo.get("telephone"))
    # un 06/07 est un portable, le reste va en fixe : le formulaire a deux peignes
    champs[cases["portable"] if tel[:2] in ("06", "07") else cases["fixe"]] = tel
    local, _, domaine = (mo.get("email") or "").partition("@")
    champs[cases["courriel_local"]] = local
    champs[cases["courriel_domaine"]] = domaine
    return champs


def _champs_demandeur(projet: dict) -> dict:
    mo = projet.get("mo") or {}
    champs = {
        P1["raison_sociale"]: mo.get("raison_sociale") or "",
        P1["siret"]: (mo.get("siret") or "").replace(" ", ""),
    }
    civilite, nom, prenom = _representant(mo)
    champs[P1["rep_nom"]] = nom
    champs[P1["rep_prenom"]] = prenom
    if civilite == "Madame":
        champs[P1["rep_madame"]] = "COCHE"
    elif civilite == "Monsieur":
        champs[P1["rep_monsieur"]] = "COCHE"
    champs.update(_coordonnees(mo, P1))
    return champs


def _champs_maitre_oeuvre(projet: dict) -> dict:
    """Cadre 3 : la même société, en personne morale. Nom/prénom de la personne
    physique et « courriers à lui adresser » restent au BE."""
    mo = projet.get("mo") or {}
    if not mo.get("raison_sociale"):
        return {}
    champs = {
        P2_MOE["personne_morale"]: "COCHE",
        P2_MOE["raison_sociale"]: mo["raison_sociale"],
        P2_MOE["siret"]: (mo.get("siret") or "").replace(" ", ""),
    }
    champs.update(_coordonnees(mo, P2_MOE))
    return champs


def _champs_terrain(projet: dict) -> tuple[dict, list[str]]:
    loc = projet.get("localisation") or {}
    trous = []
    d = _decouper_adresse(loc.get("adresse"), CASES_ADRESSE_DEMANDEUR)
    champs = {
        P2["etablissement"]: ETABLISSEMENT_ERP,
        P2["numero"]: d.get("D3N_numero", ""),
        P2["voie"]: d.get("D3V_voie", ""),
        P2["localite"]: (loc.get("commune") or d.get("D3L_localite", "")).upper()[:60],
        P2["cp"]: loc.get("code_postal") or d.get("D3C_code", ""),
    }
    parcelles = loc.get("parcelles") or []
    if parcelles:
        # une seule ligne par rubrique sur ce formulaire : sections et numéros
        # sont listés, dans le même ordre, séparés par des virgules
        champs[P2["sections"]] = ", ".join(dict.fromkeys(p.get("section") or "?" for p in parcelles))
        champs[P2["parcelles"]] = ", ".join((p.get("section") or "") + " " + (p.get("numero") or "?")
                                            for p in parcelles)
    else:
        trous.append("À compléter : références cadastrales (cadre 4.1) — étape 1.")
    if not loc.get("adresse"):
        trous.append("À compléter : adresse du terrain (cadre 4.1) — étape 1.")
    return champs, trous


def _champs_activite() -> dict:
    """Cadre 4.2, avant et après travaux : l'ombrière ne change ni l'activité,
    ni le classement, ni l'exploitant du magasin."""
    a = P2_ACTIVITE
    return {a["activite_avant"]: ACTIVITE_ERP, a["activite_apres"]: ACTIVITE_ERP,
            a["classement_avant"]: CLASSEMENT_ERP, a["classement_apres"]: CLASSEMENT_ERP,
            a["exploitant_avant"]: ETABLISSEMENT_ERP, a["exploitant_apres"]: ETABLISSEMENT_ERP}


def _champs_projet(projet: dict) -> tuple[dict, list[str]]:
    omb = projet.get("ombriere") or {}
    champs = {P3["construction_neuve"]: "COCHE",     # une ombrière est une construction nouvelle
              P3["stationnement_couvert"]: "COCHE"}
    trous = []
    if omb.get("nb_places"):
        champs[P3["places_avant"]] = str(omb["nb_places"])
        champs[P3["places_apres"]] = str(omb["nb_places"])
    else:
        trous.append("À compléter : nombre de places de stationnement (cadre 4.5) — étape 3.")
    return champs, trous


def _champs_engagement(projet: dict) -> dict:
    loc = projet.get("localisation") or {}
    return {P4["lieu"]: loc.get("commune") or "",
            P4["date"]: date.today().strftime("%d/%m/%Y"),   # champ libre, pas un peigne
            P4["opposition_commerciale"]: "COCHE"}


def _texte_signature(projet: dict) -> str:
    mo = projet.get("mo") or {}
    civ, nom, prenom = _representant(mo)
    return f"{prenom} {nom.title()}".strip() if prenom else (nom or mo.get("raison_sociale") or "")


def _champs_bordereau(statuts: dict | None) -> tuple[dict, list[str]]:
    """Page 5 seulement : l'imprimé lui-même (toujours joint), le plan de
    situation (DP1) et le plan côté en 3 dimensions (DP2) quand ils sont prêts.
    Les autres pièces sont propres à l'autorisation ERP."""
    champs = {P5["imprime"]: "COCHE"}
    manquantes = []
    for code, cle, libelle in (("dp1_situation", "plan_situation", "plan de situation"),
                               ("dp2", "plan_cote", "plan côté en 3 dimensions (plan de masse)")):
        if statuts and statuts.get(code) == "prete":
            champs[P5[cle]] = "COCHE"
        else:
            manquantes.append(libelle)
    trous = ([f"À compléter : bordereau, non cochées faute d'être prêtes — {', '.join(manquantes)}."]
             if manquantes else [])
    return champs, trous


def rapport(champs: list[str], avertissements: list[str]) -> dict:
    return {"remplis": len(champs), "champs": sorted(champs),
            "a_completer": list(avertissements),
            "hors_portee": [{"cadre": c, "quoi": q, "pourquoi": p} for c, q, p in HORS_PORTEE]}


def preremplir(projet: dict, statuts_pieces: dict | None = None):
    """Remplit le gabarit dans les assets du projet. Renvoie (chemin, champs, avertissements).

    Fonctionne sur tout projet existant : aucune donnée nouvelle n'est requise,
    ce qui manque est signalé. Contrairement au 16702, pas de refus en régime
    PC : l'autorisation ERP accompagne aussi bien une DP qu'un PC.
    """
    if not GABARIT.exists():
        raise FileNotFoundError("Gabarit Cerfa 13824 absent (app/gabarits/cerfa_13824.pdf).")

    avertissements: list[str] = []
    terrain, t1 = _champs_terrain(projet)
    proj, t2 = _champs_projet(projet)
    bordereau, t3 = _champs_bordereau(statuts_pieces)
    avertissements += t1 + t2 + t3

    # champs par page (index 0) : indispensable pour le bordereau, dont les
    # noms partiels se répètent d'une page à l'autre
    par_page = {0: _champs_demandeur(projet),
                1: {**_champs_maitre_oeuvre(projet), **terrain,    # cadres 3, 4.1 et 4.2,
                    **_champs_activite()},                         # même page
                2: proj, 3: _champs_engagement(projet), 4: bordereau}

    chemin = config.assets_dir(projet["id"]) / "cerfa_13824_prerempli.pdf"
    # 1. la signature est posée sur une COPIE du gabarit (contenu de page),
    #    2. puis les champs sont remplis dessus : voir app/signature.py
    tampon = chemin.with_suffix(".signe.tmp.pdf")
    try:
        source = signature.apposer(GABARIT, tampon, page_index=3,
                                   zone_pt=ZONE_SIGNATURE_PT, texte=_texte_signature(projet))
    except Exception:                       # police, pdfium… : le formulaire sort quand même
        logger.warning("Signature non apposée sur le Cerfa 13824", exc_info=True)
        source = GABARIT
        avertissements.append("À compléter : signature non apposée (voir le journal), à signer à la main.")
    reader = PdfReader(source)
    etats = _etats_coches(reader)
    writer = PdfWriter()
    writer.append(reader)
    remplis: list[str] = []
    for i, champs in par_page.items():
        page = writer.pages[i]
        # les cases prennent la valeur « coché » PROPRE au gabarit (/On, /Oui, /1)
        valeurs = {}
        for nom, v in champs.items():
            if v == "COCHE":
                qualifie = next((q for q in etats if q == nom or q.endswith("." + nom)
                                 and (f"Page{i + 1}[0]" in q)), None)
                if qualifie is None:
                    qualifie = next((q for q in etats if q == nom), None)
                if qualifie is None:
                    continue
                valeurs[nom] = etats[qualifie]
            elif v:
                valeurs[nom] = v
        if not valeurs:
            continue
        writer.update_page_form_field_values(page, valeurs, auto_regenerate=False)
        remplis += list(valeurs)
    try:
        writer.set_need_appearances_writer(True)
    except AttributeError:  # pragma: no cover
        pass
    with open(chemin, "wb") as f:
        writer.write(f)
    tampon.unlink(missing_ok=True)
    return chemin, remplis, avertissements
