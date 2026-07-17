"""DP3 (coupe) — rendu vectoriel propre.

Redessin fidèle aux coupes commerciales Solstyce START PLAINE (dossier
../COUPES) : mât caisson à bandes bleues de signalisation, arbalétrier(s)
effilé(s), bracon(s) diagonal(aux), pannes, modules full black en couverture,
massif béton texturé sous le sol, cotes et échelle graphique. Le modèle
paramétrique (catalogue + saisie) alimente DP3.
"""
from __future__ import annotations

import math
import random

from PIL import Image, ImageDraw

from ..catalogue import libelle_coupe, parametres_effectifs
from . import base
from .base import (
    BLANC, BLEU_BANDE, ENCRE, GRIS, MUTED, NAVY, PANNEAU, Planche,
    cote_horizontale, cote_verticale, police,
)

# teintes acier galvanisé (dégradé pour donner du volume)
ACIER = (176, 183, 191)
ACIER_CLAIR = (208, 214, 220)
ACIER_SOMBRE = (135, 143, 152)
BETON = (198, 198, 194)
BETON_PT = (150, 150, 145)

LARG_MAT = 0.34          # côté du mât caisson (m)
EP_ARBA_RACINE = 0.46    # hauteur de l'arbalétrier au droit du mât (m)
EP_ARBA_TIP = 0.14       # hauteur à l'extrémité (effilé)
EP_PANNEAU = 0.09        # épaisseur modules + rails (m)
H_PANNE = 0.16           # hauteur des pannes entre arbalétrier et modules
PROF_MASSIF = 2.0        # profondeur visible du massif béton (m)
LARG_MASSIF = 0.85       # diamètre du massif (m)


def _lerp(a, b, t):
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b))


def _poly(dr, pts, fill, outline=ENCRE, width=2):
    dr.polygon(pts, fill=fill, outline=outline)
    if width > 1:  # trait de contour plus net
        dr.line(pts + [pts[0]], fill=outline, width=width)


def _monde_vers_px(box, monde_w, monde_h, marge=0.06):
    x0, y0, x1, y1 = box
    dispo_w, dispo_h = (x1 - x0) * (1 - marge), (y1 - y0) * (1 - marge)
    k = min(dispo_w / monde_w, dispo_h / monde_h)
    ox = x0 + ((x1 - x0) - monde_w * k) / 2
    oy = y0 + ((y1 - y0) - monde_h * k) / 2
    return k, ox, oy


def _pente_points(p):
    """(x_haut, h_haut, x_bas, h_bas, x_poteau) en m depuis le bord gauche."""
    prof = p["profondeur_m"]
    if p["poteau"] == "haut":
        return 0.0, p["h_haut_m"], prof, p["h_bas_m"], LARG_MAT / 2
    if p["poteau"] == "bas":
        return 0.0, p["h_haut_m"], prof, p["h_bas_m"], prof - LARG_MAT / 2
    return 0.0, p["h_haut_m"], prof, p["h_bas_m"], prof / 2


def _massif(dr, xp, sol_y, k):
    """Massif béton cylindrique texturé sous le sol, avec symbole de rupture."""
    demi = LARG_MASSIF / 2 * k
    haut = PROF_MASSIF * k
    x0, x1 = xp - demi, xp + demi
    y0, y1 = sol_y + 6, sol_y + haut
    dr.rectangle([x0, y0, x1, y1], fill=BETON, outline=MUTED, width=2)
    rng = random.Random(1234)  # texture reproductible
    for _ in range(int(demi * haut / 55)):
        gx = rng.uniform(x0 + 4, x1 - 4)
        gy = rng.uniform(y0 + 4, y1 - 4)
        r = rng.uniform(1.5, 4)
        dr.ellipse([gx - r, gy - r, gx + r, gy + r], fill=BETON_PT)
    # symbole de rupture (le pieu continue) au 2/3
    yr = y0 + haut * 0.7
    dr.line([(x0, yr - 8), (xp, yr + 8), (x1, yr - 8)], fill=BLANC, width=6)
    dr.line([(x0, yr - 8), (xp, yr + 8), (x1, yr - 8)], fill=MUTED, width=2)


