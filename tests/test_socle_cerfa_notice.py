"""Filet de sécurité du SOCLE livrable : Cerfa 16702 et notice DP11.

Pourquoi ce fichier (01/09/2026) : le module Insertion IA va être retiré
(insertion_ia, routes_insertion, implantation, perspective, scaffold,
fiche_emprise et l'étape 4 du front). La suite de tests existante couvre
massivement ce qui part ; les deux pièces que la mairie lit vraiment, le
Cerfa et la notice, ne le sont qu'en surface. Ces tests caractérisent
l'existant pour qu'une régression introduite par l'amputation se voie.

Chaque test cible une régression PLAUSIBLE du retrait :
- un import supprimé de trop (catalogue, regles) qui casse le pré-remplissage,
- un champ Cerfa qui cesse d'être écrit,
- une balise {{...}} de la notice qui reste crue faute de sa valeur,
- la route notice/generer qui casse parce que routes_dossier.py importait
  insertion_ia.

Rien n'est écrit hors de tmp_path : config.PROJETS_DIR est redirigé par
monkeypatch dans chaque test, comme dans tests/test_api_projets.py.
Aucun appel réseau.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfReader

from app import config
from app.cerfa import GABARIT, preremplir
from app.main import app
from app.notice import SECTIONS, generer_sections, variables
from tests.test_notice_cerfa import PROJET

# Champs Cerfa dont le remplissage est le contrat du socle. Volontairement
# SANS C2ZE1_puissance ni C2ZP1_crete (inversion connue puissance/hauteur, à
# corriger), sans E1D_date (date au format JJ/MM/AAAA dans un champ à cases, à
# corriger) et sans D5A_acceptation (case cochée d'office, à corriger) :
# verrouiller ces quatre-là interdirait la correction.
CHAMPS_SOCLE = {
    "C2ZA1_nouvelle",            # construction nouvelle
    "C2ZD1_description",         # description du projet
    "D2D_denomination",          # demandeur personne morale
    "D2R_raison",
    "D2J_type",
    "D2N_nom",
    "D2S_siret",
    "D3V_voie",                  # adresse du demandeur
    "E1L_lieu",                  # engagement : lieu
    "S1A_stationnementavant",    # stationnement avant / après
    "S1M_stationnementapres",
    "T2V_voie",                  # terrain
    "T2L_localite",
    "T2C_code",
    "T2F_prefixe",               # 1re parcelle
    "T2S_section",
    "T2N_numero",
    "T2T_superficie",
}


@pytest.fixture()
def projets_temporaires(tmp_path, monkeypatch):
    """Redirige le stockage projets vers tmp_path (jamais le vrai PROJETS/)."""
    monkeypatch.setattr(config, "PROJETS_DIR", tmp_path)
    return tmp_path


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROJETS_DIR", tmp_path / "PROJETS")
    return TestClient(app)


# --------------------------------------------------------------------- Cerfa


def test_cerfa_produit_un_pdf_acroform_intact(projets_temporaires):
    """Le PDF de sortie reste un vrai PDF, relisible, sans perte de champs.

    Le pré-remplissage réécrit les 20 pages du gabarit : une régression de la
    chaîne pypdf (append + update_page_form_field_values) produit typiquement
    un fichier ouvrable mais amputé de son AcroForm, donc un Cerfa qui
    s'imprime vide. On compare au gabarit versionné.
    """
    # Arrange
    gabarit = PdfReader(GABARIT)
    champs_gabarit = set(gabarit.get_fields())

    # Act
    chemin, _, _ = preremplir(PROJET)

    # Assert
    assert chemin.exists()
    with open(chemin, "rb") as f:
        assert f.read(5) == b"%PDF-"
    lecteur = PdfReader(chemin)
    assert len(lecteur.pages) == len(gabarit.pages) == 20
    assert set(lecteur.get_fields()) == champs_gabarit, (
        "des champs du formulaire ont disparu à l'écriture"
    )
    # sans NeedAppearances, le lecteur PDF affiche un formulaire vide malgré
    # les valeurs présentes dans le fichier : c'est la panne silencieuse type
    acroform = lecteur.trailer["/Root"]["/AcroForm"]
    assert bool(acroform.get("/NeedAppearances")) is True


def test_cerfa_ecrit_dans_les_assets_du_projet(projets_temporaires):
    """Le fichier atterrit dans <projet>.assets, sous le nom attendu par la
    route de téléchargement (/api/projets/{id}/cerfa.pdf, chemin en dur)."""
    # Act
    chemin, _, _ = preremplir(PROJET)

    # Assert
    assert chemin.name == "cerfa_16702_prerempli.pdf"
    assert chemin.parent.name == f"{PROJET['id']}.assets"
    assert projets_temporaires in chemin.parents  # rien hors du dossier de test


def test_cerfa_remplit_le_socle_des_champs_attendus(projets_temporaires):
    """Les champs du socle sont tous écrits, et la liste rendue est triée.

    L'API renvoie `len(champs)` à l'écran ; un champ qui cesse d'être rempli
    est invisible côté utilisateur, d'où l'énumération explicite.
    """
    # Act
    _, champs, avertissements = preremplir(PROJET)

    # Assert
    assert CHAMPS_SOCLE.issubset(set(champs)), CHAMPS_SOCLE - set(champs)
    assert champs == sorted(champs)
    assert avertissements == []      # 1 seule parcelle : rien à signaler


def test_cerfa_relit_les_valeurs_saisies(projets_temporaires):
    """Contrôle de bout en bout : ce qui a été saisi se relit dans le PDF.

    Le test existant vérifie déjà le demandeur et la 1re parcelle ; ici on
    couvre les blocs non testés (adresse du terrain, commune, code postal,
    stationnement, lieu d'engagement), qui viennent de trois fonctions
    différentes de cerfa.py.
    """
    # Act
    chemin, _, _ = preremplir(PROJET)

    # Assert. Les champs « voie » reçoivent l'adresse non structurée telle
    # quelle : on vérifie qu'ils portent bien la rue, sans figer la redondance
    # code postal / commune qui s'y trouve aussi aujourd'hui.
    lus = PdfReader(chemin).get_fields()
    assert "Allée du Golf" in (lus["T2V_voie"].get("/V") or "")
    assert lus["T2L_localite"].get("/V") == "Soufflenheim"
    assert lus["T2C_code"].get("/V") == "67620"
    assert lus["T2F_prefixe"].get("/V") == "000"
    assert "rue de l'Énergie" in (lus["D3V_voie"].get("/V") or "")
    assert lus["S1A_stationnementavant"].get("/V") == "60"
    assert lus["S1M_stationnementapres"].get("/V") == "60"
    assert lus["E1L_lieu"].get("/V") == "Soufflenheim"


def test_cerfa_nemet_pas_les_champs_non_renseignes(projets_temporaires):
    """Une valeur vide ne doit pas être écrite du tout.

    Écrire "" au lieu de ne rien écrire efface la valeur d'un formulaire déjà
    rempli à la main et fait grimper artificiellement le compteur de champs.
    Ici le maître d'ouvrage n'a ni SIRET, ni représentant, ni adresse.
    """
    # Arrange
    projet = {**PROJET, "mo": {"type": "Société", "raison_sociale": "ACME"}}

    # Act
    _, champs, _ = preremplir(projet)

    # Assert
    assert "D2D_denomination" in champs
    for absent in ("D2S_siret", "D2N_nom", "D3V_voie"):
        assert absent not in champs


def test_cerfa_demandeur_particulier_bascule_sur_le_cadre_nom(projets_temporaires):
    """Un particulier remplit le cadre 1 (nom), pas le cadre 2 (personne morale).

    Se tromper de cadre rend le Cerfa irrecevable au guichet.
    """
    # Arrange
    projet = {**PROJET, "mo": {"type": "particulier",
                               "raison_sociale": "Jean Dupont"}}

    # Act
    chemin, champs, _ = preremplir(projet)

    # Assert
    assert "D1N_nom" in champs
    assert "D2D_denomination" not in champs
    assert "D2S_siret" not in champs
    assert PdfReader(chemin).get_fields()["D1N_nom"].get("/V") == "Jean Dupont"


def test_cerfa_refuse_le_regime_pc_en_secteur_abf(projets_temporaires):
    """Second chemin vers le PC : le secteur ABF, en plus du seuil 3 MWc déjà
    testé. Générer un Cerfa DP pour un projet en PC produit un dossier faux ;
    le refus doit citer la raison pour que l'utilisateur comprenne.
    """
    # Arrange
    projet = {**PROJET,
              "urbanisme": {**PROJET["urbanisme"], "secteur_abf": True}}

    # Act / Assert
    with pytest.raises(ValueError, match="Permis de Construire"):
        preremplir(projet)
    with pytest.raises(ValueError, match="ABF"):
        preremplir(projet)
    # refus AVANT écriture : aucun PDF trompeur ne traîne dans les assets
    assert not (config.assets_dir(PROJET["id"])
                / "cerfa_16702_prerempli.pdf").exists()


def test_cerfa_ne_retient_que_les_trois_premieres_parcelles(projets_temporaires):
    """Le gabarit n'a que 3 lignes de parcelles : les suivantes sont écartées.

    Le test existant vérifie l'avertissement ; celui-ci vérifie l'autre moitié
    du contrat, à savoir qu'aucune 4e parcelle ne se retrouve écrite par-dessus
    une autre ligne.
    """
    # Arrange
    parcelles = [{"section": "30", "numero": f"04{i:02d}", "com_abs": "000"}
                 for i in range(5)]
    projet = {**PROJET,
              "localisation": {**PROJET["localisation"], "parcelles": parcelles}}

    # Act
    chemin, _, avertissements = preremplir(projet)

    # Assert
    lus = PdfReader(chemin).get_fields()
    numeros = [lus[c].get("/V")
               for c in ("T2N_numero", "T2NP2_numero", "T2NP3_numero")]
    assert numeros == ["0400", "0401", "0402"]
    valeurs = {(v.get("/V") or "") for v in lus.values()}
    assert "0403" not in valeurs and "0404" not in valeurs
    assert any("papier libre" in a for a in avertissements)


def test_cerfa_sans_aucune_donnee_ombriere_naffirme_rien(projets_temporaires):
    """Ombrière non renseignée : AUCUN champ « projet » n'est écrit.

    C'est la doctrine « brouillon avec trous signalés » correctement appliquée
    (garde en tête de _champs_projet) : pas de description inventée. Ce
    comportement doit survivre au retrait, il sert de référence au test xfail
    plus bas.
    """
    # Arrange
    projet = {**PROJET, "ombriere": {}}

    # Act
    _, champs, _ = preremplir(projet)

    # Assert
    for champ in ("C2ZD1_description", "C2ZA1_nouvelle",
                  "S1A_stationnementavant"):
        assert champ not in champs
    assert "T2S_section" in champs   # le reste du formulaire est bien rempli


def test_cerfa_sans_famille_dombriere_ne_plante_pas(projets_temporaires):
    """Famille absente mais puissance saisie : la génération aboutit.

    Comportement à préserver tel quel (pas de crash). Ce que le Cerfa RACONTE
    dans ce cas est en revanche fautif : voir le test xfail juste en dessous.
    """
    # Arrange
    projet = {**PROJET, "ombriere": {"puissance_kwc": 500}}

    # Act
    chemin, champs, avertissements = preremplir(projet)

    # Assert
    assert chemin.exists()
    assert "C2ZD1_description" in champs
    assert avertissements == []


@pytest.mark.xfail(
    strict=False,
    reason="DÉFAUT CONNU (catalogue.py:81) : sans famille saisie, "
           "CATALOGUE.get(famille, CATALOGUE['START PLAINE Bas']) retombe "
           "silencieusement sur le type Mono Bas, et le Cerfa affirme alors "
           "ses cotes (5 m de profondeur, 3,50 m hors tout) alors que RIEN "
           "n'a été saisi. La doctrine retenue est « brouillon avec trous "
           "signalés » : l'absence de type doit produire un trou et un "
           "avertissement, pas une cote de catalogue. Ce comportement devra "
           "donc changer, et ce test passera au vert quand ce sera fait.",
)
def test_cerfa_sans_famille_ne_devrait_pas_affirmer_de_cotes_catalogue(
    projets_temporaires,
):
    """Documente le repli silencieux du catalogue SANS le bénir.

    On écrit ici le comportement VOULU, pas le comportement actuel : le
    marquer xfail laisse la trace du défaut dans la suite sans le verrouiller
    par un assert, ce qui reviendrait à interdire la correction.
    """
    # Arrange : aucune famille, aucune cote, seulement une puissance
    projet = {**PROJET, "ombriere": {"puissance_kwc": 500}}

    # Act
    chemin, _, avertissements = preremplir(projet)
    desc = PdfReader(chemin).get_fields()["C2ZD1_description"].get("/V") or ""

    # Assert : rien du type par défaut ne doit être affirmé
    assert "START PLAINE Bas" not in desc
    assert "5 m de profondeur" not in desc
    assert "3,50 m" not in desc
    assert avertissements, "l'absence de type d'ombrière devrait être signalée"


# -------------------------------------------------------------------- Notice


def test_notice_conserve_les_sept_sections_dans_l_ordre():
    """L'ordre des sections est le plan de la notice DP11 imprimée.

    generer_sections itère sur TEMPLATES : une section retirée ou déplacée
    change silencieusement le document déposé en mairie.
    """
    # Act
    sections = generer_sections(PROJET)

    # Assert
    assert list(sections) == [cle for cle, _ in SECTIONS]
    assert len(sections) == 7


def test_notice_ne_laisse_aucune_balise_non_remplie():
    """Aucun {{variable}} ne doit subsister dans le texte livré.

    Régression la plus probable du retrait : une clé disparaît de _valeurs
    (elle vient du modèle paramétrique partagé avec le module Insertion) et la
    balise crue part telle quelle dans la notice. _remplir ne signale rien, il
    recopie la balise inconnue. On teste le projet complet ET le projet vide.
    """
    # Act
    complet = generer_sections(PROJET)
    minimal = generer_sections({"nom": "Projet à peine créé"})

    # Assert
    for source, sections in (("complet", complet), ("minimal", minimal)):
        for cle, texte in sections.items():
            assert "{{" not in texte and "}}" not in texte, (source, cle, texte)


def test_notice_projet_minimal_marque_les_trous_sans_inventer():
    """Sans données, la notice affiche des trous, pas des valeurs fabriquées.

    Doctrine « aucune donnée inventée » : les cotes du catalogue (5 m de
    profondeur, 3,47 m hors tout) ne doivent pas apparaître quand aucune
    ombrière n'a été choisie.
    """
    # Act
    sections = generer_sections({"nom": "Projet à peine créé"})

    # Assert
    assert "—" in sections["etat_initial"]         # parcelles, commune, INSEE
    assert "de dimensions à préciser" in sections["description"]
    assert "une trame de poteaux régulière" in sections["description"]
    for cote_catalogue in ("5 m", "3,47 m", "3,06 m", "START PLAINE"):
        assert cote_catalogue not in sections["description"], cote_catalogue


def test_notice_dit_explicitement_labsence_de_donnee_urbanisme():
    """Zonage, ABF et risques absents : la notice le dit, elle ne se tait pas.

    Une notice muette laisserait croire que le point a été vérifié. Ces trois
    phrases sont la formulation opposable devant l'instructeur.
    """
    # Act
    sections = generer_sections({"nom": "X", "localisation": {"commune": "Y"}})

    # Assert
    texte = sections["reglementaire"]
    assert "non couvert par le GPU" in texte
    assert "aucun périmètre de protection patrimoniale" in texte
    assert "Aucun risque majeur" in texte
    assert "R.421-9" in texte     # base réglementaire de la DP


def test_notice_variables_a_surligner_apparaissent_bien_dans_le_texte():
    """Contrat du surlignage de relecture : chaque valeur rendue par
    `variables` doit exister mot pour mot dans le texte généré.

    L'UI surligne ces chaînes pour que le BE relise les valeurs concrètes. Si
    un formatage diverge d'un côté (la hauteur passant par exemple de 3,86 m à
    3.86 m), le surlignage tombe à côté et la relecture ne sert plus à rien.
    """
    # Act
    vals = variables(PROJET)
    texte = " ".join(generer_sections(PROJET).values())

    # Assert
    assert vals, "aucune variable à surligner"
    absentes = [v for v in vals if v not in texte]
    assert absentes == [], absentes
    assert vals == sorted(vals, key=len, reverse=True)  # les plus longues d'abord
    assert "—" not in vals                              # jamais surligner un trou


# ------------------------------------------- Notice : route de (re)génération


def _creer_projet(client, **surcharges) -> dict:
    """Crée un projet via l'API et renvoie sa représentation serveur."""
    charge = {
        "nom": "Socle notice",
        "mo": {"type": "Société", "raison_sociale": "Greenvolt Next France",
               "representant": "Florent Guillemin"},
        "localisation": {"adresse": "Allée du Golf", "commune": "Soufflenheim",
                         "code_postal": "67620", "code_insee": "67472"},
        "ombriere": {"famille": "START PLAINE Double", "puissance_kwc": 500},
        **surcharges,
    }
    reponse = client.post("/api/projets", json=charge)
    assert reponse.status_code == 200
    return reponse.json()["projet"]


