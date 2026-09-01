"""Filet de sécurité du SOCLE, avant le retrait du module Insertion.

Contexte : le bloc Insertion (insertion_ia, routes_insertion, implantation,
perspective, scaffold, fiche_emprise, étape 4 du wizard) va être supprimé, soit
environ la moitié du code. Les quatre modules couverts ici produisent les vrais
livrables du dossier DP et NE DOIVENT PAS bouger pendant cette amputation :

    app/regles.py            les 11 pièces du dossier et leurs statuts
    app/catalogue.py         les cotes des structures et le drapeau "saisis"
    app/planches/base.py     le canvas, la zone utile, l'échelle A3
    app/planches/cartes.py   le cadrage et l'échelle des planches DP1
    app/planches/ombriere.py la coupe paramétrique DP3

Chaque test vise une régression plausible du retrait : une pièce qui disparaît
de la checklist, une cote inventée qui remonte dans le Cerfa, un dessin qui
cesse de dépendre du catalogue, un cadrage de carte qui change d'échelle.

Aucun test n'appelle le réseau (le fixture `reseau_interdit` le garantit) et
aucun n'écrit dans PROJETS (redirigé vers tmp_path par `projets_isoles`).
"""
from __future__ import annotations

import ast
from pathlib import Path

import httpx
import pytest
from PIL import Image
from pyproj import Transformer

from app import config, regles
from app.catalogue import CATALOGUE, libelle_coupe, parametres_effectifs
from app.models import MaitreOuvrage, Notice, Projet
from app.planches import GENERATEURS, base, cartes
from app.planches.ombriere import dessiner_coupe
from app.regles import PIECES_DP, completude


# --------------------------------------------------------------- garde-fous


@pytest.fixture(autouse=True)
def projets_isoles(tmp_path, monkeypatch):
    """Jamais d'écriture dans le vrai PROJETS/ (données client réelles)."""
    monkeypatch.setattr(config, "PROJETS_DIR", tmp_path / "PROJETS")


@pytest.fixture(autouse=True)
def reseau_interdit(monkeypatch):
    """Le socle doit se tester hors ligne : tout appel HTTP fait échouer le test.

    cartes.py appelle le WMS Géoplateforme via httpx ; les tests de cadrage
    remplacent `_getmap`, ce garde-fou vérifie qu'aucun chemin n'échappe au
    remplacement (sinon le test deviendrait dépendant du réseau sans qu'on le
    remarque).
    """
    def _interdit(*args, **kwargs):
        raise AssertionError("appel réseau interdit dans ce fichier de tests")

    monkeypatch.setattr(httpx, "Client", _interdit)
    monkeypatch.setattr(httpx, "get", _interdit)


# ============================================================================
# app/regles.py : les 11 pièces du dossier DP
# ============================================================================

# Table de référence recopiée depuis le plan (R.431-36 code de l'urbanisme,
# DP4 retirée le 17/07/2026). Elle est volontairement dupliquée ici : si
# quelqu'un modifie PIECES_DP en retirant l'insertion, le test doit le dire.
PIECES_ATTENDUES = [
    ("garde", "Page de garde", "auto"),
    ("dp1_situation", "DP1 · Plan de situation", "auto"),
    ("dp1_cadastral", "DP1 · Plan cadastral", "auto"),
    ("dp1_aerien", "DP1 · Vue aérienne", "auto"),
    ("dp2", "DP2 · Plan de masse", "mixte"),
    ("dp3", "DP3 · Plan en coupe", "be"),
    ("dp6", "DP6 · Insertion paysagère", "be"),
    ("dp7", "DP7 · Photo environnement proche", "be"),
    ("dp8", "DP8 · Photo paysage lointain", "be"),
    ("dp11", "DP11 · Notice descriptive", "auto"),
    ("cerfa", "Cerfa 16702 (DP) pré-rempli", "auto"),
]


def test_les_onze_pieces_du_dossier_gardent_codes_titres_modes_et_ordre():
    """Le dossier DP compte 11 pièces, dans cet ordre exact.

    DP6 s'appelle « insertion paysagère » : c'est une pièce du BE, elle n'a
    rien à voir avec le module Insertion IA qui est retiré. Le risque de
    confusion pendant l'amputation est réel, d'où ce verrou.
    """
    # Arrange / Act
    reel = [(p["code"], p["titre"], p["mode"]) for p in PIECES_DP]

    # Assert
    assert reel == PIECES_ATTENDUES
    assert len({c for c, _, _ in reel}) == 11  # pas de doublon de code