def _mat(dr, X, Y, x_pot, h_attache, sol_y, k):
    """Mât caisson légèrement effilé, à bandes bleues + platine en pied."""
    demi_bas = LARG_MAT / 2 * k
    demi_haut = demi_bas * 0.82
    xp = X(x_pot)
    y_haut = Y(h_attache)
    # corps (dégradé gauche->droite pour le volume)
    corps = [(xp - demi_bas, sol_y), (xp - demi_haut, y_haut),
             (xp + demi_haut, y_haut), (xp + demi_bas, sol_y)]
    dr.polygon(corps, fill=ACIER)
    dr.polygon([(xp - demi_bas, sol_y), (xp - demi_haut, y_haut),
                (xp - demi_haut * 0.2, y_haut), (xp - demi_bas * 0.2, sol_y)],
               fill=ACIER_CLAIR)  # reflet
    dr.line(corps + [corps[0]], fill=ENCRE, width=3)
    # bandes bleues de signalisation (4, dans le tiers bas)
    for i in range(1, 5):
        yb = sol_y - h_attache * k * (0.10 + 0.13 * i)
        t = (sol_y - yb) / max(sol_y - y_haut, 1)
        dg = demi_bas + (demi_haut - demi_bas) * t
        dr.rectangle([xp - dg + 3, yb - 6, xp + dg - 3, yb + 6], fill=BLEU_BANDE)
    # platine en pied
    dr.rectangle([xp - demi_bas - 6, sol_y - 8, xp + demi_bas + 6, sol_y + 4],
                 fill=ACIER_SOMBRE, outline=ENCRE, width=2)
    return xp, demi_haut


