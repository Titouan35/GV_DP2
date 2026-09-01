"""Filet de sécurité du socle : composition du PPTX (app/assemblage.py).

Ce fichier caractérise le livrable tel qu'il est produit AUJOURD'HUI, avant le
retrait du module Insertion. Objectif : prouver après l'amputation que les
planches, leur ordre, les pièces qui y atterrissent et les repli sur
emplacement réservé n'ont pas bougé.

Conventions reprises de tests/test_api_projets.py et tests/test_assemblage.py :
PROJETS_DIR redirigé vers tmp_path par monkeypatch (jamais d'écriture dans le
vrai dossier PROJETS/), générateurs de cartes DP1 neutralisés (ils appellent
l'IGN, aucun test ne doit dépendre du réseau).

Le module Insertion a été retiré le 01/09/2026. Les tests qui dépendaient de
la clé `insertion` ont été réécrits sur la nouvelle règle de la planche DP6 :
l'état existant vient de la pièce DP7, l'état projeté de la pièce DP6 déposée
par le bureau d'études. Aucun test de ce fichier ne dépend plus de cette clé.
"""
import hashlib

import pytest
from PIL import Image
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE

from app import assemblage, config, regles
from tests.test_notice_cerfa import PROJET

ID_PROJET = "socle-assemblage-test"

# Bornes verticales de la zone de contenu d'une planche : servent à isoler
# l'image de la PIÈCE du logo Greenvolt (cartouche, y = 675) et du dégradé de
# la page de garde (y = 0), qui sont des images de décor.
HAUT_CONTENU = assemblage.P(100)
BAS_CONTENU = assemblage.P(650)


# ------------------------------------------------------------------ outillage

@pytest.fixture()
def socle(tmp_path, monkeypatch):
    """Dossier de projets isolé + aucun appel réseau pendant l'assemblage."""
    monkeypatch.setattr(config, "PROJETS_DIR", tmp_path)
    # Les générateurs DP1 (situation, cadastral, aérien) interrogent la
    # Géoplateforme : neutralisés, les planches DP1 tombent sur leur
    # emplacement réservé, ce qui ne change rien au reste du montage.
    monkeypatch.setattr(assemblage, "GENERATEURS", {})
    return tmp_path


def projet_socle(**surcharges):
    """Projet de référence : pièces BE absentes, AUCUNE clé « insertion ».

    L'absence volontaire de la clé `insertion` garantit que ces tests restent
    verts une fois le module Insertion retiré.
    """
    projet = {**PROJET, "id": ID_PROJET, "documents": {}}
    projet.update(surcharges)
    return projet


def assets(tmp_path):
    dossier = tmp_path / f"{ID_PROJET}.assets"
    dossier.mkdir(parents=True, exist_ok=True)
    return dossier


def deposer_image(tmp_path, nom, couleur=(200, 60, 60), taille=(240, 160)):
    """Écrit une pièce image et renvoie (chemin, entrée JSON `documents`).

    Petite image PNG : _optimiser la laisse telle quelle (pas de recompression),
    donc son empreinte se retrouve à l'identique dans le PPTX.
    """
    chemin = assets(tmp_path) / nom
    Image.new("RGB", taille, couleur).save(chemin)
    return chemin, {"fichier": f"{ID_PROJET}.assets/{nom}", "nom": nom}


def deposer_pdf(tmp_path, nom, couleurs):
    """Écrit une pièce PDF de len(couleurs) pages (pages visuellement distinctes)."""
    chemin = assets(tmp_path) / nom
    pages = [Image.new("RGB", (600, 400), c) for c in couleurs]
    pages[0].save(chemin, "PDF", save_all=True, append_images=pages[1:])
    return chemin, {"fichier": f"{ID_PROJET}.assets/{nom}", "nom": nom}


def empreinte(chemin):
    return hashlib.sha1(chemin.read_bytes()).hexdigest()


def planches(chemin_pptx):
    return list(Presentation(str(chemin_pptx)).slides)


def titre(slide):
    """Titre de planche posé par _entete (x = 48, y = 44). None sur la garde."""
    for forme in slide.shapes:
        if (forme.has_text_frame and forme.left == assemblage.P(48)
                and forme.top == assemblage.P(44)):
            return forme.text_frame.text
    return None


