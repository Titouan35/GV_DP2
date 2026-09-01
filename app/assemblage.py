"""Assemblage du dossier : PPTX 16:9 fidèle à la maquette Claude Design.

Maquette de référence : export « DP_Template » (Declaration Prealable
Ombriere.dc.html) validé par Florent le 16/07/2026. Une planche 1280x720 px
par pièce : page de garde à barre dégradée verticale, cartouche standard en
pied (logo + intitulé + date + badge de pièce), notice en deux colonnes,
insertion avant/après, photos DP7/DP8.

Le .pptx est le format de travail ; l'export PDF (dépôt) se fait via
export_pdf.py. Les pièces générées (DP1, DP3, DP4) sont régénérées à
l'assemblage ; les pièces BE viennent des uploads (PDF rendus par pypdfium2).
Les grosses images (orthophotos) sont compressées en JPEG à l'embarquement
pour garder un dossier léger.
"""
from __future__ import annotations

import math
import os
import threading
from datetime import date
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image, ImageOps
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.dml import MSO_LINE_DASH_STYLE as MSO_LINE
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Emu, Pt

from . import config, regles
from .geo.client import GeoApiError
from .models import Projet
from .planches import GENERATEURS

# --- charte (maquette) ---
NAVY = RGBColor(0x00, 0x24, 0x55)
VERT = RGBColor(0x05, 0xDB, 0x79)
VIOLET = RGBColor(0x77, 0x6D, 0xF8)
GRIS_LIGNE = RGBColor(0xDF, 0xDF, 0xDD)
GRIS_TIRETS = RGBColor(0xC9, 0xC9, 0xC4)
FOND_CLAIR = RGBColor(0xFA, 0xFA, 0xF9)
BLANC = RGBColor(0xFF, 0xFF, 0xFF)
MUTED = RGBColor(0x66, 0x7C, 0x99)   # navy à ~60 %
SOFT = RGBColor(0x2E, 0x4B, 0x74)    # navy à ~82 % (corps de texte)
POLICE = "Poppins"

# maquette en pixels : 1280x720 px -> slide 13,33 x 7,5 in (96 px / in)
EMU_PAR_PX = 914400 / 96.0
ZONE = (48, 118, 1184, 516)          # zone de contenu sous l'en-tête

MOIS_FR = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet",
           "août", "septembre", "octobre", "novembre", "décembre"]


def P(px: float) -> Emu:
    return Emu(int(round(px * EMU_PAR_PX)))


def _pt(px: float) -> Pt:
    return Pt(round(px * 0.75, 1))    # px maquette -> points


def _date_fr() -> str:
    d = date.today()
    return f"{MOIS_FR[d.month - 1].capitalize()} {d.year}"


def _logo() -> Path:
    return config.REPO_ROOT / "app" / "static" / "img" / "greenvolt_logo.png"


def _ajouter_logo(slide, x, y, height):
    """Logo GV s'il est présent : son absence (déploiement incomplet) ne doit
    pas faire échouer tout l'assemblage."""
    chemin = _logo()
    if chemin.exists():
        slide.shapes.add_picture(str(chemin), x, y, height=height)


# deux assemblages simultanés du même projet écrivaient les mêmes fichiers
# (embed_*.jpg, Dossier_DP_<id>.pptx) : PPTX potentiellement corrompu. Un
# verrou par projet sérialise la génération.
_VERROUS_DOSSIER: dict[str, threading.Lock] = {}
_VERROUS_DOSSIER_GARDE = threading.Lock()


def _verrou_dossier(projet_id: str) -> threading.Lock:
    with _VERROUS_DOSSIER_GARDE:
        return _VERROUS_DOSSIER.setdefault(projet_id, threading.Lock())


# ------------------------------------------------------------------ primitives

def _slide(prs):
    return prs.slides.add_slide(prs.slide_layouts[6])  # vierge


def _texte(slide, x, y, w, h, contenu, px=13, gras=False, couleur=NAVY,
           centre=False, droite=False, interligne=1.35):
    box = slide.shapes.add_textbox(P(x), P(y), P(w), P(h))
    tf = box.text_frame
    tf.word_wrap = True
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    lignes = contenu if isinstance(contenu, list) else [contenu]
    for i, ligne in enumerate(lignes):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.text = str(ligne)
        p.line_spacing = interligne
        p.font.size = _pt(px)
        p.font.bold = gras
        p.font.color.rgb = couleur
        p.font.name = POLICE
        if centre:
            p.alignment = PP_ALIGN.CENTER
        elif droite:
            p.alignment = PP_ALIGN.RIGHT
    return box


