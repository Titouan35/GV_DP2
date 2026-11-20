"""Pré-remplissage du Cerfa de Déclaration Préalable.

ATTENTION réglementaire (constaté le 13/07/2026) : le formulaire DP n'est
plus le 13404 mais le **Cerfa n° 16702*03** (renumérotation des formulaires
d'urbanisme ; source : entreprendre.service-public.gouv.fr, fiche R2028).
Le gabarit officiel est versionné dans app/gabarits/cerfa_16702.pdf
(AcroForm, 20 pages, 386 champs).

Le pré-remplissage reste un BROUILLON : relecture obligatoire par le BE
avant dépôt (aucune case à risque n'est cochée automatiquement).
"""
from __future__ import annotations

import re
from datetime import date

from pypdf import PdfReader, PdfWriter

from . import config
from .catalogue import parametres_effectifs

GABARIT = config.REPO_ROOT / "app" / "gabarits" / "cerfa_16702.pdf"
NUMERO_CERFA = "16702*03"

COCHE = "/Oui"  # état « coché » des cases du gabarit


def _telephone_cerfa(brut: str | None) -> str:
    """Numéro en 10 chiffres collés, pour le peigne du formulaire.

    La case « Téléphone » est un peigne de 10 cases aux séparateurs déjà
    imprimés : « +33(0)6 81 19 42 76 » y entrait sur 19 caractères, décalant
    tout le numéro. Même piège que le champ date (E1D_date).

    L'indicatif international est retiré — le Cerfa a une case « Indicatif pour
    le pays étranger » séparée, et un numéro français s'écrit en 0X. Si le
    résultat ne fait pas 10 chiffres, on le rend tel quel : mieux vaut une case
    visiblement à corriger qu'un numéro tronqué qui a l'air juste.
    """
    chiffres = re.sub(r"\D", "", brut or "")
    if len(chiffres) > 10:
        if chiffres.startswith("0033"):
            chiffres = chiffres[4:]
        elif chiffres.startswith("33"):
            chiffres = chiffres[2:]
    if len(chiffres) == 9 and not chiffres.startswith("0"):
        chiffres = "0" + chiffres      # le « (0) » de +33(0)6… avait disparu
    return chiffres


def _champs_demandeur(projet: dict) -> dict:
    mo = projet.get("mo") or {}
    champs: dict[str, str] = {}
    if (mo.get("type") or "").lower() == "particulier":
        champs["D1N_nom"] = mo.get("raison_sociale") or mo.get("representant") or ""
    else:
        champs["D2D_denomination"] = mo.get("raison_sociale") or ""
        champs["D2R_raison"] = mo.get("raison_sociale") or ""
        champs["D2S_siret"] = (mo.get("siret") or "").replace(" ", "")
        champs["D2J_type"] = mo.get("type") or ""
        # Nom et prénom du représentant dans leurs cases respectives quand
        # c'est le représentant par défaut (config.REPRESENTANT_MO_CERFA) ;
        # sinon le texte libre va entier dans « Nom », on ne devine pas un
        # découpage (30/09/2026, avec l'arrivée du Cerfa 13824).
        rep = (mo.get("representant") or "").strip()
        if rep == config.MAITRE_OUVRAGE_DEFAUT["representant"]:
            champs["D2N_nom"] = config.REPRESENTANT_MO_CERFA["nom"]
            champs["D2P_prenom"] = config.REPRESENTANT_MO_CERFA["prenom"]
        else:
            champs["D2N_nom"] = rep
    champs.update(_adresse_demandeur(mo.get("adresse")))
    champs["D3T_telephone"] = _telephone_cerfa(mo.get("telephone"))
    # « Adresse électronique » : DEUX cases, avec un « @ » DÉJÀ IMPRIMÉ entre
    # elles. Y déverser l'adresse entière donnait « urbanisme@next.greenvolt.com
    # @ » sur le formulaire, et la seconde case vide. Partie locale à gauche,
    # domaine à droite (relevé sur le gabarit le 16/09/2026).
    local, _, domaine = (mo.get("email") or "").partition("@")
    if local:
        champs["D5GE1_email"] = local
    if domaine:
        champs["D5GE2_email"] = domaine
        # D5A_acceptation (accepter les actes par voie électronique) N'EST PLUS
        # cochée : c'est un consentement qui engage juridiquement le déclarant.
        # Le module promettait dans son en-tête de ne cocher aucune case à
        # risque, et cochait celle-là. Elle reste à la main du déclarant.
    return champs


