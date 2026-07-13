"""DP3 (coupe) et DP4 (façades / toiture) générées depuis le modèle paramétrique.

Un seul modèle (catalogue START PLAINE + paramètres saisis) alimente les
deux pièces : géométrie fidèle aux coupes Solstyce (poteau caisson à bandes
bleues, arbalétrier effilé, bracon, modules full black), cf. app/catalogue.py.
"""
from __future__ import annotations

import math

from PIL import Image, ImageDraw

from ..catalogue import parametres_effectifs
from . import base
from .base import (
    BLANC, BLEU_BANDE, ENCRE, GRIS, MUTED, NAVY, PANNEAU, Planche,
    cote_horizontale, cote_verticale, police,
)

EP_ARBA_POTEAU = 0.42   # hauteur de l'arbalétrier côté poteau (m, effilé)
EP_ARBA_TIP = 0.12      # hauteur à l'extrémité
EP_PANNEAU = 0.08       # épaisseur visuelle modules + rails (m)
LARG_POTEAU = 0.32      # caisson (m)
PROF_MASSIF = 1.1       # massif béton sous le sol (m)


def _monde_vers_px(box, monde_w, monde_h):
    """Facteur px/m et origine pour caser le dessin dans la zone utile."""
    x0, y0, x1, y1 = box
    k = min((x1 - x0) / monde_w, (y1 - y0) / monde_h)
    ox = x0 + ((x1 - x0) - monde_w * k) / 2
    oy = y0 + ((y1 - y0) - monde_h * k) / 2
    return k, ox, oy


def _pente_points(p):
    """Extrémités du versant (x en m depuis le bord gauche couvert, y = hauteur).

    Renvoie (x_haut, h_haut, x_bas, h_bas, x_poteau)."""
    prof = p["profondeur_m"]
    if p["poteau"] == "haut":      # poteau côté haut, pente descend vers la droite
        return 0.0, p["h_haut_m"], prof, p["h_bas_m"], LARG_POTEAU / 2
    if p["poteau"] == "bas":       # poteau côté bas, point haut à gauche
        return 0.0, p["h_haut_m"], prof, p["h_bas_m"], prof - LARG_POTEAU / 2
    return 0.0, p["h_haut_m"], prof, p["h_bas_m"], prof / 2  # central