def test_route_notice_genere_les_sept_sections(client):
    """La route de génération de la notice reste opérationnelle.

    routes_dossier.py importe insertion_ia en tête de fichier : si le retrait
    laisse cet import, tout le routeur (notice, Cerfa, assemblage, exports)
    tombe à l'import et ce test échoue immédiatement.
    """
    # Arrange
    projet = _creer_projet(client)

    # Act
    r = client.post(f"/api/projets/{projet['id']}/notice/generer")

    # Assert
    assert r.status_code == 200
    corps = r.json()
    sections = corps["projet"]["notice"]["sections"]
    assert list(sections) == [cle for cle, _ in SECTIONS]
    assert corps["projet"]["notice"]["genere_par_ia"] is False
    assert corps["variables"]                       # surlignage alimenté
    # la notice remplie fait basculer la pièce DP11 de « à générer » à relire
    dp11 = [p for p in corps["evaluation"]["completude"]["pieces"]
            if p["code"] == "dp11"][0]
    assert dp11["statut"] == "a_completer"


def test_route_notice_ne_reecrit_que_les_sections_vides(client):
    """Une section déjà rédigée n'est JAMAIS resynchronisée sans `force`.

    Mécanisme : routes_dossier.generer_notice n'écrit une section que si elle
    est vide. C'est la cause d'une incohérence d'adresse réelle : l'adresse est
    corrigée à l'étape localisation, la notice est régénérée, et le texte garde
    l'ancienne adresse sans le dire. Comportement documenté ici tel qu'il est,
    avec sa contrepartie (le texte relu par un humain n'est jamais écrasé),
    pour qu'un changement soit un choix et pas un accident.
    """
    # Arrange : notice générée avec l'adresse initiale
    projet = _creer_projet(client)
    pid = projet["id"]
    client.post(f"/api/projets/{pid}/notice/generer")
    projet = client.get(f"/api/projets/{pid}").json()["projet"]
    assert "Allée du Golf" in projet["notice"]["sections"]["presentation"]

    # l'adresse est corrigée après coup, comme dans le cas réel
    projet["localisation"]["adresse"] = "12 rue Neuve"
    assert client.put(f"/api/projets/{pid}", json=projet).status_code == 200

    # Act
    r = client.post(f"/api/projets/{pid}/notice/generer")

    # Assert : la section déjà écrite garde l'ANCIENNE adresse
    presentation = r.json()["projet"]["notice"]["sections"]["presentation"]
    assert "Allée du Golf" in presentation
    assert "12 rue Neuve" not in presentation