def _rect(slide, x, y, w, h, fill=None, ligne=None, epaisseur=1.0,
          tirets=False, arrondi=False, radius=0.25):
    forme = slide.shapes.add_shape(
        MSO_SHAPE.ROUNDED_RECTANGLE if arrondi else MSO_SHAPE.RECTANGLE,
        P(x), P(y), P(w), P(h))
    forme.shadow.inherit = False
    if arrondi:
        try:
            forme.adjustments[0] = radius
        except (IndexError, ValueError):
            pass
    if fill is None:
        forme.fill.background()
    else:
        forme.fill.solid()
        forme.fill.fore_color.rgb = fill
    if ligne is None:
        forme.line.fill.background()
    else:
        forme.line.color.rgb = ligne
        forme.line.width = Pt(epaisseur)
        if tirets:
            forme.line.dash_style = MSO_LINE.DASH
    return forme


def _pastille_texte(forme, texte, px, couleur, gras=True):
    tf = forme.text_frame
    tf.word_wrap = False
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    tf.vertical_anchor = MSO_ANCHOR.MIDDLE
    p = tf.paragraphs[0]
    p.text = texte
    p.alignment = PP_ALIGN.CENTER
    p.font.size = _pt(px)
    p.font.bold = gras
    p.font.color.rgb = couleur
    p.font.name = POLICE


def _badge(slide, texte, bg=VERT, fg=NAVY):
    """Badge de pièce, aligné à droite du cartouche."""
    w = 30 + 10 * len(texte)
    forme = _rect(slide, 1280 - 22 - w, 671, w, 30, fill=bg, arrondi=True, radius=0.28)
    _pastille_texte(forme, texte, 15, fg)


def _pastille(slide, x, y, texte, bg, fg, w=None, h=26):
    w = w or (28 + 8 * len(texte))
    forme = _rect(slide, x, y, w, h, fill=bg, arrondi=True, radius=0.5)
    _pastille_texte(forme, texte, 13, fg)


def _entete(slide, titre):
    """Titre de planche. Pas de mention en haut à droite (décision 17/07/2026)."""
    _texte(slide, 48, 44, 1136, 46, titre, px=34, gras=True)


def _cartouche(slide, projet, badge_txt, badge_bg=VERT, badge_fg=NAVY):
    _rect(slide, 0, 650, 1280, 1.2, fill=GRIS_LIGNE)
    _ajouter_logo(slide, P(22), P(675), height=P(21))
    loc = projet.get("localisation") or {}
    mo = (projet.get("mo") or {}).get("raison_sociale") or projet.get("nom") or ""
    sous = " · ".join(filter(None, [mo, loc.get("adresse")]))
    _texte(slide, 320, 663, 640, 20, "Construction d'une ombrière photovoltaïque",
           px=12, gras=True, centre=True)
    _texte(slide, 320, 683, 640, 18, sous, px=12, couleur=MUTED, centre=True)
    _texte(slide, 980, 677, 130, 20, _date_fr(), px=12, couleur=MUTED, droite=True)
    _badge(slide, badge_txt, badge_bg, badge_fg)


def _zone_cadre(slide, zone=ZONE):
    x, y, w, h = zone
    _rect(slide, x, y, w, h, fill=BLANC, ligne=GRIS_LIGNE, epaisseur=1)


def _image_zone(slide, chemin: Path, zone=ZONE, pad=6):
    x, y, w, h = zone
    with Image.open(chemin) as im:
        iw, ih = im.size
    x, y, w, h = x + pad, y + pad, w - 2 * pad, h - 2 * pad
    r = min(w / iw, h / ih)
    w2, h2 = iw * r, ih * r
    slide.shapes.add_picture(str(chemin), P(x + (w - w2) / 2), P(y + (h - h2) / 2),
                             P(w2), P(h2))


