"""Socle de rendu des planches (Pillow) : canvas 16:9, cartouche charte GV,
cotes, flèche nord, échelle graphique.

Les planches sont générées en 2560×1440 (16:9, ~155 dpi sur A3 paysage).
Le dossier final étant un PPTX 16:9 exporté en PDF, l'échelle nominale
« 1/X » est calculée pour une impression A3 (420 mm de large) et une
échelle GRAPHIQUE est toujours dessinée (elle seule fait foi, plan §10).
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

PLATE_W, PLATE_H = 2560, 1440
CARTOUCHE_H = 120
MARGE = 24
A3_LARGEUR_MM = 420.0
PX_PAR_MM = PLATE_W / A3_LARGEUR_MM  # ~6,1 px/mm à l'impression A3

# Charte officielle (plan §12)
VERT = (5, 219, 121)
VIOLET = (119, 109, 248)
NAVY = (0, 36, 85)
GRIS = (223, 223, 221)
ENCRE = (30, 42, 58)
MUTED = (100, 116, 139)
BLANC = (255, 255, 255)
BLEU_BANDE = (30, 107, 214)   # bandes bleues des poteaux START
PANNEAU = (16, 19, 26)        # modules full black

_FONT_CANDIDATES = [
    "C:/Windows/Fonts/segoeui.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    # Linux (conteneur Azure) : DejaVu / Liberation installées via le Dockerfile
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
]
_FONT_BOLD_CANDIDATES = [
    "C:/Windows/Fonts/segoeuib.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
]
_font_cache: dict[tuple, ImageFont.FreeTypeFont] = {}


def police(taille: int, gras: bool = False):
    cle = (taille, gras)
    if cle not in _font_cache:
        for cand in (_FONT_BOLD_CANDIDATES if gras else _FONT_CANDIDATES):
            if Path(cand).exists():
                _font_cache[cle] = ImageFont.truetype(cand, taille)
                break
        else:  # pragma: no cover - selon l'OS
            _font_cache[cle] = ImageFont.load_default(size=taille)
    return _font_cache[cle]


def gradient_h(w: int, h: int, c1=VERT, c2=VIOLET) -> Image.Image:
    """Bande dégradée vert → violet (marque)."""
    img = Image.new("RGB", (w, h))
    dr = ImageDraw.Draw(img)
    for x in range(w):
        t = x / max(w - 1, 1)
        c = tuple(round(a + (b - a) * t) for a, b in zip(c1, c2))
        dr.line([(x, 0), (x, h)], fill=c)
    return img


class Planche:
    """Canvas d'une pièce du dossier, avec cartouche standard en pied."""

    def __init__(self, titre: str, projet: dict, echelle_txt: str = "",
                 cartouche: bool = False):
        # cartouche=False par défaut : le dossier PPTX pose son propre cartouche
        # (charte maquette), on évite le double cartouche sur les planches.
        self.img = Image.new("RGB", (PLATE_W, PLATE_H), BLANC)
        self.draw = ImageDraw.Draw(self.img)
        self.titre = titre
        self.projet = projet
        self.echelle_txt = echelle_txt
        self.avec_cartouche = cartouche

    # zone utile (au-dessus du cartouche si présent)
    @property
    def content_box(self) -> tuple[int, int, int, int]:
        bas = PLATE_H - (CARTOUCHE_H if self.avec_cartouche else 0) - MARGE
        return (MARGE, MARGE, PLATE_W - MARGE, bas)

    def coller_contenu(self, image: Image.Image):
        """Colle une image (fond de carte...) centrée dans la zone utile."""
        x0, y0, x1, y1 = self.content_box
        w, h = x1 - x0, y1 - y0
        img = image if image.size == (w, h) else image.resize((w, h), Image.LANCZOS)
        self.img.paste(img, (x0, y0))

    def cadre_contenu(self):
        self.draw.rectangle(self.content_box, outline=GRIS, width=2)

    def fleche_nord(self, cx: int | None = None, cy: int | None = None):
        x0, y0, x1, _ = self.content_box
        cx = cx if cx is not None else x1 - 60
        cy = cy if cy is not None else y0 + 70
        r = 38
        self.draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=BLANC, outline=NAVY, width=3)
        self.draw.polygon(
            [(cx, cy - r + 10), (cx - 12, cy + 12), (cx, cy + 2), (cx + 12, cy + 12)],
            fill=NAVY,
        )
        self.draw.text((cx, cy + r - 18), "N", font=police(24, True), fill=NAVY, anchor="mm")

    def echelle_graphique(self, m_par_px: float, x: int | None = None, y: int | None = None):
        """Barre d'échelle : longueur « ronde » visant ~300 px."""
        x0, _, _, y1 = self.content_box
        x = x if x is not None else x0 + 24
        y = y if y is not None else y1 - 48
        cible_m = 300 * m_par_px
        for m in (5, 10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000, 5000):
            if m >= cible_m:
                break
        px = m / m_par_px
        lbl = f"{m} m" if m < 1000 else f"{m / 1000:g} km"
        self.draw.rectangle([x - 10, y - 26, x + px + 84, y + 20], fill=BLANC, outline=GRIS)
        moitie = px / 2
        self.draw.rectangle([x, y, x + moitie, y + 10], fill=NAVY)
        self.draw.rectangle([x + moitie, y, x + px, y + 10], outline=NAVY, width=2)
        self.draw.text((x, y - 18), "0", font=police(20), fill=NAVY)
        self.draw.text((x + px + 8, y - 4), lbl, font=police(20, True), fill=NAVY)

    def source(self, texte: str):
        _, _, x1, y1 = self.content_box
        f = police(18)
        w = self.draw.textlength(texte, font=f)
        self.draw.rectangle([x1 - w - 20, y1 - 34, x1 - 4, y1 - 6], fill=(255, 255, 255))
        self.draw.text((x1 - w - 12, y1 - 30), texte, font=f, fill=MUTED)

    def cartouche(self):
        """Bandeau de pied : marque, pièce, projet, échelle, date."""
        y0 = PLATE_H - CARTOUCHE_H
        self.draw.rectangle([0, y0, PLATE_W, PLATE_H], fill=BLANC)
        self.img.paste(gradient_h(PLATE_W, 6), (0, y0))
        self.draw.line([(0, y0), (PLATE_W, y0)], fill=GRIS, width=1)

        # marque
        self.draw.text((MARGE, y0 + 30), "greenvolt", font=police(44, True), fill=NAVY)
        w = self.draw.textlength("greenvolt", font=police(44, True))
        self.draw.text((MARGE + w + 12, y0 + 44), "next", font=police(28, True), fill=VIOLET)

        # pièce + projet
        loc = self.projet.get("localisation") or {}
        sous = " · ".join(
            filter(None, [self.projet.get("nom"), loc.get("commune"),
                          f"INSEE {loc.get('code_insee')}" if loc.get("code_insee") else None])
        )
        self.draw.text((520, y0 + 22), self.titre, font=police(34, True), fill=NAVY)
        self.draw.text((520, y0 + 66), sous, font=police(24), fill=MUTED)

        # échelle + date
        droite = f"{self.echelle_txt}   ·   {date.today().strftime('%d/%m/%Y')}   ·   GV_DP"
        f = police(24)
        wd = self.draw.textlength(droite, font=f)
        self.draw.text((PLATE_W - MARGE - wd, y0 + 44), droite, font=f, fill=ENCRE)

    def finaliser(self, cadre: bool = True) -> Image.Image:
        if cadre:
            self.cadre_contenu()
        if self.avec_cartouche:
            self.cartouche()
        return self.img