def test_dp6_reste_une_piece_du_bureau_d_etudes():
    """DP6 est un upload BE, jamais une image produite par le module IA.

    Après le retrait, DP6 doit rester en mode "be" : si elle basculait en
    "auto", le dossier annoncerait une pièce générée qui n'existe plus.
    """
    dp6 = next(p for p in PIECES_DP if p["code"] == "dp6")
    assert dp6["mode"] == "be"

    projet = Projet(nom="Test")
    statuts = {p["code"]: p["statut"] for p in completude(projet)["pieces"]}
    assert statuts["dp6"] == "en_attente"


def test_modes_repartis_entre_generation_auto_et_upload_be():
    """4 pièces générées par le socle, 1 mixte (DP2), 5 attendues du BE."""
    par_mode: dict[str, list[str]] = {}
    for piece in PIECES_DP:
        par_mode.setdefault(piece["mode"], []).append(piece["code"])

    assert par_mode["auto"] == ["garde", "dp1_situation", "dp1_cadastral",
                                "dp1_aerien", "dp11", "cerfa"]
    assert par_mode["mixte"] == ["dp2"]
    assert par_mode["be"] == ["dp3", "dp6", "dp7", "dp8"]


def test_statut_page_de_garde_exige_le_nom_et_le_maitre_d_ouvrage():
    """La garde est « prête » seulement quand elle a de quoi être imprimée."""
    # Arrange
    sans_mo = Projet(nom="Soufflenheim")
    avec_mo = Projet(nom="Soufflenheim",
                     mo=MaitreOuvrage(raison_sociale="Greenvolt Next France"))

    # Act
    st_sans = {p["code"]: p["statut"] for p in completude(sans_mo)["pieces"]}
    st_avec = {p["code"]: p["statut"] for p in completude(avec_mo)["pieces"]}

    # Assert
    assert st_sans["garde"] == "a_completer"
    assert st_avec["garde"] == "prete"


@pytest.mark.parametrize(
    "sections, valide, statut_attendu",
    [
        ({}, False, "a_generer"),                       # rien de rédigé
        ({"presentation": "   "}, False, "a_generer"),  # blancs = vide
        ({"presentation": "Texte."}, False, "a_completer"),  # brouillon à relire
        ({"presentation": "Texte."}, True, "prete"),         # relue et validée
    ],
)
def test_statut_dp11_suit_le_cycle_brouillon_relecture_validation(
        sections, valide, statut_attendu):
    """La notice n'est « prête » qu'après validation humaine explicite.

    C'est le garde-fou anti-IA du dossier : un brouillon généré ne doit jamais
    partir en préfecture sans relecture. Ce cycle vit dans le socle, pas dans
    le module retiré.
    """
    projet = Projet(nom="Test",
                    notice=Notice(sections=sections, valide_humain=valide))

    statuts = {p["code"]: p["statut"] for p in completude(projet)["pieces"]}

    assert statuts["dp11"] == statut_attendu


def test_cerfa_reste_a_generer_tant_qu_il_n_a_pas_ete_produit():
    """Même dossier complet par ailleurs, le Cerfa se produit à l'étape 7."""
    projet = Projet(
        nom="Soufflenheim",
        mo=MaitreOuvrage(raison_sociale="Greenvolt Next France"),
        notice=Notice(sections={"presentation": "Texte."}, valide_humain=True),
    )

    statuts = {p["code"]: p["statut"] for p in completude(projet)["pieces"]}

    assert statuts["cerfa"] == "a_generer"


def test_piece_fournie_par_le_be_est_comptee_prete():
    """Un upload BE enregistré dans projet.documents bascule la pièce à prête.

    Comportement nominal à préserver : c'est ainsi que DP2/DP3/DP6/DP7/DP8
    sortent de l'état « en attente ».
    """
    # Arrange : le fichier existe réellement sous PROJETS/
    relatif = "p-test.assets/uploads/dp3.pdf"
    chemin = config.PROJETS_DIR / relatif
    chemin.parent.mkdir(parents=True, exist_ok=True)
    chemin.write_bytes(b"%PDF-1.4 factice")
    projet = Projet(nom="Test", id="p-test",
                    documents={"dp3": {"nom_fichier": "coupe.pdf",
                                       "fichier": relatif, "taille": 16}})

    # Act
    resultat = completude(projet)
    statuts = {p["code"]: p["statut"] for p in resultat["pieces"]}

    # Assert
    assert statuts["dp3"] == "prete"
    assert resultat["pretes"] == 1
    assert resultat["total"] == 11