def _placeholder_zone(slide, titre, detail, zone=ZONE, note=""):
    """Emplacement réservé (pièce BE manquante), style maquette."""
    x, y, w, h = zone
    _rect(slide, x, y, w, h, fill=FOND_CLAIR, ligne=GRIS_TIRETS,
          epaisseur=1.5, tirets=True, arrondi=True, radius=0.04)
    cy = y + h / 2
    _texte(slide, x, cy - 46, w, 30, titre, px=20, gras=True, centre=True)
    _texte(slide, x + (w - 620) / 2, cy - 6, 620, 54, detail, px=13,
           couleur=MUTED, centre=True)
    if note:
        _texte(slide, x, cy + 58, w, 22, note.upper(), px=12, gras=True,
               couleur=VIOLET, centre=True)


# ------------------------------------------------------------------ utilitaires

def _gradient_v_png(assets: Path) -> Path:
    """Barre verticale dégradée vert -> violet (page de garde)."""
    chemin = assets / "_gradient_v.png"
    if not chemin.exists():
        img = Image.new("RGB", (14, 720))
        for yy in range(720):
            t = yy / 719
            c = tuple(round(a + (b - a) * t) for a, b in zip((5, 219, 121), (119, 109, 248)))
            img.paste(c, (0, yy, 14, yy + 1))
        img.save(chemin)
    return chemin


def _optimiser(chemin: Path, assets: Path) -> Path:
    """Compresse les grosses images pour un dossier léger.

    PNG volumineux (orthophotos) -> JPEG ; photos très haute résolution
    (téléphone) -> redimensionnées à 2400 px de large (~150 dpi sur A3),
    largement suffisant pour le dépôt. Les dessins légers restent en PNG.
    """
    try:
        taille = chemin.stat().st_size
        est_png = chemin.suffix.lower() == ".png"
        with Image.open(chemin) as im:
            orientation = im.getexif().get(274, 1)  # tag EXIF Orientation
            gros = taille > (400_000 if est_png else 900_000)
            if not gros and orientation == 1:
                return chemin
            sortie = assets / f"embed_{chemin.stem}.jpg"
            if (not sortie.exists()
                    or sortie.stat().st_mtime < chemin.stat().st_mtime):
                im = ImageOps.exif_transpose(im).convert("RGB")
                if im.width > 2400:
                    im = im.resize((2400, round(im.height * 2400 / im.width)),
                                   Image.LANCZOS)
                im.save(sortie, "JPEG", quality=87)
        return sortie
    except OSError:
        return chemin


def _pdf_en_images(chemin_pdf: Path, assets: Path, prefixe: str,
                   max_pages: int = 3) -> list[Path]:
    """Rend les premières pages d'un PDF uploadé en JPEG (150 dpi).

    Cache par date du PDF : sans lui, chaque assemblage re-rendait TOUS les
    PDF du dossier (et la DP6 plusieurs fois), sous le verrou pdfium global
    qui gèle en plus les autres requêtes pendant ce temps.
    """
    temoin = assets / f"{prefixe}_p1.jpg"
    if temoin.exists() and temoin.stat().st_mtime >= chemin_pdf.stat().st_mtime:
        pages = sorted(assets.glob(f"{prefixe}_p*.jpg"))
        if pages:
            return pages[:max_pages]
    for ancien in assets.glob(f"{prefixe}_p*.jpg"):  # purge des pages périmées
        ancien.unlink(missing_ok=True)
    sorties = []
    with config.PDFIUM_LOCK:
        doc = pdfium.PdfDocument(str(chemin_pdf))
        try:
            for i in range(min(len(doc), max_pages)):
                image = doc[i].render(scale=150 / 72).to_pil()
                chemin = assets / f"{prefixe}_p{i + 1}.jpg"
                image.convert("RGB").save(chemin, "JPEG", quality=88)
                sorties.append(chemin)
        finally:
            doc.close()
    return sorties


def _fichier_document(projet: dict, code: str) -> Path | None:
    doc = (projet.get("documents") or {}).get(code)
    if not doc:
        return None
    chemin = config.PROJETS_DIR / doc["fichier"]
    return chemin if chemin.exists() else None


def _pages_document(projet: dict, code: str, assets: Path) -> list[Path]:
    """Fichier uploadé -> liste d'images (pages PDF rendues ou image seule)."""
    chemin = _fichier_document(projet, code)
    if not chemin:
        return []
    if chemin.suffix.lower() == ".pdf":
        return _pdf_en_images(chemin, assets, code)
    return [chemin]