def test_route_notice_force_resynchronise_le_texte(client):
    """`force=1` est la seule sortie de secours : il réécrit toutes les sections.

    Sans lui, l'incohérence d'adresse ci-dessus serait sans remède côté outil.
    """
    # Arrange
    projet = _creer_projet(client)
    pid = projet["id"]
    client.post(f"/api/projets/{pid}/notice/generer")
    projet = client.get(f"/api/projets/{pid}").json()["projet"]
    projet["localisation"]["adresse"] = "12 rue Neuve"
    client.put(f"/api/projets/{pid}", json=projet)

    # Act
    r = client.post(f"/api/projets/{pid}/notice/generer?force=1")

    # Assert
    presentation = r.json()["projet"]["notice"]["sections"]["presentation"]
    assert "12 rue Neuve" in presentation
    assert "Allée du Golf" not in presentation


def test_route_notice_ne_touche_pas_a_la_validation_humaine(client):
    """Régénérer ne doit pas remettre en cause la validation du BE.

    `valide_humain` est ce qui autorise le dépôt (regles._statut_piece) : le
    faire retomber à faux à chaque régénération bloquerait l'export, et
    l'inverse ferait partir un brouillon en mairie.
    """
    # Arrange
    projet = _creer_projet(client)
    pid = projet["id"]
    client.post(f"/api/projets/{pid}/notice/generer")
    projet = client.get(f"/api/projets/{pid}").json()["projet"]
    projet["notice"]["valide_humain"] = True
    client.put(f"/api/projets/{pid}", json=projet)

    # Act
    r = client.post(f"/api/projets/{pid}/notice/generer")

    # Assert
    assert r.json()["projet"]["notice"]["valide_humain"] is True