def badge(slide):
    """Badge de pièce du cartouche (seule forme posée à y = 671)."""
    for forme in slide.shapes:
        if forme.has_text_frame and forme.top == assemblage.P(671):
            return forme.text_frame.text
    return None


def textes(slide):
    return "\n".join(f.text_frame.text for f in slide.shapes if f.has_text_frame)


def images_contenu(slide):
    """Images de la zone de contenu, décor du cartouche exclu."""
    return [f for f in slide.shapes
            if f.shape_type == MSO_SHAPE_TYPE.PICTURE
            and HAUT_CONTENU <= f.top < BAS_CONTENU]


def empreintes_contenu(slide):
    return [hashlib.sha1(f.image.blob).hexdigest() for f in images_contenu(slide)]


def planche(slides, titre_attendu, rang=0):
    """La rang-ième planche portant ce titre (lève si elle n'existe pas)."""
    trouvees = [s for s in slides if titre(s) == titre_attendu]
    assert len(trouvees) > rang, f"planche « {titre_attendu} » absente"
    return trouvees[rang]


# Ordre de la maquette (export DP_Template validé le 16/07/2026), un couple
# (titre de planche, badge de pièce) par planche. La garde n'a ni l'un ni
# l'autre. DP4 retirée le 17/07/2026.
MAQUETTE = [
    (None, None),
    ("Plan de situation et cadastral", "DP1"),
    ("Vue aérienne", "DP1"),
    ("Plan de masse", "DP2"),
    ("Coupe du terrain et de la construction", "DP3"),
    ("Notice descriptive", "Notice"),
    ("Insertion paysagère", "DP6"),
    ("Photographies du terrain", "DP7/8"),
]


# ------------------------------------------------------------------ composition

def test_dossier_socle_produit_les_planches_de_la_maquette_dans_l_ordre(socle):
    """Squelette du livrable : 8 planches, titres et badges, dans cet ordre.

    C'est le test le plus large du filet : toute planche perdue, ajoutée ou
    déplacée par le retrait du module Insertion le fait tomber.
    """
    # Arrange : aucune pièce BE, aucune clé insertion
    projet = projet_socle()

    # Act
    chemin, _ = assemblage.generer_dossier(projet)

    # Assert
    slides = planches(chemin)
    assert [(titre(s), badge(s)) for s in slides] == MAQUETTE
    prs = Presentation(str(chemin))
    assert (prs.slide_width, prs.slide_height) == (assemblage.P(1280),
                                                   assemblage.P(720))


def test_le_pptx_est_ecrit_dans_les_assets_sans_laisser_de_fichier_temporaire(socle):
    """Écriture atomique : le .tmp doit être remplacé, jamais laissé en place."""
    # Arrange / Act
    chemin, _ = assemblage.generer_dossier(projet_socle())

    # Assert
    assert chemin == config.assets_dir(ID_PROJET) / f"Dossier_DP_{ID_PROJET}.pptx"
    assert chemin.exists()
    assert not list(assets(socle).glob("*.tmp"))


def test_projet_sans_identifiant_est_refuse(socle):
    """Sans id, les assets iraient n'importe où : l'assemblage doit refuser."""
    projet = projet_socle()
    projet.pop("id")

    with pytest.raises(ValueError):
        assemblage.generer_dossier(projet)


def test_regenerer_le_dossier_redonne_le_meme_montage(socle):
    """Deux assemblages de suite doivent donner le même dossier.

    Garde-fou du cache de rendu PDF (_pdf_en_images) : à la 2e passe, les pages
    viennent du cache disque et non d'un nouveau rendu. Une régression du cache
    ferait disparaître les planches DP2 au 2e export.
    """
    # Arrange
    _, doc = deposer_pdf(socle, "dp2.pdf", [(220, 40, 40), (40, 60, 220)])
    projet = projet_socle(documents={"dp2": doc})

    # Act
    chemin1, avert1 = assemblage.generer_dossier(projet)
    chemin2, avert2 = assemblage.generer_dossier(projet)

    # Assert
    assert chemin1 == chemin2
    assert avert1 == avert2
    slides = planches(chemin2)
    assert len(slides) == 9                       # 8 + la 2e page du plan
    assert [titre(s) for s in slides].count("Plan de masse") == 2