# ------------------------------------------------------------------ planches

def _slide_garde(prs, assets, projet, evaluation):
    slide = _slide(prs)
    slide.shapes.add_picture(str(_gradient_v_png(assets)), 0, 0, P(14), P(720))
    _ajouter_logo(slide, P(80), P(60), height=P(52))

    reg = evaluation["regime"]
    _texte(slide, 80, 170, 900, 22,
           f"DOSSIER D'URBANISME · CERFA {reg['cerfa']}", px=14, gras=True,
           couleur=VIOLET)
    titre = ["Déclaration", "préalable"] if reg["regime"] == "DP" else ["Permis de", "construire"]
    _texte(slide, 80, 198, 900, 150, titre, px=64, gras=True, interligne=1.05)
    _texte(slide, 80, 352, 900, 34,
           "Construction d'une ombrière photovoltaïque", px=22, couleur=SOFT)
    _rect(slide, 80, 410, 64, 3, fill=VERT)

    loc = projet.get("localisation") or {}
    mo = projet.get("mo") or {}
    omb = projet.get("ombriere") or {}
    parcelles = loc.get("parcelles") or []
    parc_txt = " · ".join(
        f"Section {p.get('section')} n° {p.get('numero')}" for p in parcelles[:4]
    ) or "—"
    lignes = [
        ("Maître d'ouvrage",
         " — ".join(filter(None, [mo.get("raison_sociale"), mo.get("adresse")])) or "—"),
        ("Adresse du projet", loc.get("adresse") or "—"),
        ("Parcelle" + ("s" if len(parcelles) > 1 else ""), parc_txt),
    ]
    if omb.get("puissance_kwc"):
        lignes.append(("Puissance", f"{omb['puissance_kwc']:g} kWc"))
    lignes.append(("Date", _date_fr()))

    y = 442
    for lab, val in lignes:
        _texte(slide, 80, y, 180, 20, lab, px=14, gras=True, couleur=VIOLET)
        _texte(slide, 274, y, 860, 20, val, px=14, couleur=SOFT)
        y += 33
    _texte(slide, 80, 640, 640, 30,
           "Document établi pour l'obtention de l'autorisation d'urbanisme. "
           "Ne vaut pas plan d'exécution.", px=12, couleur=MUTED)
    return slide


def _slide_piece_image(prs, projet, assets, titre, badge, image=None,
                       placeholder=("Pièce en attente", "À fournir par le bureau d'études (étape 2).", ""),
                       pastille=None):
    slide = _slide(prs)
    _entete(slide, titre)
    if image:
        _zone_cadre(slide)
        _image_zone(slide, _optimiser(image, assets))
    else:
        _placeholder_zone(slide, placeholder[0], placeholder[1], note=placeholder[2])
    if pastille:
        _pastille(slide, ZONE[0] + 14, ZONE[1] + 14, pastille, VIOLET, BLANC)
    _cartouche(slide, projet, badge)
    return slide


def _slide_dp1_fusion(prs, projet, assets, generees):
    """DP1 : plan de situation + plan cadastral, moitié / moitié."""
    a, b = generees.get("dp1_situation"), generees.get("dp1_cadastral")
    slide = _slide(prs)
    _entete(slide, "Plan de situation et cadastral")
    demi = (ZONE[2] - 24) / 2
    zones = [(ZONE[0], ZONE[1] + 26, demi, ZONE[3] - 26),
             (ZONE[0] + demi + 24, ZONE[1] + 26, demi, ZONE[3] - 26)]
    for (img, label), zone in zip([(a, "Plan de situation"), (b, "Plan cadastral")], zones):
        _texte(slide, zone[0], ZONE[1] - 4, zone[2], 20, label, px=13, gras=True, couleur=MUTED)
        if img:
            _rect(slide, *zone, fill=BLANC, ligne=GRIS_LIGNE, epaisseur=1)
            _image_zone(slide, _optimiser(img, assets), zone)
        else:
            _placeholder_zone(slide, "Planche indisponible",
                              "Complétez la localisation (étape 1) puis régénérez.", zone)
    _cartouche(slide, projet, "DP1")
    return slide