# Le formulaire découpe l'adresse du demandeur en cases distinctes, avec des
# longueurs contraintes : numéro (10), voie (40), code postal (5), localité (60).
# L'outil, lui, ne stocke qu'un texte libre. Tout déverser dans « Voie » la
# tronquait à 40 caractères (« Immeuble Le Danica, 21 avenue Georges Pompid »)
# et laissait le code postal et la localité vides, alors que l'information
# était là. Défaut relevé par la relecture du 01/09/2026.
_RE_ADRESSE = re.compile(
    r"^\s*(?P<numero>\d{1,4}\s*(?:bis|ter|quater)?)?\s*"
    r"(?P<voie>.*?)\s*,?\s*"
    r"(?P<cp>\d{5})\s+(?P<localite>[^,]+?)\s*$",
    re.IGNORECASE,
)


# Cases d'adresse, par cadre du formulaire. Le découpage est le même ; seuls
# les noms de champs changent d'un cadre à l'autre.
CASES_ADRESSE_DEMANDEUR = {"numero": "D3N_numero", "voie": "D3V_voie",
                           "code": "D3C_code", "localite": "D3L_localite"}
CASES_ADRESSE_TERRAIN = {"numero": "T2Q_numero", "voie": "T2V_voie",
                         "code": "T2C_code", "localite": "T2L_localite"}


def _decouper_adresse(adresse: str | None, cases: dict) -> dict:
    """Découpe au mieux une adresse libre dans les cases du Cerfa.

    Si le découpage échoue, on ne perd rien : le texte part dans « Voie »,
    tronqué à la longueur du champ, ce que faisait déjà l'ancien code.
    """
    adresse = (adresse or "").strip()
    if not adresse:
        return {}
    m = _RE_ADRESSE.match(adresse)
    if not m:
        return {cases["voie"]: adresse[:40]}
    champs = {
        cases["voie"]: (m.group("voie") or "")[:40],
        cases["code"]: m.group("cp"),
        cases["localite"]: (m.group("localite") or "")[:60],
    }
    numero = (m.group("numero") or "").strip()
    if numero:
        champs[cases["numero"]] = numero[:10]
    return {k: v for k, v in champs.items() if v}


def _adresse_demandeur(adresse: str | None) -> dict:
    """Adresse du maître d'ouvrage, cadre 2 du formulaire."""
    adresse = (adresse or "").strip()
    # L'adresse de Greenvolt Next France est une constante : son découpage est
    # arrêté à la main dans config, parce que la règle générale ci-dessous ne
    # peut pas deviner que « Parc Everest III » est un complément et non la
    # voie — elle l'enverrait dans « Voie », où la case coupe à 40 caractères.
    if adresse == config.MAITRE_OUVRAGE_DEFAUT["adresse"].strip():
        return dict(config.ADRESSE_MO_CERFA)
    return _decouper_adresse(adresse, CASES_ADRESSE_DEMANDEUR)