# ------------------------------------------------------------------ page de garde

def test_page_de_garde_annonce_le_regime_dp_et_son_cerfa(socle):
    """La garde porte le régime calculé par regles.evaluer, pas une valeur figée."""
    chemin, _ = assemblage.generer_dossier(projet_socle())

    garde = textes(planches(chemin)[0])
    assert f"CERFA {regles.CERFA_DP}" in garde
    assert "Déclaration" in garde and "préalable" in garde
    assert "Construction d'une ombrière photovoltaïque" in garde


def test_adresse_de_la_page_de_garde_vient_de_la_localisation(socle):
    """L'adresse « Adresse du projet » vient de localisation.adresse.

    Piège : le maître d'ouvrage a lui aussi une adresse (son siège). Les deux
    sont volontairement différentes ici pour prouver laquelle est affichée où.
    """
    # Arrange
    projet = projet_socle(
        localisation={**PROJET["localisation"], "adresse": "Allée du Golf 67620 Soufflenheim"},
        mo={**PROJET["mo"], "adresse": "1 rue de l'Énergie, 69000 Lyon"})

    # Act
    chemin, _ = assemblage.generer_dossier(projet)

    # Assert
    slides = planches(chemin)
    garde = textes(slides[0])
    assert "Adresse du projet" in garde
    assert "Allée du Golf 67620 Soufflenheim" in garde
    # le siège du MO reste sur la ligne « Maître d'ouvrage », accolé à la
    # raison sociale
    assert "Greenvolt Next France — 1 rue de l'Énergie, 69000 Lyon" in garde
    # la parcelle du cadastre est reprise telle quelle
    assert "Section 30 n° 0464" in garde
    # et le cartouche des planches reprend « raison sociale · adresse du site »
    assert ("Greenvolt Next France · Allée du Golf 67620 Soufflenheim"
            in textes(slides[1]))


def test_page_de_garde_sans_adresse_ni_parcelle_affiche_un_tiret(socle):
    """Projet en début de saisie : la garde reste montable, avec des tirets."""
    projet = projet_socle(localisation={"commune": "Soufflenheim"})

    chemin, _ = assemblage.generer_dossier(projet)

    garde = textes(planches(chemin)[0])
    assert "Adresse du projet" in garde
    assert "—" in garde


def test_cartouche_retombe_sur_le_nom_du_projet_sans_raison_sociale(socle):
    """Sans MO renseigné, le cartouche affiche le nom du projet (repli)."""
    projet = projet_socle(mo={})

    chemin, _ = assemblage.generer_dossier(projet)

    assert PROJET["nom"] in textes(planches(chemin)[1])


def test_regime_pc_change_la_page_de_garde_sans_changer_les_planches(socle):
    """>= 3 MWc : regles bascule en PC, l'assemblage n'échoue pas.

    Contraste utile avec cerfa.preremplir, qui REFUSE le régime PC. Ici le
    montage se poursuit : seule la page de garde change de titre et de numéro
    de formulaire. On assert le numéro via la constante regles.CERFA_PC, pas sa
    valeur littérale : elle est explicitement marquée « à vérifier » dans
    app/regles.py:36, ce n'est pas un acquis à verrouiller.
    """
    # Arrange
    projet = projet_socle(ombriere={**PROJET["ombriere"], "puissance_kwc": 3500})

    # Act
    chemin, _ = assemblage.generer_dossier(projet)

    # Assert
    slides = planches(chemin)
    garde = textes(slides[0])
    assert "Permis de" in garde and "construire" in garde
    assert f"CERFA {regles.CERFA_PC}" in garde
    # le reste du dossier est identique : le régime ne pilote QUE la garde
    assert [(titre(s), badge(s)) for s in slides] == MAQUETTE


# ------------------------------------------------------------------ pièces déposées