def echelle_nominale(m_par_px: float) -> str:
    """Échelle « 1/X » pour une impression A3 paysage (indicative)."""
    denom = m_par_px * PX_PAR_MM * 1000.0
    return f"1/{round(denom):,}".replace(",", " ")


# ---- cotes (dessins techniques DP3/DP4) ----

def cote_verticale(dr: ImageDraw.ImageDraw, x: int, y_a: int, y_b: int, texte: str,
                   cote_a_gauche: bool = True):
    y_min, y_max = min(y_a, y_b), max(y_a, y_b)
    dr.line([(x, y_min), (x, y_max)], fill=NAVY, width=2)
    for y in (y_min, y_max):
        dr.line([(x - 8, y), (x + 8, y)], fill=NAVY, width=2)
    f = police(24)
    tx = x - 14 if cote_a_gauche else x + 14
    ancre = "rm" if cote_a_gauche else "lm"
    dr.text((tx, (y_min + y_max) // 2), texte, font=f, fill=NAVY, anchor=ancre)


def cote_horizontale(dr: ImageDraw.ImageDraw, x_a: int, x_b: int, y: int, texte: str):
    x_min, x_max = min(x_a, x_b), max(x_a, x_b)
    dr.line([(x_min, y), (x_max, y)], fill=NAVY, width=2)
    for x in (x_min, x_max):
        dr.line([(x, y - 8), (x, y + 8)], fill=NAVY, width=2)
    dr.text(((x_min + x_max) // 2, y + 14), texte, font=police(24), fill=NAVY, anchor="ma")