@pytest.mark.xfail(
    strict=True,
    reason="DÉFAUT CONNU (non corrigé) : regles._statut_piece bascule une pièce "
           "à « prete » sur la seule présence de son code dans projet.documents, "
           "sans vérifier que le fichier existe encore sous PROJETS/. Un dossier "
           "dont un upload a été effacé ou déplacé affiche donc 11/11 pièces "
           "prêtes alors que l'assemblage, lui, ne trouvera rien "
           "(assemblage._fichier_document renvoie None) et posera un "
           "placeholder. Ce test décrit le comportement ATTENDU : il doit "
           "passer au vert le jour où le contrôle disque sera ajouté, et le "
           "marqueur xfail devra alors être retiré. Il n'est PAS un blanc-seing "
           "donné au comportement actuel.",
)
def test_completude_ne_devrait_pas_annoncer_prete_une_piece_dont_le_fichier_manque():
    # Arrange : les 11 pièces déclarées fournies, mais un seul fichier absent
    # du disque (cas réel : dossier client rangé à la main, OneDrive non
    # synchronisé, purge d'un assets/).
    documents = {}
    for code, _, _ in PIECES_ATTENDUES:
        relatif = f"p-test.assets/uploads/{code}.pdf"
        documents[code] = {"nom_fichier": f"{code}.pdf", "fichier": relatif}
        if code == "dp3":
            continue  # le fichier de la coupe n'est jamais écrit
        chemin = config.PROJETS_DIR / relatif
        chemin.parent.mkdir(parents=True, exist_ok=True)
        chemin.write_bytes(b"%PDF-1.4 factice")
    projet = Projet(nom="Test", id="p-test", documents=documents)

    # Act
    resultat = completude(projet)

    # Assert : 10 pièces réellement disponibles sur 11
    assert resultat["pretes"] == 10


# ---- indépendance du socle vis-à-vis du bloc supprimé ----------------------

MODULES_SUPPRIMES = {
    "insertion_ia", "routes_insertion", "implantation",
    "perspective", "scaffold", "fiche_emprise",
}

FICHIERS_SOCLE = [
    "app/regles.py",
    "app/catalogue.py",
    "app/planches/__init__.py",
    "app/planches/base.py",
    "app/planches/cartes.py",
    "app/planches/ombriere.py",
]


def _noms_importes(chemin: Path) -> set[str]:
    """Tous les identifiants apparaissant dans un import, y compris locaux."""
    arbre = ast.parse(chemin.read_text(encoding="utf-8"))
    noms: set[str] = set()
    for noeud in ast.walk(arbre):
        if isinstance(noeud, ast.Import):
            noms.update(alias.name.split(".")[-1] for alias in noeud.names)
        elif isinstance(noeud, ast.ImportFrom):
            if noeud.module:
                noms.update(noeud.module.split("."))
            noms.update(alias.name for alias in noeud.names)
    return noms


@pytest.mark.parametrize("relatif", FICHIERS_SOCLE)
def test_le_socle_n_importe_aucun_module_du_bloc_insertion(relatif):
    """Preuve que le retrait ne peut pas casser ces fichiers par import.

    Les imports locaux (à l'intérieur d'une fonction) comptent aussi : c'est
    justement par là que les dépendances cachées passent.
    """
    chemin = config.REPO_ROOT / relatif
    fautifs = _noms_importes(chemin) & MODULES_SUPPRIMES

    assert not fautifs, f"{relatif} dépend de {sorted(fautifs)}"


def test_generateurs_de_planches_couvrent_exactement_les_trois_pieces_dp1():
    """Le lien regles <-> planches : chaque générateur porte un code de pièce.

    assemblage.py boucle sur GENERATEURS pour régénérer les planches DP1 ; une
    clé orpheline produirait une planche sans pièce correspondante.
    """
    codes_pieces = {p["code"] for p in PIECES_DP}

    assert set(GENERATEURS) == {"dp1_situation", "dp1_cadastral", "dp1_aerien"}
    assert set(GENERATEURS) <= codes_pieces


# ============================================================================
# app/catalogue.py : cotes réelles et traçabilité des valeurs fabriquées
# ============================================================================

# Cotes relevées sur les coupes commerciales Solstyce le 13/07/2026 (cf.
# docstring de app/catalogue.py). Ce sont elles qui atterrissent dans le Cerfa
# et la notice : elles doivent survivre au retrait à la virgule près.
COTES_ATTENDUES = {
    "START PLAINE Bas": {"poteau": "haut", "double": False, "profondeur_m": 5.0,
                         "h_haut_m": 3.47, "h_bas_m": 3.06, "pente_deg": 5.0},
    "START PLAINE Haut": {"poteau": "bas", "double": False, "profondeur_m": 5.0,
                          "h_haut_m": 3.92, "h_bas_m": 3.51, "pente_deg": 5.0},
    "START PLAINE Double": {"poteau": "central", "double": True, "profondeur_m": 10.0,
                            "h_haut_m": 3.92, "h_bas_m": 2.99, "pente_deg": 5.0},
}