def test_dp2_deposee_est_placee_sur_la_planche_plan_de_masse(socle):
    """La pièce DP2 du BE atterrit sur SA planche, et pas sur une autre."""
    # Arrange
    chemin_dp2, doc = deposer_image(socle, "plan_masse.png", couleur=(10, 120, 200))
    projet = projet_socle(documents={"dp2": doc})

    # Act
    chemin, _ = assemblage.generer_dossier(projet)

    # Assert
    slides = planches(chemin)
    assert len(slides) == 8
    assert empreintes_contenu(planche(slides, "Plan de masse")) == [empreinte(chemin_dp2)]
    # aucune autre planche ne reprend cette image
    ailleurs = [s for s in slides if titre(s) != "Plan de masse"]
    assert all(empreinte(chemin_dp2) not in empreintes_contenu(s) for s in ailleurs)


def test_dp2_pdf_multipage_donne_une_planche_par_page(socle):
    """Un plan de masse en PDF de 2 pages produit 2 planches DP2, dans l'ordre."""
    # Arrange
    _, doc = deposer_pdf(socle, "dp2.pdf", [(220, 40, 40), (40, 60, 220)])
    projet = projet_socle(documents={"dp2": doc})

    # Act
    chemin, _ = assemblage.generer_dossier(projet)

    # Assert
    slides = planches(chemin)
    assert len(slides) == 9
    pages = sorted(assets(socle).glob("dp2_p*.jpg"))
    assert len(pages) == 2
    assert empreintes_contenu(planche(slides, "Plan de masse", 0)) == [empreinte(pages[0])]
    assert empreintes_contenu(planche(slides, "Plan de masse", 1)) == [empreinte(pages[1])]
    # les planches supplémentaires s'insèrent AVANT la coupe, pas à la fin
    assert [titre(s) for s in slides][3:6] == [
        "Plan de masse", "Plan de masse", "Coupe du terrain et de la construction"]


def test_dp3_pdf_multipage_ne_donne_qu_une_planche_sa_premiere_page(socle):
    """Décision explicite (cas Anse) : une coupe est un dessin unique.

    Les pages suivantes du PDF fourni sont en pratique d'autres pièces, elles
    ne doivent pas entrer au dossier. Comportement volontairement différent de
    la DP2.
    """
    # Arrange
    _, doc = deposer_pdf(socle, "dp3.pdf", [(30, 180, 90), (250, 220, 20)])
    projet = projet_socle(documents={"dp3": doc})

    # Act
    chemin, avertissements = assemblage.generer_dossier(projet)

    # Assert
    slides = planches(chemin)
    assert len(slides) == 8
    pages = sorted(assets(socle).glob("dp3_p*.jpg"))
    coupe = planche(slides, "Coupe du terrain et de la construction")
    assert empreintes_contenu(coupe) == [empreinte(pages[0])]   # page 1 seule
    # la DP3 du BE remplace la coupe provisoire paramétrique
    assert not any("provisoire" in a for a in avertissements)


def test_dp7_et_dp8_partagent_la_planche_photographies(socle):
    """Les deux photos réglementaires vont sur UNE planche, DP7 puis DP8."""
    # Arrange
    chemin7, doc7 = deposer_image(socle, "proche.png", couleur=(200, 30, 30))
    chemin8, doc8 = deposer_image(socle, "lointain.png", couleur=(30, 30, 200))
    projet = projet_socle(documents={"dp7": doc7, "dp8": doc8})

    # Act
    chemin, _ = assemblage.generer_dossier(projet)

    # Assert
    slides = planches(chemin)
    assert len(slides) == 8
    photos = planche(slides, "Photographies du terrain")
    assert empreintes_contenu(photos) == [empreinte(chemin7), empreinte(chemin8)]
    assert "Environnement proche" in textes(photos)
    assert "Paysage lointain" in textes(photos)


def test_dp8_seule_laisse_l_emplacement_reserve_de_la_dp7(socle):
    """Une seule des deux photos : l'autre moitié reste un emplacement réservé."""
    # Arrange
    chemin8, doc8 = deposer_image(socle, "lointain.png", couleur=(30, 30, 200))
    projet = projet_socle(documents={"dp8": doc8})

    # Act
    chemin, _ = assemblage.generer_dossier(projet)

    # Assert
    photos = planche(planches(chemin), "Photographies du terrain")
    assert empreintes_contenu(photos) == [empreinte(chemin8)]
    # texte propre à l'emplacement réservé (le libellé numéroté, lui, est
    # toujours écrit, photo présente ou non)
    assert "Photo datée et repérée" in textes(photos)