def _slide_notice(prs, projet, assets):
    """Notice descriptive en deux colonnes (style maquette)."""
    TITRES = {
        "presentation": "Objet de la demande",
        "etat_initial": "Le site",
        "description": "Le projet",
        "reglementaire": "Contexte réglementaire",
        "insertion": "Insertion paysagère",
        "acces_reseaux": "Accès et raccordement aux réseaux",
        "chantier": "Chantier et remise en état",
    }
    COL1 = ["presentation", "etat_initial", "description", "insertion"]
    COL2 = ["reglementaire", "acces_reseaux", "chantier"]

    sections = (projet.get("notice") or {}).get("sections") or {}
    slide = _slide(prs)
    _entete(slide, "Notice descriptive")
    if not any((sections.get(c) or "").strip() for c in TITRES):
        _placeholder_zone(slide, "Notice à générer",
                          "Générez et relisez la notice à l'étape 5 de l'outil avant l'export.")
        _cartouche(slide, projet, "Notice", VIOLET, BLANC)
        return slide

    _rect(slide, 640, ZONE[1] + 4, 1, ZONE[3] - 8, fill=GRIS_LIGNE)  # séparateur

    def px_ajuste(cles, largeur_px=560, hauteur_px=ZONE[3] - 4):
        """Taille de police qui fait tenir la colonne dans son cadre.

        Les zones de texte sont à hauteur FIXE : une notice longue débordait
        sous le cartouche. Estimation lignes = titres + texte replié à la
        largeur de colonne ; on descend la police (12 -> 8,5 px) jusqu'à tenir.
        """
        px = 12.0
        while px > 8.5:
            cpl = max(30, largeur_px / (px * 0.55))   # ~caractères par ligne
            lignes = 0.0
            for cle in cles:
                txt = (sections.get(cle) or "").strip()
                if txt:
                    lignes += 2.2 + math.ceil(len(txt) / cpl)  # titre + espaces
            if lignes * px * 1.32 <= hauteur_px:
                break
            px -= 0.5
        return px

    px_corps = min(px_ajuste(COL1), px_ajuste(COL2))

    def colonne(cles, x, w):
        box = slide.shapes.add_textbox(P(x), P(ZONE[1] + 2), P(w), P(ZONE[3] - 4))
        tf = box.text_frame
        tf.word_wrap = True
        tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
        premier = True
        for cle in cles:
            txt = (sections.get(cle) or "").strip()
            if not txt:
                continue
            pt = tf.paragraphs[0] if premier else tf.add_paragraph()
            premier = False
            pt.text = TITRES[cle]
            pt.font.size = _pt(px_corps + 2)
            pt.font.bold = True
            pt.font.color.rgb = NAVY
            pt.font.name = POLICE
            pt.space_after = Pt(2)
            pb = tf.add_paragraph()
            pb.text = txt
            pb.font.size = _pt(px_corps)
            pb.font.color.rgb = SOFT
            pb.font.name = POLICE
            pb.line_spacing = 1.3
            pb.space_after = Pt(10)

    colonne(COL1, ZONE[0], 560)
    colonne(COL2, 672, 560)
    _cartouche(slide, projet, "Notice", VIOLET, BLANC)
    return slide


def _insertion_pour_apres(projet, assets):
    """Ce qui illustre l'état projeté sur la planche avant / après.

    Depuis le retrait du module Insertion (01/09/2026), il n'y a plus qu'une
    seule source possible : le photomontage DP6 déposé par le bureau d'études.
    C'est la pièce réglementaire, et c'était déjà elle qui primait.

    Renvoie le chemin, ou None si la pièce n'a pas encore été déposée.
    """
    pages = _pages_document(projet, "dp6", assets)
    return pages[0] if pages else None


def _photo_pour_avant(projet, assets):
    """Ce qui illustre l'état existant sur la planche avant / après.

    C'est la pièce DP7 (photo de l'environnement proche), c'est-à-dire la
    photo du parking actuel. Avant le retrait du module Insertion, l'« avant »
    venait de `insertion.photo`, une photo déposée dans l'écran Insertion, et
    le texte de l'emplacement réservé promettait déjà un repli sur la DP7 qui
    n'existait pas dans le code. Ce repli est désormais le chemin normal, et
    il évite de redemander deux fois la même photo au BE.
    """
    pages = _pages_document(projet, "dp7", assets)
    return pages[0] if pages else None