@pytest.mark.parametrize("famille", sorted(COTES_ATTENDUES))
def test_cotes_du_catalogue_restent_celles_des_coupes_solstyce(famille):
    attendu = COTES_ATTENDUES[famille]
    entree = CATALOGUE[famille]

    for cle, valeur in attendu.items():
        assert entree[cle] == valeur, f"{famille}.{cle}"


def test_catalogue_ne_contient_que_les_trois_familles_start_plaine():
    assert set(CATALOGUE) == set(COTES_ATTENDUES)


def test_repli_sur_le_type_bas_quand_la_famille_est_absente():
    """Ombrière non renseignée : le socle sert des cotes réelles, pas des zéros.

    Les planches et la notice appellent parametres_effectifs très tôt dans le
    wizard, avant que le type ne soit choisi : le repli doit rester silencieux
    et cohérent.
    """
    # Act
    p = parametres_effectifs({})

    # Assert
    assert p["famille"] == "START PLAINE Bas"
    assert p["poteau"] == "haut"
    assert p["double"] is False
    assert p["profondeur_m"] == CATALOGUE["START PLAINE Bas"]["profondeur_m"]
    assert p["h_bas_m"] == CATALOGUE["START PLAINE Bas"]["h_bas_m"]
    assert p["pente_deg"] == CATALOGUE["START PLAINE Bas"]["pente_deg"]
    # h_haut_m n'est volontairement pas vérifié ici : il est recalculé depuis
    # h_bas et la pente au lieu d'être repris du catalogue (divergence décrite
    # par test_hauteur_hors_tout_par_defaut_devrait_valoir_la_cote_relevee).


def test_famille_inconnue_retombe_sur_la_geometrie_du_type_bas():
    """Projet ancien ou clé de type renommée : aucune exception, cotes du Bas.

    On verrouille ici la seule chose souhaitable : ne pas planter et servir une
    géométrie issue du catalogue. La façon dont le nom inconnu est ensuite
    répété tel quel dans les livrables est un point signalé au rapport, pas un
    contrat.
    """
    p = parametres_effectifs({"famille": "START PLAINE Inexistante"})

    assert p["poteau"] == CATALOGUE["START PLAINE Bas"]["poteau"]
    assert p["profondeur_m"] == CATALOGUE["START PLAINE Bas"]["profondeur_m"]
    assert p["h_bas_m"] == CATALOGUE["START PLAINE Bas"]["h_bas_m"]


@pytest.mark.xfail(
    strict=True,
    reason="INCOHÉRENCE CONNUE (non corrigée) : le catalogue déclare les cotes "
           "relevées sur les coupes Solstyce (Bas 3,47 m ; Haut 3,92 m ; "
           "Double 3,92 m) mais parametres_effectifs ne les sert jamais tant "
           "que l'utilisateur ne saisit pas hauteur_hors_tout_m : il recalcule "
           "h_haut = h_bas + profondeur x tan(pente), ce qui donne 3,50 / 3,95 "
           "/ 3,86 m. Le quadruplet du catalogue est lui-même incohérent (la "
           "pente qui relierait les deux hauteurs vaut 4,69° en Mono et 5,31° "
           "en Double, pas 5,0°). Conséquence : la hauteur hors tout déclarée "
           "au Cerfa (C2ZP1_crete) et dans la notice s'écarte de 3 à 6 cm de "
           "la coupe du fabricant. Le test passera au vert que l'on corrige la "
           "pente du catalogue ou que l'on serve la cote relevée ; l'arbitrage "
           "revient à Florent et ne doit pas être tranché en silence pendant "
           "le retrait de l'insertion.",
)
@pytest.mark.parametrize("famille", sorted(CATALOGUE))
def test_hauteur_hors_tout_par_defaut_devrait_valoir_la_cote_relevee(famille):
    p = parametres_effectifs({"famille": famille})

    assert p["h_haut_m"] == pytest.approx(CATALOGUE[famille]["h_haut_m"], abs=0.005)


def test_les_defauts_fabriques_ne_sont_jamais_marques_comme_saisis():
    """CRITIQUE Cerfa : 4 travées x 5 m = 20 m sont des valeurs de dessin.

    Elles existent (les planches en ont besoin) mais "saisis" doit rester à
    False pour que cerfa.py et notice.py refusent de les affirmer. C'est la
    règle « aucune donnée inventée » (plan §13).
    """
    # Act
    p = parametres_effectifs({"famille": "START PLAINE Bas"})

    # Assert : les valeurs fabriquées sont bien là...
    assert p["nb_travees"] == 4
    assert p["entraxe_m"] == 5.0
    assert p["longueur_m"] == 20.0
    # ... mais aucune n'est présentée comme saisie par l'utilisateur
    assert p["saisis"] == {"longueur": False, "nb_travees": False, "entraxe": False}


