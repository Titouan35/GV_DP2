"""DP3 (coupe) et DP4 (façades / toiture) — rendu vectoriel propre.

Redessin fidèle aux coupes commerciales Solstyce START PLAINE (dossier
../COUPES) : mât caisson à bandes bleues de signalisation, arbalétrier(s)
effilé(s), bracon(s) diagonal(aux), pannes, modules full black en couverture,
massif béton texturé sous le sol, cotes et échelle graphique. Un seul modèle
paramétrique (catalogue + saisie) alimente DP3 et DP4.
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


def dessiner_facades(projet: dict) -> Image.Image:
    """DP4 : élévation longitudinale + plan de toiture, cotés."""
    p = parametres_effectifs(projet.get("ombriere") or {})
    longueur, prof = p["longueur_m"], p["profondeur_m"]
    entraxe, nb_trav = p["entraxe_m"], p["nb_travees"]
    h_bas, h_haut = p["h_bas_m"], p["h_haut_m"]

    planche = Planche("DP4 · Plan des façades et toitures", projet)
    x0, y0, x1, y1 = planche.content_box
    dr = planche.draw
    dr.text((x0 + 14, y0 + 8), "DP4 · Façades et plan de toiture",
            font=police(30, True), fill=NAVY)

    milieu = y0 + 70
    h_zone = (y1 - milieu - 70) // 2
    box_elev = (x0, milieu, x1, milieu + h_zone)
    box_toit = (x0, milieu + h_zone + 70, x1, y1)

    # ---- élévation longitudinale ----
    monde_w = longueur + 3.0
    monde_h = h_haut + 1.2
    k, ox, oy = _monde_vers_px(box_elev, monde_w, monde_h)
    sol_y = round(oy + monde_h * k)

    def Xe(xm): return round(ox + (1.5 + xm) * k)
    def Ye(hm): return round(sol_y - hm * k)

    dr.text((Xe(longueur / 2), box_elev[1] - 2),
            "Élévation longitudinale (vue de la façade)", font=police(24, True),
            fill=MUTED, anchor="ma")
    dr.line([(x0 + 8, sol_y), (x1 - 8, sol_y)], fill=ENCRE, width=4)
    for i in range(x0 + 20, x1 - 20, 30):
        dr.line([(i, sol_y), (i - 12, sol_y + 12)], fill=GRIS, width=2)

    # bandeau de toiture (modules) vu de profil, légèrement incliné
    dr.polygon([(Xe(0), Ye(h_haut)), (Xe(longueur), Ye(h_bas)),
                (Xe(longueur), Ye(h_bas - EP_PANNEAU * 1.6)),
                (Xe(0), Ye(h_haut - EP_PANNEAU * 1.6))], fill=PANNEAU)
    dr.line([(Xe(0), Ye(h_haut)), (Xe(longueur), Ye(h_bas))],
            fill=(70, 120, 160), width=2)
    # arbalétrier apparent sous les modules
    dr.line([(Xe(0), Ye(h_haut - EP_PANNEAU * 1.6)), (Xe(longueur), Ye(h_bas - EP_PANNEAU * 1.6))],
            fill=ACIER_SOMBRE, width=max(4, int(0.25 * k)))
    # poteaux + bandes bleues
    for i in range(nb_trav + 1):
        xm = min(i * entraxe, longueur)
        hp = h_haut + (h_bas - h_haut) * (xm / longueur if longueur else 0)
        xpi = Xe(xm)
        dg = LARG_MAT / 2 * k
        dr.rectangle([xpi - dg, Ye(hp - 0.35), xpi + dg, sol_y], fill=ACIER, outline=ENCRE, width=2)
        for b in range(1, 4):
            yb = sol_y - (hp - 0.35) * k * (0.12 + 0.14 * b)
            dr.rectangle([xpi - dg + 3, yb - 5, xpi + dg - 3, yb + 5], fill=BLEU_BANDE)
        dr.rectangle([xpi - dg - 5, sol_y - 6, xpi + dg + 5, sol_y + 3], fill=ACIER_SOMBRE)

    cote_verticale(dr, Xe(0) - int(0.9 * k), sol_y, Ye(h_haut),
                   f"{h_haut:.2f} m".replace(".", ","), cote_a_gauche=True)
    cote_horizontale(dr, Xe(0), Xe(longueur), sol_y + int(0.55 * k),
                     f"{longueur:g} m  ·  {nb_trav} travées de {entraxe:g} m")

    # ---- plan de toiture ----
    monde_w2 = longueur + 3.0
    monde_h2 = prof + 2.2
    k2, ox2, oy2 = _monde_vers_px(box_toit, monde_w2, monde_h2)

    def Xt(xm): return round(ox2 + (1.5 + xm) * k2)
    def Yt(ym): return round(oy2 + (1.1 + ym) * k2)

    dr.text((Xt(longueur / 2), box_toit[1] - 2), "Plan de toiture",
            font=police(24, True), fill=MUTED, anchor="ma")
    dr.rectangle([Xt(0), Yt(0), Xt(longueur), Yt(prof)], fill=PANNEAU, outline=ENCRE, width=3)
    xm = 1.134
    while xm < longueur:  # calepinage modules
        dr.line([(Xt(xm), Yt(0)), (Xt(xm), Yt(prof))], fill=(58, 64, 74), width=1)
        xm += 1.134
    ym = 1.0
    while ym < prof:
        dr.line([(Xt(0), Yt(ym)), (Xt(longueur), Yt(ym))], fill=(58, 64, 74), width=1)
        ym += 1.0
    # flèche de pente
    sens = 1 if p["poteau"] == "bas" else -1
    yA, yB = (Yt(prof * 0.72), Yt(prof * 0.28)) if sens < 0 else (Yt(prof * 0.28), Yt(prof * 0.72))
    xc = Xt(longueur / 2)
    dr.line([(xc, yA), (xc, yB)], fill=BLANC, width=5)
    dr.polygon([(xc, yB), (xc - 13, yB + (22 if yB > yA else -22)),
                (xc + 13, yB + (22 if yB > yA else -22))], fill=BLANC)
    dr.text((xc + 22, (yA + yB) // 2), f"pente {p['pente_deg']:g}°",
            font=police(24, True), fill=BLANC, anchor="lm")
    cote_horizontale(dr, Xt(0), Xt(longueur), Yt(prof) + int(0.45 * k2), f"{longueur:g} m")
    cote_verticale(dr, Xt(0) - int(0.7 * k2), Yt(0), Yt(prof), f"{prof:g} m")

    planche.echelle_graphique(1 / k2, y=box_toit[3] - 12)
    planche.source("Façades et toiture paramétriques GV_DP")
    return planche.finaliser(cadre=False)
