"""Signature du demandeur, apposée dans un PDF.

Demande du 30/09/2026 : les formulaires partent signés du représentant légal
de Greenvolt Next France. Deux sources, par ordre de préférence :

1. `app/gabarits/signature_mo.png` — une VRAIE signature scannée, déposée là
   par le BE. C'est la voie recommandée : ce qui sort de l'outil est alors la
   signature de la personne, pas un dessin.
2. À défaut, un PARAPHE GÉNÉRIQUE : un tracé dessiné par le code, à l'encre
   bleue, sans nom ni initiale (demande du 30/09/2026, confirmée le
   28/10/2026 : le nom en police manuscrite a été écarté). Depuis le
   28/10/2026, tracé « à la plume » plus manuscrit (proposition B). Déterministe : le même à chaque
   génération. Il ne représente personne et ne cherche à imiter aucun tracé.

L'image est posée dans le PDF par pdfium (objet image + matrice) AVANT que
pypdf remplisse les champs : l'image devient du contenu de page, que la
copie du gabarit emporte sans effort. L'inverse (remplir puis tamponner)
ferait dépendre les valeurs de formulaire de la sauvegarde pdfium.
"""
from __future__ import annotations

import logging
import math
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image, ImageDraw

from . import config

logger = logging.getLogger(__name__)

SIGNATURE_SCANNEE = config.REPO_ROOT / "app" / "gabarits" / "signature_mo.png"
ENCRE = (24, 40, 112)                          # bleu nuit, comme un stylo


def _catmull_rom(points, n=24):
    """Points d'une spline de Catmull-Rom passant par tous les `points`."""
    p = [points[0], *points, points[-1]]
    out = []
    for i in range(1, len(p) - 2):
        p0, p1, p2, p3 = p[i - 1], p[i], p[i + 1], p[i + 2]
        for k in range(n):
            t = k / n
            out.append(tuple(0.5 * (2 * p1[j] + (p2[j] - p0[j]) * t
                                    + (2 * p0[j] - 5 * p1[j] + 4 * p2[j] - p3[j]) * t * t
                                    + (3 * p1[j] - p0[j] - 3 * p2[j] + p3[j]) * t ** 3)
                             for j in (0, 1)))
    out.append(points[-1])
    return out


# Le paraphe (proposition « B » retenue le 28/10/2026) : une haute boucle
# d'attaque qui redescend en jambage, un corps en vagues, une finale montante,
# puis un trait barré horizontal. Points de passage dans un canevas 1000 x 420,
# avant inclinaison.
_TRACE = [
    [(150, 340), (220, 70), (260, 40), (250, 120), (170, 240), (140, 220), (260, 170),
     (380, 160), (330, 260), (290, 220), (420, 170), (470, 240), (520, 170), (570, 240),
     (620, 170), (700, 230), (820, 110)],
    [(160, 200), (500, 190), (860, 150)],                 # trait barré
]
_INCLINAISON = 0.35                    # écriture penchée vers la droite
_PLUME = math.radians(-50)             # angle du bec : pleins et déliés
_SURECH = 3                            # dessiné en 3x puis réduit (lissage)


def _paraphe() -> Image.Image:
    """Tracé générique à l'encre bleue, épaisseur selon la direction comme
    une plume biseautée, attaque et levée effilées."""
    W, H, S = 1000, 420, _SURECH
    img = Image.new("RGB", (W * S, H * S), "white")
    d = ImageDraw.Draw(img)
    for trait in _TRACE:
        pts = _catmull_rom([(x - _INCLINAISON * (y - 210), y) for x, y in trait])
        n = len(pts)
        for i in range(n - 1):
            (x0, y0), (x1, y1) = pts[i], pts[i + 1]
            largeur = 1.0 + 11.0 * abs(math.sin(math.atan2(y1 - y0, x1 - x0) - _PLUME))
            t = i / (n - 1)
            largeur *= (min(1, t * 12, (1 - t) * 8) + 0.15) * S
            r = largeur / 2
            d.line([(x0 * S, y0 * S), (x1 * S, y1 * S)], fill=ENCRE, width=max(1, int(largeur)))
            d.ellipse([x0 * S - r, y0 * S - r, x0 * S + r, y0 * S + r], fill=ENCRE)
    img = img.resize((W, H), Image.LANCZOS)
    x0, y0, x1, y1 = img.convert("L").point(lambda v: 255 if v < 250 else 0).getbbox()
    return img.crop((max(0, x0 - 20), max(0, y0 - 20), min(W, x1 + 20), min(H, y1 + 20)))


def image_signature(texte: str = "") -> Image.Image:
    """`texte` est ignoré depuis le 30/09/2026 (paraphe sans nom) ; gardé
    pour ne pas casser les appelants."""
    if SIGNATURE_SCANNEE.exists():
        with Image.open(SIGNATURE_SCANNEE) as im:
            fond = Image.new("RGB", im.size, "white")
            fond.paste(im.convert("RGBA"), mask=im.convert("RGBA").split()[3])
            return fond
    return _paraphe()


def apposer(source: Path, destination: Path, page_index: int,
            zone_pt: tuple[float, float, float, float], texte: str) -> Path:
    """Copie `source` vers `destination` avec la signature dans `zone_pt`
    (x, y bas-gauche, largeur, hauteur, en points PDF). L'image est ajustée
    dans la zone sans déformation et centrée.
    """
    img = image_signature(texte)
    jpeg = destination.with_suffix(".signature.jpg")
    img.save(jpeg, "JPEG", quality=92)

    x, y, w, h = zone_pt
    ratio = min(w / img.width, h / img.height)
    lw, lh = img.width * ratio, img.height * ratio
    try:
        with config.PDFIUM_LOCK:            # pdfium n'est pas thread-safe
            pdf = pdfium.PdfDocument(str(source))
            try:
                page = pdf[page_index]
                objet = pdfium.PdfImage.new(pdf)
                objet.load_jpeg(str(jpeg))
                objet.set_matrix(pdfium.PdfMatrix().scale(lw, lh)
                                 .translate(x + (w - lw) / 2, y + (h - lh) / 2))
                page.insert_obj(objet)
                page.gen_content()
                pdf.save(str(destination))
            finally:
                pdf.close()
    finally:
        jpeg.unlink(missing_ok=True)
    return destination