@pytest.mark.parametrize(
    "ombriere, longueur_affirmable, longueur_calculee",
    [
        # longueur saisie directement : affirmable
        ({"longueur_m": 33.0}, True, 33.0),
        # travées ET entraxe saisis : la longueur en découle, affirmable
        ({"nb_travees": 6, "entraxe_m": 5.5}, True, 33.0),
        # travées seules : l'entraxe est un défaut de 5 m, donc NON affirmable
        ({"nb_travees": 6}, False, 30.0),
        # entraxe seul : le nombre de travées est un défaut, NON affirmable
        ({"entraxe_m": 5.5}, False, 22.0),
    ],
)
def test_table_de_verite_du_drapeau_longueur(ombriere, longueur_affirmable,
                                             longueur_calculee):
    """La longueur n'est « saisie » que si toutes ses sources le sont.

    Le cas « travées seules » est le piège : la longueur est calculée et a
    l'air crédible, mais elle repose sur un entraxe de 5 m que personne n'a
    confirmé. Le Cerfa doit alors se rabattre sur la seule profondeur.
    """
    p = parametres_effectifs({"famille": "START PLAINE Bas", **ombriere})

    assert p["saisis"]["longueur"] is longueur_affirmable
    assert p["longueur_m"] == pytest.approx(longueur_calculee)


def test_drapeaux_travees_et_entraxe_suivent_la_saisie():
    """notice.py ne mentionne « N travées de X m » que si les deux sont saisis."""
    ni_l_un_ni_l_autre = parametres_effectifs({"famille": "START PLAINE Bas"})
    les_deux = parametres_effectifs({"famille": "START PLAINE Bas",
                                     "nb_travees": 7, "entraxe_m": 4.5})

    assert ni_l_un_ni_l_autre["saisis"]["nb_travees"] is False
    assert ni_l_un_ni_l_autre["saisis"]["entraxe"] is False
    assert les_deux["saisis"]["nb_travees"] is True
    assert les_deux["saisis"]["entraxe"] is True
    assert les_deux["nb_travees"] == 7
    assert les_deux["entraxe_m"] == 4.5


@pytest.mark.parametrize(
    "famille, libelle",
    [("START PLAINE Bas", "Mono Bas"),
     ("START PLAINE Haut", "Mono Haut"),
     ("START PLAINE Double", "Double")],
)
def test_libelle_coupe_traduit_les_cles_internes(famille, libelle):
    """Les clés internes sont rétro-compatibles, l'UI et la DP3 voient le libellé."""
    assert libelle_coupe(famille) == libelle


def test_libelle_coupe_tolere_l_absence_de_famille():
    assert libelle_coupe(None) == "—"
    assert libelle_coupe("") == "—"
    # famille hors catalogue : renvoyée telle quelle, jamais d'exception
    assert libelle_coupe("Type maison") == "Type maison"


# ============================================================================
# app/planches/ombriere.py : la coupe paramétrique DP3
# ============================================================================

PROJET_COUPE = {
    "nom": "Soufflenheim",
    "localisation": {"commune": "Soufflenheim", "code_insee": "67473"},
}


def _couleurs(img: Image.Image) -> set[tuple[int, int, int]]:
    return {couleur for _, couleur in img.getcolors(maxcolors=2 ** 24)}


@pytest.mark.parametrize("famille", sorted(CATALOGUE))
def test_coupe_dp3_se_dessine_hors_ligne_pour_chaque_famille(famille):
    """La DP3 provisoire est la seule pièce technique que le socle sait produire.

    Elle doit rester générable sans réseau et sans le module Insertion : c'est
    elle qui sauve le dossier quand le BE n'a pas encore livré sa coupe.
    """
    # Arrange
    projet = {**PROJET_COUPE, "ombriere": {"famille": famille}}

    # Act
    img = dessiner_coupe(projet)

    # Assert : format planche et éléments de charte réellement dessinés
    assert img.size == (base.PLATE_W, base.PLATE_H)
    couleurs = _couleurs(img)
    assert base.PANNEAU in couleurs, "modules full black absents du dessin"
    assert base.BLEU_BANDE in couleurs, "bandes bleues du mât absentes"
    assert base.NAVY in couleurs, "cotes ou titre absents"