def _champs_terrain(projet: dict) -> dict:
    """Cadre 3.1 : adresse du terrain, parcelles, superficie totale.

    L'adresse du terrain était déversée ENTIÈRE dans la case « Voie »
    (« 79 Rue Alexandre Bérard 01500 Ambérieu-en-Bugey »), qui coupe à 40
    caractères : le numéro de voie restait vide et la commune partait avec le
    reste. Elle est maintenant découpée comme celle du demandeur, mais le code
    postal et la commune viennent du MODÈLE, pas du découpage : ils sont
    validés par le géocodage, là où la découpe n'est qu'une lecture de texte.
    """
    loc = projet.get("localisation") or {}
    champs = _decouper_adresse(loc.get("adresse"), CASES_ADRESSE_TERRAIN)
    if loc.get("commune"):
        champs["T2L_localite"] = loc["commune"][:60]
    if loc.get("code_postal"):
        champs["T2C_code"] = loc["code_postal"]

    parcelles = (loc.get("parcelles") or [])[:3]  # le gabarit n'a que 3 lignes
    suffixes = ["", "P2", "P3"]
    for parc, sfx in zip(parcelles, suffixes):
        champs[f"T2F{sfx}_prefixe"] = parc.get("com_abs") or "000"
        champs[f"T2S{sfx}_section"] = parc.get("section") or ""
        champs[f"T2N{sfx}_numero"] = parc.get("numero") or ""
        if parc.get("contenance_m2"):
            champs[f"T2T{sfx}_superficie"] = str(parc["contenance_m2"])

    # « Superficie totale du terrain (en m²) », case D5T_total malgré son nom.
    # C'est la somme des contenances cadastrales, que l'outil affiche depuis
    # toujours à l'étape 1 mais ne portait pas au formulaire. Toutes les
    # parcelles comptent, y compris au-delà des 3 lignes du gabarit.
    surfaces = [p["contenance_m2"] for p in (loc.get("parcelles") or [])
                if p.get("contenance_m2")]
    if surfaces:
        champs["D5T_total"] = str(sum(surfaces))
    return champs


