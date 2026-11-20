"""Rendu des plans PDF : plafonné, pour tenir dans 512 Mo.

Le serveur gratuit s'arrêtait faute de mémoire sur les plans de masse A0
(images.rendre_page_pdf). Ce que ces tests protègent :

1. Un grand format ne fabrique plus une image démesurée.
2. Les formats courants gardent exactement la résolution demandée : le
   plafond ne doit dégrader aucun dossier ordinaire.
3. Le dossier assemblé garde au moins les 2 400 px de large que _optimiser
   conserve, même pour un A0 en portrait.
"""
from __future__ import annotations

import pypdfium2 as pdfium
import pytest

from app import assemblage, images

MM = 72 / 25.4          # points PDF par millimètre


def pdf_vierge(chemin, largeur_mm, hauteur_mm):
    doc = pdfium.PdfDocument.new()
    doc.new_page(largeur_mm * MM, hauteur_mm * MM)
    doc.save(str(chemin))
    doc.close()
    return chemin


def rendu(chemin, dpi, cote_max):
    doc = pdfium.PdfDocument(str(chemin))
    try:
        return images.rendre_page_pdf(doc[0], dpi, cote_max)
    finally:
        doc.close()


def test_un_plan_a0_ne_depasse_pas_le_plafond(tmp_path):
    a0 = pdf_vierge(tmp_path / "a0.pdf", 1189, 841)
    image = rendu(a0, 150, 3400)
    # sans plafond : 7 022 x 4 967 px
    assert max(image.size) <= 3400
    assert image.width > image.height           # proportions conservées


def test_un_a4_garde_sa_resolution(tmp_path):
    a4 = pdf_vierge(tmp_path / "a4.pdf", 210, 297)
    image = rendu(a4, 150, 3400)
    assert image.size == pytest.approx((1240, 1754), abs=2)


def test_le_dossier_garde_2400_px_pour_un_a0_en_portrait(tmp_path):
    a0 = pdf_vierge(tmp_path / "dp2.pdf", 841, 1189)
    pages = assemblage._pdf_en_images(a0, tmp_path, "dp2")
    from PIL import Image
    with Image.open(pages[0]) as im:
        assert im.width >= 2400
        assert max(im.size) <= assemblage.COTE_MAX_RENDU_PDF


def test_l_annexe_ne_rend_que_la_page_demandee(tmp_path):
    from app import notices_erp
    doc = pdfium.PdfDocument.new()
    for _ in range(3):
        doc.new_page(1189 * MM, 841 * MM)
    doc.save(str(tmp_path / "plan.pdf"))
    doc.close()

    pages = notices_erp._pages_pdf(tmp_path / "plan.pdf", dpi=200, cote_max_px=3500, max_pages=1)

    assert len(pages) == 1
    assert max(pages[0].size) <= 3500