def dessiner_coupe(projet: dict) -> Image.Image:
    """DP3 : coupe transversale cotée, fidèle à la coupe Solstyce."""
    p = parametres_effectifs(projet.get("ombriere") or {})
    prof = p["profondeur_m"]
    marge_lat = max(2.4, prof * 0.28)
    monde_w = prof + 2 * marge_lat
    monde_h = p["h_haut_m"] + 1.4 + PROF_MASSIF

    planche = Planche("DP3 · Plan en coupe", projet)
    box = planche.content_box
    k, ox, oy = _monde_vers_px(box, monde_w, monde_h)
    sol_y = round(oy + (p["h_haut_m"] + 1.4) * k)
    dr = planche.draw

    def X(xm): return round(ox + (marge_lat + xm) * k)
    def Y(hm): return round(sol_y - hm * k)

    x_haut, h_haut, x_bas, h_bas, x_pot = _pente_points(p)

    def h_versant(xm):
        if x_bas == x_haut:
            return h_haut
        return h_haut + (xm - x_haut) / (x_bas - x_haut) * (h_bas - h_haut)

    # --- sol + hachures ---
    dr.line([(box[0] + 8, sol_y), (box[2] - 8, sol_y)], fill=ENCRE, width=4)
    for i in range(box[0] + 20, box[2] - 20, 30):
        dr.line([(i, sol_y), (i - 14, sol_y + 14)], fill=GRIS, width=2)

    h_attache = h_versant(x_pot) - EP_ARBA_RACINE
    _massif(dr, X(x_pot), sol_y, k)
    xp, demi_haut = _mat(dr, X, Y, x_pot, h_attache, sol_y, k)

    # --- arbalétrier(s) effilé(s) : polygone racine (épais) -> extrémité (fin) ---
    def arba(x_dep, x_fin):
        yr_dep = h_versant(x_dep)
        yr_fin = h_versant(x_fin)
        pts = [(X(x_dep), Y(yr_dep)), (X(x_fin), Y(yr_fin)),
               (X(x_fin), Y(yr_fin - EP_ARBA_TIP)),
               (X(x_dep), Y(yr_dep - EP_ARBA_RACINE))]
        dr.polygon(pts, fill=ACIER)
        dr.polygon([pts[0], pts[1],
                    (pts[1][0], pts[1][1] - 3), (pts[0][0], pts[0][1] - 3)],
                   fill=ACIER_CLAIR)
        dr.line(pts + [pts[0]], fill=ENCRE, width=3)

    def bracon(x_vers):
        y0 = h_attache * 0.46
        x_acc = x_pot + (x_vers - x_pot) * 0.66
        y_acc = h_versant(x_acc) - EP_ARBA_RACINE * 0.5
        dx, dy = X(x_acc) - xp, Y(y_acc) - Y(y0)
        n = math.hypot(dx, dy) or 1
        e = max(4, int(0.11 * k))
        nx, ny = -dy / n * e, dx / n * e
        pts = [(xp + nx, Y(y0) + ny), (X(x_acc) + nx, Y(y_acc) + ny),
               (X(x_acc) - nx, Y(y_acc) - ny), (xp - nx, Y(y0) - ny)]
        dr.polygon(pts, fill=ACIER_SOMBRE, outline=ENCRE)

    def pannes_et_modules(x0, x1):
        # pannes verticales
        n = max(2, int(abs(x1 - x0) / 1.6))
        for i in range(n + 1):
            xm = x0 + (x1 - x0) * i / n
            yv = h_versant(xm)
            dr.rectangle([X(xm) - 3, Y(yv + H_PANNE), X(xm) + 3, Y(yv)],
                         fill=ACIER_SOMBRE)
        # bande de modules full black
        pts = [(X(x0), Y(h_versant(x0) + H_PANNE)),
               (X(x1), Y(h_versant(x1) + H_PANNE)),
               (X(x1), Y(h_versant(x1) + H_PANNE + EP_PANNEAU)),
               (X(x0), Y(h_versant(x0) + H_PANNE + EP_PANNEAU))]
        dr.polygon(pts, fill=PANNEAU)
        dr.line([pts[0], pts[1]], fill=(70, 120, 160), width=2)  # arête bleutée

    if p["poteau"] == "central":
        arba(x_pot, x_haut + 0.02); arba(x_pot, x_bas - 0.02)
        bracon(x_haut); bracon(x_bas)
        pannes_et_modules(x_haut, x_bas)
    elif p["poteau"] == "haut":
        arba(x_pot, x_bas); bracon(x_bas); pannes_et_modules(0.0, x_bas)
    else:
        arba(x_pot, x_haut); bracon(x_haut); pannes_et_modules(x_haut, prof)

    # --- cotes ---
    cote_verticale(dr, X(x_haut) - int(1.0 * k), sol_y, Y(h_haut),
                   f"{p['h_haut_m']:.2f} m".replace(".", ","), cote_a_gauche=True)
    cote_verticale(dr, X(x_bas) + int(1.0 * k), sol_y, Y(h_bas),
                   f"{p['h_bas_m']:.2f} m".replace(".", ","), cote_a_gauche=False)
    cote_horizontale(dr, X(0), X(prof), sol_y + int(PROF_MASSIF * k) + 30,
                     f"{prof:.2f} m couverts".replace(".", ","))

    # titre (pas de cartouche : posé par le PPTX)
    dr.text((box[0] + 14, box[1] + 10),
            f"Coupe {libelle_coupe(p['famille'])}  ·  pente {p['pente_deg']:g}°  ·  "
            f"acier galvanisé, bandes bleues  ·  modules full black",
            font=police(30, True), fill=NAVY)

    m_par_px = 1 / k
    planche.echelle_graphique(m_par_px)
    planche.source("Coupe paramétrique GV_DP (catalogue Solstyce START PLAINE)")
    return planche.finaliser(cadre=False)