def test_dp6_du_be_alimente_l_etat_projete_sans_cle_insertion(socle):
    """Le photomontage DP6 déposé occupe l'après de la planche d'insertion.

    Test écrit SANS clé `insertion` : c'est le chemin qui doit rester après le
    retrait du module Insertion.
    """
    # Arrange
    chemin6, doc6 = deposer_image(socle, "photomontage.png", couleur=(90, 170, 60))
    projet = projet_socle(documents={"dp6": doc6})

    # Act
    chemin, _ = assemblage.generer_dossier(projet)

    # Assert
    slides = planches(chemin)
    assert len(slides) == 8
    insertion = planche(slides, "Insertion paysagère")
    assert empreintes_contenu(insertion) == [empreinte(chemin6)]
    assert "Avant" in textes(insertion) and "Après" in textes(insertion)
    # l'avant n'a pas d'image : il reste sur son emplacement réservé
    assert "État existant" in textes(insertion)
    # aucune pastille « visuel d'illustration » : ce n'est pas une image IA
    assert "IA" not in textes(insertion)


# ------------------------------------------------------------------ pièces manquantes

def test_piece_declaree_mais_absente_du_disque_retombe_sur_l_emplacement_reserve(socle):
    """Cas réel constaté : le JSON déclare dp6.jpg, le fichier n'existe pas.

    L'assemblage ne doit ni échouer ni embarquer une image fantôme : la
    planche retombe sur son emplacement réservé, comme si la pièce n'avait
    jamais été déclarée.
    """
    # Arrange : entrées documents pointant vers des fichiers jamais écrits
    projet = projet_socle(documents={
        "dp6": {"fichier": f"{ID_PROJET}.assets/dp6.jpg", "nom": "dp6.jpg"},
        "dp2": {"fichier": f"{ID_PROJET}.assets/dp2.pdf", "nom": "dp2.pdf"},
    })

    # Act
    chemin, _ = assemblage.generer_dossier(projet)

    # Assert
    slides = planches(chemin)
    assert [(titre(s), badge(s)) for s in slides] == MAQUETTE
    assert images_contenu(planche(slides, "Plan de masse")) == []
    assert images_contenu(planche(slides, "Insertion paysagère")) == []
    assert "État projeté" in textes(planche(slides, "Insertion paysagère"))


def test_piece_declaree_mais_absente_du_disque_est_signalee(socle):
    """Trou bouché le 01/09/2026 par app/coherence.py.

    Ce test était marqué xfail(strict=True) : regles._statut_piece marque la
    pièce « prête, fournie » dès qu'elle est déclarée dans le JSON, sans
    vérifier que le fichier existe. Un dossier réel affichait ainsi « 11/11
    pièces prêtes » avec un fichier DP6 disparu du disque. Le contrôle de
    cohérence lève désormais une anomalie bloquante, remontée en tête des
    avertissements de l'assemblage.
    """
    # Arrange : la DP6 est déclarée, son fichier n'existe pas
    projet = projet_socle(documents={
        "dp6": {"fichier": f"{ID_PROJET}.assets/dp6.jpg", "nom_fichier": "dp6.jpg"}})

    # Act
    _, avertissements = assemblage.generer_dossier(projet)

    # Assert
    alerte = [a for a in avertissements if "DP6" in a and "introuvable" in a]
    assert alerte, avertissements
    assert alerte[0].startswith("[bloquante]")


def test_pieces_absentes_du_json_sont_toutes_signalees(socle):
    """Complétude évaluée AVANT montage : plus de PPTX de placeholders muet.

    On vérifie que CHAQUE pièce attendue du bureau d'études est signalée, en
    lisant la liste de référence dans regles pour ne pas figer des libellés.
    """
    # Arrange
    projet = projet_socle()

    # Act
    _, avertissements = assemblage.generer_dossier(projet)

    # Assert
    attendues = [p["titre"] for p in regles.PIECES_DP if p["mode"] in ("mixte", "be")]
    assert attendues, "garde-fou : la liste des pièces BE ne doit pas être vide"
    for titre_piece in attendues:
        assert any(titre_piece in a for a in avertissements), titre_piece
    # notice non rédigée et Cerfa non pré-rempli sont signalés eux aussi
    assert any("Notice" in a for a in avertissements)
    assert any("Cerfa" in a for a in avertissements)