def dessiner_coupe(projet: dict) -> Image.Image:
    """DP3 : coupe transversale cotée, à l'échelle (échelle graphique)."""
    p = parametres_effectifs(projet.get("ombriere") or {})
    prof = p["profondeur_m"]

    marge_g, marge_d = 2.2, 2.2          # m de sol de part et d'autre
    monde_w = prof + marge_g + marge_d
    monde_h = p["h_haut_m"] + 1.1 + PROF_MASSIF   # ciel + massif

    planche = Planche("DP3 · Plan en coupe", projet)
    box = planche.content_box
    k, ox, oy = _monde_vers_px(box, monde_w, monde_h)
    sol_y = oy + (p["h_haut_m"] + 1.1) * k        # y du sol en px

    def X(xm): return round(ox + (marge_g + xm) * k)
    def Y(hm): return round(sol_y - hm * k)       # hauteur au-dessus du sol

    dr = planche.draw
    x_haut, h_haut, x_bas, h_bas, x_pot = _pente_points(p)

    # sol + hachures
    dr.line([(box[0] + 10, sol_y), (box[2] - 10, sol_y)], fill=ENCRE, width=3)
    for i in range(box[0] + 20, box[2] - 20, 26):
        dr.line([(i, sol_y), (i - 12, sol_y + 12)], fill=GRIS, width=2)

    # massif béton (pointillés sous le sol)
    xp = X(x_pot)
    demi_massif = 0.35 * k
    y_massif = sol_y + PROF_MASSIF * k
    for seg in range(0, int(PROF_MASSIF * k), 14):
        dr.line([(xp - demi_massif, sol_y + seg), (xp - demi_massif, min(sol_y + seg + 7, y_massif))], fill=MUTED, width=2)
        dr.line([(xp + demi_massif, sol_y + seg), (xp + demi_massif, min(sol_y + seg + 7, y_massif))], fill=MUTED, width=2)
    for seg in range(int(-demi_massif), int(demi_massif), 14):
        dr.line([(xp + seg, y_massif), (xp + min(seg + 7, demi_massif), y_massif)], fill=MUTED, width=2)
    dr.text((xp, y_massif + 14), "massif béton selon étude géotechnique",
            font=police(20), fill=MUTED, anchor="ma")

    # versant : ligne du haut de l'arbalétrier
    def h_versant(xm):
        if x_bas == x_haut:
            return h_haut
        t = (xm - x_haut) / (x_bas - x_haut)
        return h_haut + t * (h_bas - h_haut)

    # poteau caisson + bandes bleues (jusque sous l'arbalétrier)
    h_attache = h_versant(x_pot) - EP_ARBA_POTEAU
    demi_pot = LARG_POTEAU / 2 * k
    dr.rectangle([xp - demi_pot, Y(h_attache), xp + demi_pot, sol_y], fill=(186, 192, 198), outline=ENCRE, width=2)
    for i in range(1, 5):
        yb = sol_y - h_attache * k * i / 5
        dr.rectangle([xp - demi_pot + 2, yb - 5, xp + demi_pot - 2, yb + 5], fill=BLEU_BANDE)

    # arbalétrier effilé (polygone sous le versant)
    def arba(x_dep, x_fin):
        pts = [
            (X(x_dep), Y(h_versant(x_dep))),
            (X(x_fin), Y(h_versant(x_fin))),
            (X(x_fin), Y(h_versant(x_fin) - EP_ARBA_TIP)),
            (X(x_dep), Y(h_versant(x_dep) - EP_ARBA_POTEAU)),
        ]
        dr.polygon(pts, fill=(200, 205, 210), outline=ENCRE)

    # bracon (diagonale poteau → arbalétrier)
    def bracon(vers_x):
        x_acc = x_pot + (vers_x - x_pot) * 0.72
        dr.line([(xp, Y(h_attache * 0.55)),
                 (X(x_acc), Y(h_versant(x_acc) - EP_ARBA_POTEAU * 0.35))],
                fill=(170, 176, 183), width=max(3, int(0.09 * k)))

    if p["poteau"] == "central":
        arba(x_pot, x_haut + 0.02)
        arba(x_pot, x_bas - 0.02)
        bracon(x_haut)
        bracon(x_bas)
    elif p["poteau"] == "haut":
        arba(x_pot, x_bas)
        bracon(x_bas)
    else:
        arba(x_pot, x_haut)
        bracon(x_haut)

    # modules full black sur le versant
    dr.line([(X(x_haut), Y(h_haut + EP_PANNEAU)), (X(x_bas), Y(h_bas + EP_PANNEAU))],
            fill=PANNEAU, width=max(4, int(EP_PANNEAU * k)))

    # cotes : hauteurs point haut / point bas, profondeur, pente
    cote_verticale(dr, X(x_haut) - int(0.9 * k), sol_y, Y(h_haut),
                   f"{p['h_haut_m']:.2f} m".replace(".", ","), cote_a_gauche=True)
    cote_verticale(dr, X(x_bas) + int(0.9 * k), sol_y, Y(h_bas),
                   f"{p['h_bas_m']:.2f} m".replace(".", ","), cote_a_gauche=False)
    cote_horizontale(dr, X(0), X(prof), sol_y + int(0.55 * k),
                     f"{prof:.2f} m couverts".replace(".", ","))
    dr.text((X(prof / 2), Y(max(p['h_haut_m'], p['h_bas_m']) + 0.75)),
            f"{p['famille']} · pente {p['pente_deg']:g}° · structure acier galvanisé, bandes bleues · modules full black",
            font=police(26, True), fill=NAVY, anchor="ma")

    m_par_px = 1 / k
    planche.echelle_txt = f"Échelle {base.echelle_nominale(m_par_px)} (A3) — voir échelle graphique"
    planche.echelle_graphique(m_par_px)
    planche.source("Coupe générée depuis le modèle paramétrique GV_DP (catalogue Solstyce START)")
    return planche.finaliser()