def _slide_dp6(prs, projet, assets, avant, apres):
    """Insertion paysagère avant / après.

    `avant` et `apres` sont calculés UNE fois par l'appelant : les recalculer
    ici re-rendait les pages PDF à chaque planche.
    """
    slide = _slide(prs)
    _entete(slide, "Insertion paysagère")
    demi = (ZONE[2] - 24) / 2
    z_avant = (ZONE[0], ZONE[1], demi, ZONE[3])
    z_apres = (ZONE[0] + demi + 24, ZONE[1], demi, ZONE[3])

    # avant : la photo du parking actuel, pièce DP7
    if avant:
        _rect(slide, *z_avant, fill=BLANC, ligne=GRIS_LIGNE, epaisseur=1)
        _image_zone(slide, _optimiser(avant, assets), z_avant)
    else:
        _placeholder_zone(slide, "État existant",
                          "Photo du parking actuel (pièce DP7, étape 2).", z_avant)
    _pastille(slide, z_avant[0] + 14, z_avant[1] + 14, "Avant", NAVY, BLANC)

    # après : le photomontage d'insertion du bureau d'études, pièce DP6
    if apres:
        _rect(slide, *z_apres, fill=BLANC, ligne=GRIS_LIGNE, epaisseur=1)
        _image_zone(slide, _optimiser(apres, assets), z_apres)
    else:
        _placeholder_zone(slide, "État projeté",
                          "Photomontage d'insertion fourni par le bureau d'études (pièce DP6, étape 2).",
                          z_apres)
    _pastille(slide, z_apres[0] + 14, z_apres[1] + 14, "Après", VERT, NAVY)
    _cartouche(slide, projet, "DP6")
    return slide


def _slide_photos(prs, projet, assets):
    """DP7 (proche) + DP8 (lointain) côte à côte, numérotées."""
    slide = _slide(prs)
    _entete(slide, "Photographies du terrain")
    demi = (ZONE[2] - 24) / 2
    zones = [(ZONE[0], ZONE[1], demi, ZONE[3]),
             (ZONE[0] + demi + 24, ZONE[1], demi, ZONE[3])]
    pieces = [("dp7", "1", "Environnement proche — DP7", VERT, NAVY),
              ("dp8", "2", "Paysage lointain — DP8", VIOLET, BLANC)]
    for (code, num, libelle, bg, fg), zone in zip(pieces, zones):
        pages = _pages_document(projet, code, assets)
        if pages:
            _rect(slide, *zone, fill=BLANC, ligne=GRIS_LIGNE, epaisseur=1)
            _image_zone(slide, _optimiser(pages[0], assets), zone)
        else:
            _placeholder_zone(slide, libelle,
                              "Photo datée et repérée, fournie par le BE (étape 2).", zone)
        rond = _rect(slide, zone[0] + 14, zone[1] + 14, 26, 26, fill=bg, arrondi=True, radius=0.5)
        _pastille_texte(rond, num, 13, fg)
        _texte(slide, zone[0] + 50, zone[1] + 17, zone[2] - 60, 20, libelle,
               px=13, gras=True, couleur=MUTED)
    _cartouche(slide, projet, "DP7/8")
    return slide


# ------------------------------------------------------------------ assemblage

def generer_dossier(projet: dict) -> tuple[Path, list[str]]:
    """Construit le PPTX complet (style maquette). Renvoie (chemin, avertissements)."""
    projet_id = projet.get("id")
    if not projet_id:
        raise ValueError("Projet sans identifiant : impossible d'assembler le dossier.")
    with _verrou_dossier(projet_id):
        return _generer_dossier_verrouille(projet, projet_id)


