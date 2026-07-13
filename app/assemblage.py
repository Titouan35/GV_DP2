"""Assemblage du dossier : PowerPoint 16:9 à la charte GV, une slide par pièce.

Le .pptx est le format de travail ; l'export PDF (dépôt) se fait via
PowerPoint installé sur le poste (export_pdf.py). Les pièces générées
(DP1, DP3, DP4) sont régénérées à l'assemblage ; les pièces BE viennent
des uploads (PDF rendus en image via pypdfium2).
"""
from __future__ import annotations

import io
from datetime import date
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.util import Emu, Inches, Pt

from . import config, regles
from .geo.client import GeoApiError
from .models import Projet
from .notice import SECTIONS
from .planches import GENERATEURS
from .planches.base import gradient_h

NAVY = RGBColor(0x00, 0x24, 0x55)
VERT = RGBColor(0x05, 0xDB, 0x79)
VIOLET = RGBColor(0x77, 0x6D, 0xF8)
MUTED = RGBColor(0x64, 0x74, 0x8B)
POLICE = "Poppins"

SLIDE_W, SLIDE_H = Inches(13.333), Inches(7.5)


def _slide(prs):
    return prs.slides.add_slide(prs.slide_layouts[6])  # vierge


def _texte(slide, x, y, w, h, contenu, taille=18, gras=False, couleur=NAVY,
           centre=False):
    box = slide.shapes.add_textbox(x, y, w, h)
    tf = box.text_frame
    tf.word_wrap = True
    lignes = contenu.split("\n") if isinstance(contenu, str) else contenu
    for i, ligne in enumerate(lignes):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.text = ligne
        p.font.size = Pt(taille)
        p.font.bold = gras
        p.font.color.rgb = couleur
        p.font.name = POLICE
        if centre:
            p.alignment = 2  # PP_ALIGN.CENTER
    return box


def _gradient_png(assets: Path) -> Path:
    chemin = assets / "_gradient.png"
    if not chemin.exists():
        gradient_h(1600, 24).save(chemin)
    return chemin


def _bandeau(slide, assets: Path, titre: str, sous_titre: str = ""):
    slide.shapes.add_picture(str(_gradient_png(assets)), 0, 0, SLIDE_W, Emu(60000))
    _texte(slide, Inches(0.5), Inches(0.22), Inches(10.5), Inches(0.6),
           titre, taille=24, gras=True)
    if sous_titre:
        _texte(slide, Inches(0.5), Inches(0.78), Inches(12.3), Inches(0.4),
               sous_titre, taille=12, couleur=MUTED)
    _texte(slide, Inches(11.2), Inches(0.28), Inches(1.9), Inches(0.5),
           "greenvolt next", taille=14, gras=True, couleur=VIOLET)


def _image_plein_cadre(slide, chemin_img: Path):
    """Insère une image ajustée dans la zone sous le bandeau."""
    with Image.open(chemin_img) as im:
        iw, ih = im.size
    zone = (Inches(0.35), Inches(1.25), Inches(12.63), Inches(6.05))
    ratio = min(zone[2] / iw, zone[3] / ih)
    w, h = int(iw * ratio), int(ih * ratio)
    x = int(zone[0] + (zone[2] - w) / 2)
    y = int(zone[1] + (zone[3] - h) / 2)
    slide.shapes.add_picture(str(chemin_img), x, y, w, h)


def _pdf_en_images(chemin_pdf: Path, assets: Path, prefixe: str,
                   max_pages: int = 3) -> list[Path]:
    """Rend les premières pages d'un PDF uploadé en PNG (150 dpi)."""
    sorties = []
    doc = pdfium.PdfDocument(str(chemin_pdf))
    try:
        for i in range(min(len(doc), max_pages)):
            image = doc[i].render(scale=150 / 72).to_pil()
            chemin = assets / f"{prefixe}_p{i + 1}.png"
            image.save(chemin)
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


def _slide_placeholder(prs, assets, piece):
    slide = _slide(prs)
    _bandeau(slide, assets, piece["titre"])
    _texte(slide, Inches(2.5), Inches(3.2), Inches(8.3), Inches(1.2),
           "Pièce en attente : à fournir par le bureau d'études (étape 4)"
           if piece["mode"] != "auto" else
           "Pièce à générer : complétez les étapes correspondantes de l'outil",
           taille=18, couleur=MUTED, centre=True)
    return slide