def _champs_projet(projet: dict) -> tuple[dict, list[str]]:
    """Cadre « projet » du formulaire. Renvoie (champs, trous signalés).

    Cartographie relevée sur le gabarit officiel (page 7), qui donne le sens
    exact de chaque case et corrige une inversion présente jusqu'au 01/09/2026 :

        C2ZP1_crete        « Indiquez sa puissance crête : ___ kW »
        C2ZR1_destination  « et la destination principale de l'énergie »
        C2ZE1_puissance    « la puissance électrique nécessaire à votre projet »

    Le code écrivait la hauteur hors tout dans la puissance crête, et la
    puissance crête dans la puissance de raccordement.
    """
    omb = projet.get("ombriere") or {}
    trous: list[str] = []
    if not omb.get("famille") and not omb.get("puissance_kwc"):
        return {}, ["À compléter : aucune caractéristique d'ombrière n'est saisie."]

    desc = "Installation d'ombrières photovoltaïques sur le parking existant"

    # Les cotes du catalogue ne sont affirmées QUE si un type a été choisi.
    # Sans famille, catalogue.py retombe silencieusement sur START PLAINE Bas :
    # ses cotes n'ont rien à faire dans un formulaire officiel.
    if omb.get("famille"):
        p = parametres_effectifs(omb)
        dims = (f"{p['longueur_m']:g} m x {p['profondeur_m']:g} m, ".replace(".", ",")
                if p["saisis"]["longueur"] else
                f"{p['profondeur_m']:g} m de profondeur, ".replace(".", ","))
        # La référence commerciale du catalogue (« START PLAINE Bas ») n'a rien
        # à faire dans la nature des travaux : la mairie instruit une structure,
        # pas un produit. Seul le matériau compte (demande du 16/09/2026).
        desc += (
            " : structure en acier galvanisé, "
            f"{dims}"
            f"hauteur hors tout {p['h_haut_m']:.2f} m".replace(".", ",")
        )
        # Pente SAISIE, et non celle que le catalogue recalcule depuis les deux
        # hauteurs : un formulaire réglementaire reprend ce que le déclarant a
        # renseigné. La FVE donnait 6°, le Cerfa affichait 4,8°.
        if omb.get("pente_deg"):
            desc += f", pente {omb['pente_deg']:g} degrés".replace(".", ",")
    else:
        trous.append(
            "À compléter : aucun type d'ombrière n'est choisi (étape 3). "
            "Les dimensions et la hauteur ne sont donc pas portées au formulaire."
        )

    # « full black » était affirmé pour TOUS les projets alors que c'est un
    # choix de produit, démenti sur un dossier réel (16/09/2026). L'aspect des
    # modules n'est pas une donnée de l'outil : on ne le décrit plus.
    desc += ", modules photovoltaïques"
    if omb.get("module_puissance_wc"):
        desc += f" de {omb['module_puissance_wc']:g} Wc".replace(".", ",")
    if omb.get("puissance_kwc"):
        desc += f", puissance {omb['puissance_kwc']:g} kWc".replace(".", ",")
    if omb.get("nb_places"):
        desc += f", {omb['nb_places']} places couvertes"
    desc += "."
    # La conformité à la loi APER est une CONCLUSION JURIDIQUE, pas un fait
    # mesuré. Elle était affirmée en dur sans le moindre test. Elle appartient
    # au déclarant, qui signe : l'outil ne la met plus dans sa bouche.

    champs = {
        "C2ZD1_description": desc,
        "C2ZA1_nouvelle": COCHE,  # une ombrière est une construction nouvelle
    }

    # puissance crête, en kW, dans la case qui la demande. Virgule décimale :
    # le formulaire est français, et le reste du descriptif l'emploie déjà.
    if omb.get("puissance_kwc"):
        champs["C2ZP1_crete"] = f"{omb['puissance_kwc']:g}".replace(".", ",")
    else:
        trous.append("À compléter : la puissance crête (kW) n'est pas renseignée.")

    # Puissance électrique nécessaire au projet (cadre 4.2.1, si > 12 kVA
    # monophasé / 36 kVA triphasé). Elle n'a JAMAIS la même valeur que la
    # puissance crête : c'est la puissance AC, lue sur la FVE dans le bloc
    # « Puissance projet » (lecture_fve.REGLES), et saisissable à l'étape 3.
    if omb.get("puissance_raccordement_kva"):
        champs["C2ZE1_puissance"] = (
            f"{omb['puissance_raccordement_kva']:g} kVA".replace(".", ","))
    else:
        trous.append(
            "À compléter : puissance électrique nécessaire au raccordement "
            "(cadre 4.2.1) — « Puissance AC » de la FVE, ou étape 3."
        )

    from .notice import DESTINATIONS
    destination = DESTINATIONS.get(omb.get("destination_energie"))
    if destination:
        champs["C2ZR1_destination"] = destination["libelle"][:70]
    else:
        trous.append(
            "À compléter : destination principale de l'énergie produite "
            "(cadre 4.2.1) — à choisir à l'étape 3."
        )

    # stationnement inchangé avant / après travaux
    if omb.get("nb_places"):
        champs["S1A_stationnementavant"] = str(omb["nb_places"])
        champs["S1M_stationnementapres"] = str(omb["nb_places"])

    # Cadre 4.3, emprise au sol. Les trois cases sont demandées ; l'outil ne
    # les calcule pas (voir models.Ombriere). Il les porte si elles sont
    # saisies et dit lesquelles manquent. Le zéro est une valeur légitime :
    # d'où le test sur None et non sur la fausseté.
    EMPRISES = [
        ("emprise_avant_m2", "W3ES1_avanttravaux", "emprise au sol avant travaux"),
        ("emprise_creee_m2", "W3ES2_creee", "emprise au sol créée"),
        ("emprise_supprimee_m2", "W3ES3_supprimee", "emprise au sol supprimée"),
    ]
    manquantes = []
    for cle, case, libelle in EMPRISES:
        if omb.get(cle) is None:
            manquantes.append(libelle)
        else:
            champs[case] = f"{omb[cle]:g}".replace(".", ",")
    if manquantes:
        trous.append("À compléter : " + ", ".join(manquantes)
                     + " (cadre 4.3) — étape 3.")
    return champs, trous