def _generer_dossier_verrouille(projet: dict, projet_id: str) -> tuple[Path, list[str]]:
    assets = config.assets_dir(projet_id)
    avertissements: list[str] = []

    evaluation = regles.evaluer(Projet.model_validate(projet))

    # 0. complétude AVANT génération : un dossier assemblé incomplet doit le
    # dire clairement, pas livrer un PPTX de placeholders en silence
    for piece in evaluation["completude"]["pieces"]:
        if piece["statut"] in ("en_attente", "a_generer", "a_completer"):
            avertissements.append(f"{piece['titre']} : {piece['detail']}")

    # 1. (re)générer les pièces automatiques
    generees: dict[str, Path] = {}
    for code, generateur in GENERATEURS.items():
        try:
            image = generateur(projet)
            chemin = assets / f"{code}.png"
            image.save(chemin, "PNG")
            generees[code] = chemin
        except (GeoApiError, ValueError) as exc:
            avertissements.append(f"{code} : {exc}")

    # 2. montage du PPTX (ordre de la maquette)
    prs = Presentation()
    prs.slide_width, prs.slide_height = P(1280), P(720)

    _slide_garde(prs, assets, projet, evaluation)
    _slide_dp1_fusion(prs, projet, assets, generees)
    _slide_piece_image(
        prs, projet, assets, "Vue aérienne", "DP1",
        image=generees.get("dp1_aerien"),
        placeholder=("Vue aérienne indisponible",
                     "Complétez la localisation (étape 1) puis régénérez.", ""))

    # DP2 plan de masse (upload BE, PDF multi-pages possible)
    pages = _pages_document(projet, "dp2", assets)
    if pages:
        for page in pages:
            _slide_piece_image(prs, projet, assets, "Plan de masse", "DP2", image=page)
    else:
        _slide_piece_image(
            prs, projet, assets, "Plan de masse", "DP2",
            placeholder=("Plan de masse fourni par le bureau d'études",
                         "Emplacement réservé à l'import du plan (étape 2). Y figurent "
                         "l'implantation de l'ombrière, les places de stationnement, les "
                         "accès et le raccordement aux réseaux.", "Pièce DP2"))

    # DP3 coupe : pièce du bureau d'études (upload étape 2).
    # UNE seule planche, la 1re page : une coupe est un dessin unique, et les
    # pages suivantes du PDF fourni sont en pratique d'autres pièces (constaté
    # sur Anse, dont la page 2 rejouait le plan de masse — remarque Florent).
    # Sans upload BE mais avec un type d'ombrière choisi : coupe PROVISOIRE
    # paramétrique, étiquetée comme telle — un dossier d'avant-vente
    # présentable plutôt qu'un placeholder (la vraie DP3 reste exigée au dépôt).
    titre_dp3 = "Coupe du terrain et de la construction"
    pages = _pages_document(projet, "dp3", assets)
    if pages:
        _slide_piece_image(prs, projet, assets, titre_dp3, "DP3", image=pages[0])
    else:
        provisoire = None
        if (projet.get("ombriere") or {}).get("famille"):
            try:
                from .planches.ombriere import dessiner_coupe
                image = dessiner_coupe(projet)
                provisoire = assets / "dp3_provisoire.png"
                image.save(provisoire, "PNG")
            except (ValueError, OSError):
                provisoire = None
        if provisoire:
            _slide_piece_image(prs, projet, assets, titre_dp3, "DP3",
                               image=provisoire,
                               pastille="Coupe provisoire — à remplacer par la DP3 du BE")
            avertissements.append(
                "DP3 : coupe provisoire paramétrique intégrée, à remplacer par "
                "la coupe du bureau d'études avant dépôt.")
        else:
            _slide_piece_image(
                prs, projet, assets, titre_dp3, "DP3",
                placeholder=(f"{titre_dp3} en attente",
                             "Coupe du projet fournie par le bureau d'études "
                             "(pièce DP3, étape 2).", "Pièce DP3"))

    _slide_notice(prs, projet, assets)
    # avant/après calculés UNE fois : chaque appel re-rend les pages PDF
    avant = _photo_pour_avant(projet, assets)
    apres = _insertion_pour_apres(projet, assets)
    _slide_dp6(prs, projet, assets, avant, apres)

    _slide_photos(prs, projet, assets)
    # Cerfa et checklist retirés du PPTX (Florent) : le Cerfa pré-rempli reste
    # un PDF joint au dépôt, la checklist reste dans l'outil (panneau complétude).

    chemin = assets / f"Dossier_DP_{projet_id}.pptx"
    tmp = assets / f"Dossier_DP_{projet_id}.pptx.tmp"
    prs.save(str(tmp))
    os.replace(tmp, chemin)   # atomique : jamais de PPTX à moitié écrit
    return chemin, avertissements