def generer_dossier(projet: dict) -> tuple[Path, list[str]]:
    """Construit le PPTX complet. Renvoie (chemin, avertissements)."""
    projet_id = projet["id"]
    assets = config.assets_dir(projet_id)
    avertissements: list[str] = []
    loc = projet.get("localisation") or {}

    # 1. (re)générer les pièces automatiques
    generees: dict[str, Path] = {}
    for code, generateur in GENERATEURS.items():
        if code in ("dp3_coupe", "dp4_facades") and not (projet.get("ombriere") or {}).get("famille"):
            continue
        try:
            image = generateur(projet)
            chemin = assets / f"{code}.png"
            image.save(chemin, "PNG")
            generees[code] = chemin
        except (GeoApiError, ValueError) as exc:
            avertissements.append(f"{code} : {exc}")

    # 2. montage du PPTX
    prs = Presentation()
    prs.slide_width, prs.slide_height = SLIDE_W, SLIDE_H

    # page de garde
    slide = _slide(prs)
    slide.shapes.add_picture(str(_gradient_png(assets)), 0, 0, SLIDE_W, Emu(160000))
    _texte(slide, Inches(0.9), Inches(1.1), Inches(6), Inches(0.6),
           "greenvolt next", taille=26, gras=True, couleur=VIOLET)
    _texte(slide, Inches(0.9), Inches(2.2), Inches(11.5), Inches(1.4),
           "Dossier de déclaration préalable\nOmbrières photovoltaïques de parking",
           taille=34, gras=True)
    evaluation = regles.evaluer(Projet.model_validate(projet))
    infos = [
        projet.get("nom") or "",
        f"{loc.get('adresse') or ''}",
        f"Commune : {loc.get('commune') or '—'} · INSEE {loc.get('code_insee') or '—'}",
        f"Maître d'ouvrage : {(projet.get('mo') or {}).get('raison_sociale') or '—'}",
        f"Régime : {evaluation['regime']['regime']} · Cerfa n° {evaluation['regime']['cerfa']}",
        f"Édité le {date.today().strftime('%d/%m/%Y')} avec GV_DP",
    ]
    _texte(slide, Inches(0.9), Inches(4.1), Inches(11.5), Inches(2.6),
           infos, taille=16, couleur=NAVY)

    sous_titre_std = " · ".join(filter(None, [projet.get("nom"), loc.get("commune")]))

    # pièces dans l'ordre réglementaire
    for piece in regles.PIECES_DP:
        code = piece["code"]
        if code == "garde":
            continue

        if code in ("dp1_situation", "dp1_cadastral", "dp1_aerien"):
            if code in generees:
                slide = _slide(prs)
                _bandeau(slide, assets, piece["titre"], sous_titre_std)
                _image_plein_cadre(slide, generees[code])
            else:
                _slide_placeholder(prs, assets, piece)
            continue

        if code in ("dp3", "dp4"):
            upload = _fichier_document(projet, code)
            if upload:  # repli BE prioritaire sur le paramétrique
                pages = (_pdf_en_images(upload, assets, code)
                         if upload.suffix.lower() == ".pdf" else [upload])
                for page_img in pages:
                    slide = _slide(prs)
                    _bandeau(slide, assets, piece["titre"] + " (pièce BE)", sous_titre_std)
                    _image_plein_cadre(slide, page_img)
                continue
            code_gen = "dp3_coupe" if code == "dp3" else "dp4_facades"
            if code_gen in generees:
                slide = _slide(prs)
                _bandeau(slide, assets, piece["titre"], sous_titre_std)
                _image_plein_cadre(slide, generees[code_gen])
            else:
                _slide_placeholder(prs, assets, piece)
            continue

        if code in ("dp2", "dp6", "dp7", "dp8"):
            upload = _fichier_document(projet, code)
            if upload:
                pages = (_pdf_en_images(upload, assets, code)
                         if upload.suffix.lower() == ".pdf" else [upload])
                for page_img in pages:
                    slide = _slide(prs)
                    _bandeau(slide, assets, piece["titre"], sous_titre_std)
                    _image_plein_cadre(slide, page_img)
            else:
                _slide_placeholder(prs, assets, piece)
            continue

        if code == "dp11":
            sections = (projet.get("notice") or {}).get("sections") or {}
            if not any(sections.values()):
                _slide_placeholder(prs, assets, piece)
                continue
            paires = [SECTIONS[i:i + 2] for i in range(0, len(SECTIONS), 2)]
            for i, paire in enumerate(paires):
                slide = _slide(prs)
                _bandeau(slide, assets, f"{piece['titre']} ({i + 1}/{len(paires)})", sous_titre_std)
                y = Inches(1.35)
                for cle, titre_sec in paire:
                    _texte(slide, Inches(0.6), y, Inches(12.1), Inches(0.4),
                           titre_sec, taille=15, gras=True)
                    _texte(slide, Inches(0.6), y + Inches(0.42), Inches(12.1), Inches(2.1),
                           sections.get(cle) or "—", taille=12, couleur=RGBColor(0x1E, 0x2A, 0x3A))
                    y += Inches(2.9)
            continue

        if code == "cerfa":
            chemin_cerfa = assets / "cerfa_16702_prerempli.pdf"
            if chemin_cerfa.exists():
                page1 = _pdf_en_images(chemin_cerfa, assets, "cerfa", max_pages=1)[0]
                slide = _slide(prs)
                _bandeau(slide, assets, piece["titre"], sous_titre_std)
                _image_plein_cadre(slide, page1)
                _texte(slide, Inches(0.6), Inches(6.95), Inches(12.1), Inches(0.4),
                       "Cerfa complet joint au dépôt (PDF pré-rempli, à relire) — le 13404 est remplacé par le 16702*03.",
                       taille=11, couleur=MUTED)
            else:
                _slide_placeholder(prs, assets, piece)
            continue

    # checklist finale
    completude = evaluation["completude"]
    slide = _slide(prs)
    _bandeau(slide, assets, "Checklist du dossier", sous_titre_std)
    lignes = [
        f"{'✔' if p['statut'] == 'prete' else '•'}  {p['titre']} — {p['statut'].replace('_', ' ')} ({p['detail']})"
        for p in completude["pieces"]
    ]
    lignes.append("")
    lignes.append(f"{completude['pretes']}/{completude['total']} pièces prêtes.")
    if avertissements:
        lignes.append("Avertissements : " + " ; ".join(avertissements))
    _texte(slide, Inches(0.7), Inches(1.4), Inches(12), Inches(5.6),
           lignes, taille=14)

    chemin = assets / f"Dossier_DP_{projet_id}.pptx"
    prs.save(str(chemin))
    return chemin, avertissements