def test_piece_fournie_disparait_des_avertissements(socle):
    """Déposer la DP2 doit faire taire son avertissement, et lui seul."""
    # Arrange
    _, doc = deposer_image(socle, "plan_masse.png")

    # Act
    _, sans = assemblage.generer_dossier(projet_socle())
    _, avec = assemblage.generer_dossier(projet_socle(documents={"dp2": doc}))

    # Assert
    assert any("Plan de masse" in a for a in sans)
    assert not any("Plan de masse" in a for a in avec)
    assert any("DP7" in a for a in avec)      # les autres pièces restent dues


# ------------------------------------------------------------------ coupe DP3

def test_dp3_sans_upload_integre_la_coupe_provisoire_parametrique(socle):
    """Type d'ombrière choisi mais pas de DP3 du BE : coupe paramétrique.

    C'est ce qui rend un dossier d'avant-vente présentable. La planche porte sa
    pastille d'avertissement et l'appelant reçoit un avertissement explicite.
    """
    # Arrange : PROJET porte déjà famille = START PLAINE Double
    projet = projet_socle()

    # Act
    chemin, avertissements = assemblage.generer_dossier(projet)

    # Assert
    coupe = planche(planches(chemin), "Coupe du terrain et de la construction")
    assert len(images_contenu(coupe)) == 1
    assert "Coupe provisoire" in textes(coupe)
    assert any("provisoire" in a for a in avertissements)
    assert (assets(socle) / "dp3_provisoire.png").exists()


def test_dp3_sans_famille_ni_upload_reste_un_emplacement_reserve(socle):
    """Sans type d'ombrière, rien à dessiner : emplacement réservé, pas d'erreur."""
    # Arrange
    projet = projet_socle(ombriere={"puissance_kwc": 500})

    # Act
    chemin, avertissements = assemblage.generer_dossier(projet)

    # Assert
    coupe = planche(planches(chemin), "Coupe du terrain et de la construction")
    assert images_contenu(coupe) == []
    assert not any("provisoire" in a for a in avertissements)


# ------------------------------------------------------------------ notice

def test_notice_vide_affiche_un_emplacement_reserve(socle):
    """Notice non rédigée : la planche existe mais annonce ce qui manque."""
    chemin, _ = assemblage.generer_dossier(projet_socle())

    notice = planche(planches(chemin), "Notice descriptive")
    assert "Notice à générer" in textes(notice)


def test_notice_repartie_en_deux_colonnes(socle):
    """Les 7 sections sont ventilées en 2 colonnes fixes, pas au fil de l'eau.

    Colonne de gauche (x = 48) : présentation, site, projet, insertion.
    Colonne de droite (x = 672) : réglementaire, réseaux, chantier.
    """
    # Arrange
    projet = projet_socle(notice={"sections": {
        "presentation": "Texte de présentation du projet.",
        "reglementaire": "Texte du contexte réglementaire.",
        "chantier": "Texte du chantier et de la remise en état.",
    }, "valide_humain": True})

    # Act
    chemin, avertissements = assemblage.generer_dossier(projet)

    # Assert
    notice = planche(planches(chemin), "Notice descriptive")
    colonnes = {f.left: f.text_frame.text for f in notice.shapes
                if f.has_text_frame and f.top == assemblage.P(assemblage.ZONE[1] + 2)}
    gauche = colonnes[assemblage.P(48)]
    droite = colonnes[assemblage.P(672)]
    assert "Objet de la demande" in gauche
    assert "Texte de présentation du projet." in gauche
    assert "Contexte réglementaire" in droite
    assert "Chantier et remise en état" in droite
    assert "Texte du chantier et de la remise en état." in droite
    # notice relue et validée : plus signalée en avertissement
    assert not any("Notice descriptive" in a for a in avertissements)


# ---------------------------------------------- planche DP6 avant / après