def test_coupe_dp3_differe_selon_la_famille_choisie():
    """Le dessin est piloté par le catalogue, pas figé sur un type unique.

    Régression visée : un raccourci qui dessinerait toujours la même coupe
    passerait inaperçu à l'oeil sur un seul projet.
    """
    images = {}
    for famille in CATALOGUE:
        projet = {**PROJET_COUPE, "ombriere": {"famille": famille}}
        images[famille] = dessiner_coupe(projet).tobytes()

    assert len(set(images.values())) == 3, "deux familles produisent le même dessin"


def test_coupe_dp3_suit_les_hauteurs_saisies():
    """Les cotes saisies par l'utilisateur redessinent la coupe."""
    # Arrange
    base_omb = {"famille": "START PLAINE Bas"}
    haute = {**base_omb, "garde_au_sol_m": 2.5, "hauteur_hors_tout_m": 5.4}

    # Act
    ref = dessiner_coupe({**PROJET_COUPE, "ombriere": base_omb}).tobytes()
    modifiee = dessiner_coupe({**PROJET_COUPE, "ombriere": haute}).tobytes()

    # Assert
    assert ref != modifiee


def test_coupe_dp3_ne_pose_pas_de_cartouche():
    """Le cartouche est posé par le PPTX : la planche doit rester nue.

    Un double cartouche est la régression classique quand on touche à base.py.
    """
    img = dessiner_coupe({**PROJET_COUPE,
                          "ombriere": {"famille": "START PLAINE Bas"}})

    # la bande dégradée du cartouche démarrerait en vert à cette hauteur
    assert img.getpixel((0, base.PLATE_H - base.CARTOUCHE_H)) != base.VERT


# ============================================================================
# app/planches/base.py : canvas, zone utile, échelle A3
# ============================================================================


def test_zone_utile_reserve_la_place_du_cartouche_quand_il_est_demande():
    """content_box est le contrat géométrique dont dépendent cartes et ombriere."""
    sans = base.Planche("Test", {}, cartouche=False)
    avec = base.Planche("Test", {}, cartouche=True)

    assert sans.content_box == (base.MARGE, base.MARGE,
                                base.PLATE_W - base.MARGE,
                                base.PLATE_H - base.MARGE)
    assert avec.content_box == (base.MARGE, base.MARGE,
                                base.PLATE_W - base.MARGE,
                                base.PLATE_H - base.CARTOUCHE_H - base.MARGE)