def dessiner_facades(projet: dict) -> Image.Image:
    """DP4 : élévation longitudinale + plan de toiture, cotés, à l'échelle."""
    p = parametres_effectifs(projet.get("ombriere") or {})
    longueur, prof = p["longueur_m"], p["profondeur_m"]
    entraxe, nb_trav = p["entraxe_m"], p["nb_travees"]

    planche = Planche("DP4 · Plan des façades et toitures", projet)
    x0, y0, x1, y1 = planche.content_box
    h_zone = (y1 - y0 - 60) // 2
    box_elev = (x0, y0, x1, y0 + h_zone)
    box_toit = (x0, y0 + h_zone + 60, x1, y1)
    dr = planche.draw

    # ---- élévation longitudinale (vue du côté bas de la pente) ----
    monde_w = longueur + 4.0
    monde_h = p["h_haut_m"] + 1.3
    k, ox, oy = _monde_vers_px(box_elev, monde_w, monde_h)
    sol_y = oy + monde_h * k

    def Xe(xm): return round(ox + (2.0 + xm) * k)
    def Ye(hm): return round(sol_y - hm * k)

    dr.text((Xe(longueur / 2), oy - 4),
            f"Élévation longitudinale (vue du point bas) — hauteur hors tout {p['h_haut_m']:.2f} m au point haut".replace(".", ","),
            font=police(24, True), fill=NAVY, anchor="ma")
    dr.line([(x0 + 10, sol_y), (x1 - 10, sol_y)], fill=ENCRE, width=3)

    h_bas, h_haut = p["h_bas_m"], p["h_haut_m"]
    # bande de toiture vue de profil : bord bas devant, versant qui monte derrière
    dr.rectangle([Xe(0), Ye(h_haut), Xe(longueur), Ye(h_haut - EP_PANNEAU)],
                 fill=(70, 76, 86))   # arête haute des modules, en arrière-plan
    dr.rectangle([Xe(0), Ye(h_bas), Xe(longueur), Ye(h_bas - 0.30)],
                 fill=(198, 203, 209), outline=ENCRE, width=2)  # longrine/chéneau bas
    # poteaux
    nb_poteaux = nb_trav + 1
    for i in range(nb_poteaux):
        xpi = Xe(min(i * entraxe, longueur))
        dr.rectangle([xpi - LARG_POTEAU / 2 * k, Ye(h_bas - 0.30), xpi + LARG_POTEAU / 2 * k, sol_y],
                     fill=(186, 192, 198), outline=ENCRE, width=2)
        for b in range(1, 4):
            yb = sol_y - (h_bas - 0.30) * k * b / 4
            dr.rectangle([xpi - LARG_POTEAU / 2 * k + 2, yb - 4, xpi + LARG_POTEAU / 2 * k - 2, yb + 4],
                         fill=BLEU_BANDE)

    cote_verticale(dr, Xe(0) - int(0.8 * k), sol_y, Ye(h_bas), f"{h_bas:.2f} m".replace(".", ","))
    cote_verticale(dr, Xe(longueur) + int(0.8 * k), sol_y, Ye(h_haut),
                   f"{h_haut:.2f} m".replace(".", ","), cote_a_gauche=True)
    cote_horizontale(dr, Xe(0), Xe(longueur), sol_y + int(0.45 * k),
                     f"{longueur:g} m ({nb_trav} travées de {entraxe:g} m)")

    # ---- plan de toiture ----
    monde_w2 = longueur + 4.0
    monde_h2 = prof + 2.4
    k2, ox2, oy2 = _monde_vers_px(box_toit, monde_w2, monde_h2)

    def Xt(xm): return round(ox2 + (2.0 + xm) * k2)
    def Yt(ym): return round(oy2 + (1.2 + ym) * k2)

    dr.text((Xt(longueur / 2), oy2 - 2), "Plan de toiture", font=police(24, True), fill=NAVY, anchor="ma")
    dr.rectangle([Xt(0), Yt(0), Xt(longueur), Yt(prof)], fill=(24, 28, 36), outline=ENCRE, width=3)
    # calepinage : modules ~1,76 m (longueur) × 1,134 m (pente)
    xm = 1.76
    while xm < longueur:
        dr.line([(Xt(xm), Yt(0)), (Xt(xm), Yt(prof))], fill=(60, 66, 76), width=1)
        xm += 1.76
    ym = 1.134
    while ym < prof:
        dr.line([(Xt(0), Yt(ym)), (Xt(longueur), Yt(ym))], fill=(60, 66, 76), width=1)
        ym += 1.134

    # sens de la pente
    if p["poteau"] == "bas":
        fleche = (Xt(longueur / 2), Yt(prof * 0.75), Xt(longueur / 2), Yt(prof * 0.25))
    else:
        fleche = (Xt(longueur / 2), Yt(prof * 0.25), Xt(longueur / 2), Yt(prof * 0.75))
    dr.line(fleche[:2] + fleche[2:], fill=BLANC, width=4)
    fx, fy = fleche[2], fleche[3]
    sens = 1 if fleche[3] > fleche[1] else -1
    dr.polygon([(fx, fy), (fx - 12, fy - sens * 20), (fx + 12, fy - sens * 20)], fill=BLANC)
    dr.text((Xt(longueur / 2) + 20, (fleche[1] + fleche[3]) // 2),
            f"pente {p['pente_deg']:g}°", font=police(24, True), fill=BLANC, anchor="lm")

    cote_horizontale(dr, Xt(0), Xt(longueur), Yt(prof) + int(0.4 * k2), f"{longueur:g} m")
    cote_verticale(dr, Xt(0) - int(0.7 * k2), Yt(0), Yt(prof), f"{prof:g} m")

    m_par_px = 1 / k2
    planche.echelle_txt = f"Échelle {base.echelle_nominale(m_par_px)} (A3) — voir échelle graphique"
    planche.echelle_graphique(m_par_px)
    planche.source("Façades et toiture générées depuis le modèle paramétrique GV_DP")
    return planche.finaliser()