def test_dp6_avant_vient_de_la_piece_dp7(socle):
    """L'état existant est la photo du parking actuel, c'est-à-dire la DP7.

    Réécrit le 01/09/2026 avec le retrait du module Insertion. Avant, l'image
    « avant » était lue dans projet['insertion']['photo'], une photo déposée
    dans l'écran Insertion ; le texte de l'emplacement réservé promettait déjà
    un repli sur la DP7, mais ce repli n'existait pas dans le code. Il est
    maintenant le chemin normal, et il évite de demander deux fois la même
    photo au bureau d'études.
    """
    # Arrange
    chemin7, doc7 = deposer_image(socle, "dp7.png", couleur=(120, 120, 120))
    projet = projet_socle(documents={"dp7": doc7})

    # Act
    chemin, _ = assemblage.generer_dossier(projet)

    # Assert : la DP7 alimente l'avant, l'après reste réservé
    insertion = planche(planches(chemin), "Insertion paysagère")
    assert empreintes_contenu(insertion) == [empreinte(chemin7)]
    assert "État projeté" in textes(insertion)


def test_dp6_apres_vient_de_la_piece_dp6_deposee(socle):
    """L'état projeté est le photomontage déposé par le bureau d'études.

    C'est désormais la SEULE source possible : la pièce réglementaire.
    """
    # Arrange
    chemin6, doc6 = deposer_image(socle, "photomontage.png", couleur=(90, 170, 60))
    projet = projet_socle(documents={"dp6": doc6})

    # Act
    chemin, _ = assemblage.generer_dossier(projet)

    # Assert : la DP6 alimente l'après, l'avant reste réservé
    insertion = planche(planches(chemin), "Insertion paysagère")
    assert empreintes_contenu(insertion) == [empreinte(chemin6)]
    assert "État existant" in textes(insertion)


def test_dp6_complete_montre_la_dp7_puis_la_dp6_sans_planche_isolee(socle):
    """Les deux pièces présentes : une seule planche de comparaison.

    Le module Insertion ajoutait des planches « visuel d'illustration » pour
    les images IA non retenues. Elles ont disparu : le dossier garde ses 8
    planches quoi qu'il arrive.
    """
    # Arrange
    chemin7, doc7 = deposer_image(socle, "dp7.png", couleur=(120, 120, 120))
    chemin6, doc6 = deposer_image(socle, "photomontage.png", couleur=(90, 170, 60))
    projet = projet_socle(documents={"dp6": doc6, "dp7": doc7})

    # Act
    chemin, _ = assemblage.generer_dossier(projet)

    # Assert
    slides = planches(chemin)
    assert len(slides) == 8
    insertion = planche(slides, "Insertion paysagère")
    # ordre imposé : avant (DP7) à gauche, après (DP6) à droite
    assert empreintes_contenu(insertion) == [empreinte(chemin7), empreinte(chemin6)]
    assert badge(insertion) == "DP6"
    assert "Visuel d'illustration" not in " ".join(textes(insertion))


def test_la_dp7_alimente_a_la_fois_l_avant_et_la_planche_photographies(socle):
    """La même photo sert deux planches, sans être demandée deux fois.

    Conséquence assumée du choix de la DP7 comme état existant : on vérifie
    qu'elle apparaît bien aux deux endroits, pour que personne ne prenne cela
    pour un doublon accidentel.
    """
    # Arrange
    chemin7, doc7 = deposer_image(socle, "dp7.png", couleur=(120, 120, 120))
    projet = projet_socle(documents={"dp7": doc7})

    # Act
    chemin, _ = assemblage.generer_dossier(projet)

    # Assert
    slides = planches(chemin)
    assert empreintes_contenu(planche(slides, "Insertion paysagère")) == [empreinte(chemin7)]
    assert empreinte(chemin7) in empreintes_contenu(planche(slides, "Photographies du terrain"))


def test_dp6_sans_photo_ni_photomontage_montre_deux_emplacements_reserves(socle):
    """Aucune pièce, aucune clé insertion : avant ET après restent réservés."""
    chemin, _ = assemblage.generer_dossier(projet_socle())

    insertion = planche(planches(chemin), "Insertion paysagère")
    assert images_contenu(insertion) == []
    assert "État existant" in textes(insertion)
    assert "État projeté" in textes(insertion)