def test_coller_contenu_redimensionne_le_fond_a_la_zone_utile():
    """Le WMS plafonne à 2048 px : le fond revient plus petit et doit être étiré.

    Sans ce redimensionnement, la carte serait collée en haut à gauche et
    l'échelle graphique mentirait.
    """
    # Arrange
    planche = base.Planche("Test", {})
    x0, y0, x1, y1 = planche.content_box
    petit = Image.new("RGB", ((x1 - x0) // 2, (y1 - y0) // 2), (10, 20, 30))

    # Act
    planche.coller_contenu(petit)

    # Assert : la couleur du fond couvre bien les deux coins de la zone utile
    assert planche.img.getpixel((x0 + 2, y0 + 2)) == (10, 20, 30)
    assert planche.img.getpixel((x1 - 2, y1 - 2)) == (10, 20, 30)


def test_echelle_nominale_correspond_a_une_impression_a3():
    """L'échelle « 1/X » annoncée est celle d'un A3 de 420 mm de large.

    Elle est calculée depuis PX_PAR_MM : si la taille de planche ou le format
    papier changent, toutes les planches DP1 mentent sur leur échelle.
    """
    m_par_px_1_10000 = 10000 / base.PX_PAR_MM / 1000.0

    assert base.echelle_nominale(m_par_px_1_10000) == "1/10 000"
    assert base.PX_PAR_MM == pytest.approx(base.PLATE_W / base.A3_LARGEUR_MM)


def test_bande_degradee_va_du_vert_au_violet():
    """gradient_h n'est plus appelé que par le cartouche après le retrait.

    Il sert aussi la page de garde du PPTX : le supprimer avec le module
    Insertion casserait la charte du dossier.
    """
    bande = base.gradient_h(100, 4)

    assert bande.getpixel((0, 0)) == base.VERT
    assert bande.getpixel((99, 0)) == base.VIOLET


# ============================================================================
# app/planches/cartes.py : cadrage et échelle des planches DP1 (hors ligne)
# ============================================================================

_L93_VERS_WGS84 = Transformer.from_crs(2154, 4326, always_xy=True)
CENTRE_L93 = (1_043_000.0, 6_855_000.0)   # nord de l'Alsace, zone Soufflenheim
LARGEUR_UTILE_PX = base.PLATE_W - 2 * base.MARGE
HAUTEUR_UTILE_PX = base.PLATE_H - 2 * base.MARGE


def _carre_wgs84(cote_m: float, centre=CENTRE_L93) -> dict:
    """Polygone GeoJSON WGS84 d'un carré de `cote_m` mètres en Lambert-93."""
    cx, cy = centre
    d = cote_m / 2
    coins = [(cx - d, cy - d), (cx + d, cy - d), (cx + d, cy + d),
             (cx - d, cy + d), (cx - d, cy - d)]
    return {"type": "Polygon",
            "coordinates": [[list(_L93_VERS_WGS84.transform(x, y)) for x, y in coins]]}


def _projet_carte(parcelles=None, lon=None, lat=None) -> dict:
    return {"nom": "Soufflenheim",
            "localisation": {"commune": "Soufflenheim", "code_insee": "67473",
                             "lon": lon, "lat": lat,
                             "parcelles": parcelles or []}}


def _echelle_reelle(appel: dict) -> float:
    """Échelle déduite du cadrage demandé au WMS, pour une impression A3."""
    largeur_terrain_m = appel["bbox"][2] - appel["bbox"][0]
    largeur_papier_m = appel["w_px"] / base.PX_PAR_MM / 1000.0
    return largeur_terrain_m / largeur_papier_m


@pytest.fixture()
def wms_factice(monkeypatch):
    """Remplace le GetMap Géoplateforme et enregistre les cadrages demandés."""
    appels: list[dict] = []

    def _faux_getmap(couche, bbox, w_px, h_px):
        appels.append({"couche": couche, "bbox": bbox, "w_px": w_px, "h_px": h_px})
        return Image.new("RGB", (w_px, h_px), (208, 208, 202))

    monkeypatch.setattr(cartes, "_getmap", _faux_getmap)
    return appels


def test_plan_de_situation_demande_le_plan_ign_au_1_10000(wms_factice):
    """DP1a : couche, échelle et zone utile demandées au WMS.

    L'échelle 1/10 000 du plan de situation est une attente de l'instructeur ;
    elle est fixée en dur ici et ne doit pas dériver.
    """
    # Arrange
    projet = _projet_carte(parcelles=[{"geometry": _carre_wgs84(120)}])

    # Act
    img = cartes.planche_situation(projet)

    # Assert
    assert img.size == (base.PLATE_W, base.PLATE_H)
    assert len(wms_factice) == 1
    appel = wms_factice[0]
    assert appel["couche"] == config.LAYER_PLAN
    assert (appel["w_px"], appel["h_px"]) == (LARGEUR_UTILE_PX, HAUTEUR_UTILE_PX)
    assert _echelle_reelle(appel) == pytest.approx(10000, rel=1e-9)
    # le cadrage garde le rapport de la planche (pas de déformation)
    hauteur = appel["bbox"][3] - appel["bbox"][1]
    largeur = appel["bbox"][2] - appel["bbox"][0]
    assert hauteur / largeur == pytest.approx(HAUTEUR_UTILE_PX / LARGEUR_UTILE_PX)


def test_cadrage_centre_sur_le_centroide_des_parcelles(wms_factice):
    """Les parcelles priment sur le point géocodé pour centrer la planche.

    Le point géocodé est volontairement placé loin (Paris) : s'il l'emportait,
    la planche montrerait la mauvaise commune.
    """
    # Arrange
    projet = _projet_carte(parcelles=[{"geometry": _carre_wgs84(200)}],
                           lon=2.3522, lat=48.8566)

    # Act
    cartes.planche_situation(projet)

    # Assert
    bbox = wms_factice[0]["bbox"]
    centre_x = (bbox[0] + bbox[2]) / 2
    centre_y = (bbox[1] + bbox[3]) / 2
    assert centre_x == pytest.approx(CENTRE_L93[0], abs=1.0)
    assert centre_y == pytest.approx(CENTRE_L93[1], abs=1.0)


def test_cadrage_utilise_le_point_geocode_sans_parcelle(wms_factice):
    """Avant la sélection cadastrale, l'adresse géocodée suffit à cadrer."""
    lon, lat = _L93_VERS_WGS84.transform(*CENTRE_L93)
    projet = _projet_carte(lon=lon, lat=lat)

    cartes.planche_situation(projet)

    bbox = wms_factice[0]["bbox"]
    assert (bbox[0] + bbox[2]) / 2 == pytest.approx(CENTRE_L93[0], abs=1.0)
    assert (bbox[1] + bbox[3]) / 2 == pytest.approx(CENTRE_L93[1], abs=1.0)


def test_localisation_absente_leve_une_erreur_explicite():
    """Sans localisation, on refuse de dessiner plutôt que de cadrer au hasard."""
    with pytest.raises(ValueError, match="Localisation absente"):
        cartes.planche_situation(_projet_carte())


@pytest.mark.parametrize(
    "cote_m, echelle_attendue",
    [
        (100, 500),      # petite parcelle : la plus grande échelle possible
        (200, 1000),
        (400, 2000),
        (550, 2500),
        (1000, 5000),
        (3000, 5000),    # au-delà, on plafonne sur la dernière échelle ronde
    ],
)
def test_echelle_du_plan_cadastral_s_adapte_a_l_etendue(wms_factice, cote_m,
                                                       echelle_attendue):
    """DP1b : la plus petite échelle ronde qui laisse de l'air autour du terrain.

    Marge de respiration : la parcelle occupe au plus 1/1,8 de la largeur A3.
    """
    projet = _projet_carte(parcelles=[{"geometry": _carre_wgs84(cote_m)}])

    cartes.planche_cadastrale(projet)

    assert _echelle_reelle(wms_factice[0]) == pytest.approx(echelle_attendue, rel=1e-9)


def test_echelle_par_defaut_sans_geometrie_de_parcelle(wms_factice):
    """Parcelle saisie à la main (sans géométrie) : repli sur le 1/2 000."""
    lon, lat = _L93_VERS_WGS84.transform(*CENTRE_L93)
    projet = _projet_carte(parcelles=[{"section": "30", "numero": "0464"}],
                           lon=lon, lat=lat)

    cartes.planche_cadastrale(projet)

    assert _echelle_reelle(wms_factice[0]) == pytest.approx(2000, rel=1e-9)


@pytest.mark.parametrize(
    "fonction, couche",
    [("planche_cadastrale", "LAYER_PARCELLES"),
     ("planche_aerienne", "LAYER_ORTHO")],
)
def test_chaque_planche_dp1_interroge_sa_propre_couche(wms_factice, fonction,
                                                       couche):
    projet = _projet_carte(parcelles=[{"geometry": _carre_wgs84(150)}])

    img = getattr(cartes, fonction)(projet)

    assert img.size == (base.PLATE_W, base.PLATE_H)
    assert wms_factice[0]["couche"] == getattr(config, couche)


def test_repere_viole_marque_le_site_quand_aucune_parcelle_n_est_dessinee(
        wms_factice):
    """Sans contour cadastral, la planche doit quand même localiser le projet."""
    # Arrange
    lon, lat = _L93_VERS_WGS84.transform(*CENTRE_L93)
    projet = _projet_carte(lon=lon, lat=lat)

    # Act
    img = cartes.planche_situation(projet)

    # Assert : la croix est tracée au centre exact de la zone utile
    centre = (base.MARGE + LARGEUR_UTILE_PX // 2, base.MARGE + HAUTEUR_UTILE_PX // 2)
    assert img.getpixel(centre) == base.VIOLET


def test_parcelles_dessinees_remplacent_le_repere_viole(wms_factice):
    """Dès qu'un contour est tracé, le repère de secours disparaît."""
    projet = _projet_carte(parcelles=[{"geometry": _carre_wgs84(200)}])

    img = cartes.planche_situation(projet)

    centre = (base.MARGE + LARGEUR_UTILE_PX // 2, base.MARGE + HAUTEUR_UTILE_PX // 2)
    assert img.getpixel(centre) != base.VIOLET


def test_multipolygone_cadastral_est_pris_en_compte(wms_factice):
    """apicarto renvoie parfois des MultiPolygon : les deux formes sont gérées.

    Le cadrage doit englober les deux morceaux, pas seulement le premier.
    """
    # Arrange : deux carrés de 100 m distants de 600 m sur l'axe est-ouest
    gauche = _carre_wgs84(100, (CENTRE_L93[0] - 300, CENTRE_L93[1]))
    droite = _carre_wgs84(100, (CENTRE_L93[0] + 300, CENTRE_L93[1]))
    geom = {"type": "MultiPolygon",
            "coordinates": [gauche["coordinates"], droite["coordinates"]]}
    projet = _projet_carte(parcelles=[{"geometry": geom}])

    # Act
    cartes.planche_cadastrale(projet)

    # Assert : étendue de 700 m -> 0,42 x e >= 700 x 1,8 -> 5 000
    appel = wms_factice[0]
    assert _echelle_reelle(appel) == pytest.approx(5000, rel=1e-9)
    assert (appel["bbox"][0] + appel["bbox"][2]) / 2 == pytest.approx(
        CENTRE_L93[0], abs=1.0)