# Bordereau de dépôt des pièces jointes (pages 11 à 14 du formulaire, 13 à 16
# du PDF). Relevé le 16/09/2026 en surimprimant les noms de champs sur le
# gabarit : les identifiants ne suivent AUCUN ordre lisible (DPC1 = P5PA2,
# DPC2 = P5PB1, DPC3 = P3GE1), il ne faut donc pas les déduire du numéro.
#
# Correspondance pièce GV_DP -> case du bordereau. Les pièces du formulaire
# sans équivalent dans un dossier d'ombrière (DPC4 plan des façades, DPC5
# aspect extérieur…) restent décochées : « Toutes les pièces ne sont pas à
# joindre systématiquement », dit le formulaire.
#
# DPC6, DPC7 et DPC8 sont sous la condition imprimée « Si vous créez ou
# modifiez une construction VISIBLE DEPUIS L'ESPACE PUBLIC ou si votre projet
# se situe [en secteur protégé] » : une ombrière de parking la remplit.
#
# DPC11 est volontairement ABSENTE, bien que l'outil produise une notice. Sa
# condition imprimée est beaucoup plus étroite : opération de restauration
# immobilière, immeuble inscrit au titre des monuments historiques, site
# patrimonial remarquable, abords de monument historique ou cœur de parc
# national. Un dossier qui relèverait de ces cas serait classé en PC par
# regles.determiner_regime, et le Cerfa DP ne serait pas généré. Cocher cette
# case reviendrait donc à déclarer une situation qui n'existe pas. Elle est
# signalée dans HORS_PORTEE, à cocher par le déclarant s'il joint la notice.
BORDEREAU = {
    "dp1_situation": ("P5PA2", "DPC1 · plan de situation"),
    "dp2": ("P5PB1", "DPC2 · plan de masse coté"),
    "dp3": ("P3GE1", "DPC3 · plan en coupe"),
    "dp6": ("P3GF1", "DPC6 · document graphique d'insertion"),
    "dp7": ("P3GG1", "DPC7 · photo de l'environnement proche"),
    "dp8": ("P3GH1", "DPC8 · photo du paysage lointain"),
}


def _champs_bordereau(statuts: dict[str, str] | None) -> tuple[dict, list[str]]:
    """Coche les pièces RÉELLEMENT prêtes dans le bordereau de dépôt.

    `statuts` vient du moteur réglementaire (regles.completude) : la liste des
    pièces jointes ne doit pas être une deuxième vérité à côté de la checklist
    de l'outil. Sans lui (appel direct, tests), on ne coche rien plutôt que de
    déclarer jointes des pièces dont on ne sait rien.

    Cocher une pièce absente ferait déclarer au demandeur qu'il joint un
    document qu'il ne joint pas : on ne coche que « prete ».
    """
    # Tous les avertissements du module commencent par « À compléter » : c'est
    # ce qui les rend reconnaissables dans l'interface et dans les tests.
    if not statuts:
        return {}, ["À compléter : le bordereau des pièces jointes n'est pas "
                    "coché (statuts des pièces indisponibles), à cocher à la main."]
    champs, absentes = {}, []
    for code, (case, libelle) in BORDEREAU.items():
        if statuts.get(code) == "prete":
            champs[case] = COCHE
        else:
            # DPC1 est la seule pièce obligatoire dans tous les cas (formulaire)
            absentes.append(libelle + (" (OBLIGATOIRE)" if code == "dp1_situation" else ""))
    if absentes:
        return champs, ["À compléter : bordereau des pièces jointes, non cochées "
                        "faute d'être prêtes — " + ", ".join(absentes) + "."]
    return champs, []


def _champs_engagement(projet: dict) -> dict:
    """Cadre d'engagement du déclarant : lieu (commune) + date du jour."""
    loc = projet.get("localisation") or {}
    return {
        "E1L_lieu": loc.get("commune") or "",
        # champ « peigne » de 8 cases (MaxLen=8, drapeau comb) : les séparateurs
        # sont déjà imprimés. « 01/09/2026 » y entrait sur 10 caractères, ce qui
        # doublait les séparateurs et tronquait l'année.
        "E1D_date": date.today().strftime("%d%m%Y"),
    }


# Ce que l'outil ne remplira JAMAIS, et pourquoi. Sans cette liste, la seule
# façon de savoir qu'une case reste vide était de relire les 20 pages du
# formulaire. Elle est rendue à l'interface (étape 5) avec les champs remplis,
# pour que le BE sache exactement ce qui lui reste à faire avant de signer.
HORS_PORTEE = [
    ("Cadre 2", "Type de société (SA, SAS, SCI…)",
     "la forme juridique du maître d'ouvrage n'est pas une donnée de l'outil"),
    ("Cadre 3.2", "Certificat d'urbanisme, lotissement, ZAC, AFU, PUP",
     "situation juridique du terrain : réponses du déclarant, jamais devinées"),
    ("Cadre 4.5", "Surface affectée au stationnement, dont surface bâtie",
     "l'outil connaît le nombre de places, pas les surfaces"),
    ("Bordereau", "Case DPC11 (notice sur les matériaux et les modalités)",
     "sa condition imprimée ne vise que les secteurs protégés et les monuments "
     "historiques — cas qui relèvent du permis de construire, pas de la DP. "
     "Si vous joignez tout de même la notice DP11, cochez-la à la main"),
    ("Cadre 5", "Loi sur l'eau, autorisation environnementale, espèces "
                "protégées, ICPE, réseau de chaleur, autre législation",
     "déclarations engageant le déclarant : à cocher par lui seul"),
    ("Cadre 6", "Participation pour voirie et réseaux (PVR)",
     "ne concerne que les terrains soumis à PVR, information hors outil"),
    ("Cadre 7", "Signature",
     "elle appartient au déclarant ; le PDF est un brouillon à relire"),
]


def rapport(champs: dict, avertissements: list[str]) -> dict:
    """Bilan lisible du pré-remplissage, pour l'interface.

    `remplis` : ce que l'outil a porté au formulaire, par cadre.
    `a_completer` : ce qu'il aurait pu remplir mais dont la donnée manque.
    `hors_portee` : ce qu'il ne remplira jamais, avec la raison.
    """
    return {
        "remplis": len(champs),
        "champs": sorted(champs),
        "a_completer": list(avertissements),
        "hors_portee": [{"cadre": c, "quoi": q, "pourquoi": p}
                        for c, q, p in HORS_PORTEE],
    }


def preremplir(projet: dict, statuts_pieces: dict[str, str] | None = None):
    """Remplit le gabarit et l'écrit dans les assets du projet.

    Renvoie (chemin, champs remplis, avertissements). Refuse un projet en
    régime PC : le 16702 est le formulaire de la DÉCLARATION PRÉALABLE, le
    générer pour un PC produirait un dossier erroné.

    `statuts_pieces` (code de pièce -> statut, cf. regles.completude) sert à
    cocher le bordereau de dépôt des pièces jointes. Facultatif : sans lui,
    aucune case du bordereau n'est cochée.
    """
    if not GABARIT.exists():
        raise FileNotFoundError(
            "Gabarit Cerfa absent (app/gabarits/cerfa_16702.pdf)."
        )
    from . import regles  # local : évite un cycle d'import
    reg = regles.determiner_regime(
        (projet.get("ombriere") or {}).get("puissance_kwc"),
        (projet.get("urbanisme") or {}).get("secteur_abf"),
    )
    if reg["regime"] != regles.REGIME_DP:
        raise ValueError(
            "Le projet relève du Permis de Construire (" + " ; ".join(reg["raisons"])
            + ") : le Cerfa DP 16702 ne s'applique pas."
        )

    avertissements: list[str] = []
    toutes_parcelles = (projet.get("localisation") or {}).get("parcelles") or []
    if len(toutes_parcelles) > 3:
        avertissements.append(
            f"{len(toutes_parcelles)} parcelles : seules les 3 premières figurent "
            "sur le formulaire (3 lignes), joignez la liste complète sur papier "
            "libre — la notice les mentionne toutes."
        )

    champs_projet, trous = _champs_projet(projet)
    avertissements.extend(trous)
    champs_bordereau, trous_bordereau = _champs_bordereau(statuts_pieces)
    avertissements.extend(trous_bordereau)

    champs = {
        **_champs_demandeur(projet),
        **_champs_terrain(projet),
        **champs_projet,
        **champs_bordereau,
        **_champs_engagement(projet),
    }
    champs = {k: v for k, v in champs.items() if v}

    reader = PdfReader(GABARIT)
    writer = PdfWriter()
    writer.append(reader)
    for page in writer.pages:
        writer.update_page_form_field_values(page, champs, auto_regenerate=False)
    try:  # afficher les valeurs dans tous les lecteurs PDF
        writer.set_need_appearances_writer(True)
    except AttributeError:  # pragma: no cover - selon version pypdf
        pass

    chemin = config.assets_dir(projet["id"]) / "cerfa_16702_prerempli.pdf"
    with open(chemin, "wb") as f:
        writer.write(f)
    return chemin, sorted(champs), avertissements
