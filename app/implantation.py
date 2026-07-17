"""Extraction de l'implantation depuis le plan de masse (code couleur GVN).

Convention des plans de masse du BE (confirmée par Florent le 17/07/2026) :
- zones BLEUES  = calepinage des panneaux (emprise des rangées, vue de dessus)
- traits ROUGES = entraxes (trame des poteaux), tracés DANS les rangées
- carrés gris   = fondations (non extraits : gris trop ambigu sur l'ortho)
- étiquettes « HAUT DE RAMPANT » / « BAS DE RAMPANT » = sens de la pente

Particularités réelles (plan Anse) : fond = orthophoto (pas blanc), rangées
inclinées, calepinage quadrillé de noir (fragmente le bleu), étiquettes de
texte sur fond bleu (faux positifs). D'où la méthode :
1. masque bleu -> max-pooling 4x4 (recolle les cellules du calepinage),
2. composantes connexes, filtrées par « contient des entraxes rouges »
   (une rangée a des entraxes ; une étiquette de texte n'en a pas),
3. axes principaux (PCA) -> longueur / largeur / angle réels via l'échelle,
4. schéma épuré (fond blanc, rangées + entraxes + flèche de pente + cotes)
   recadré sur la zone utile : c'est la contrainte envoyée à Gemini.

Tout est déterministe (aucune IA). Si le plan n'est pas un PDF vectoriel au
code couleur attendu, l'analyse renvoie None et l'appelant retombe sur le
plan brut.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pypdfium2 as pdfium
from PIL import Image, ImageDraw

from .lecture_plan import analyser_texte

DPI = 150
POOL = 4                     # max-pooling : soude le calepinage quadrillé
SURFACE_MIN_M2 = 30.0        # une rangée couvre au moins ~30 m²
FRACTION_MIN = 0.0015        # repli sans échelle : 0,15 % de l'image

BLEU_SCHEMA = (37, 99, 235)
ROUGE_SCHEMA = (220, 38, 38)
ENCRE = (0, 36, 85)


# ------------------------------------------------------------------ rendu

def _rendre(chemin_pdf: Path) -> tuple[Image.Image, str]:
    doc = pdfium.PdfDocument(str(chemin_pdf))
    try:
        image = doc[0].render(scale=DPI / 72).to_pil().convert("RGB")
        page_texte = doc[0].get_textpage()
        try:
            texte = page_texte.get_text_bounded() or ""
        finally:
            page_texte.close()
    finally:
        doc.close()
    return image, texte


def _m_par_px(texte: str) -> float | None:
    """1 px rendu -> mètres réels, via l'échelle du cartouche (ex. 1/200)."""
    lecture = analyser_texte(texte)
    echelle = lecture.get("echelle_plan")  # "1/200"
    if not echelle:
        return None
    try:
        denominateur = float(echelle.split("/")[1])
    except (IndexError, ValueError):
        return None
    return (25.4 / DPI) * denominateur / 1000.0


# ------------------------------------------------------------------ masques

def _masques(arr: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    r = arr[..., 0].astype(np.int16)
    g = arr[..., 1].astype(np.int16)
    b = arr[..., 2].astype(np.int16)
    # bleu panneaux, en excluant le cyan très clair des étiquettes de texte
    bleu = (b > 110) & (b > r + 35) & (b > g + 25) & ~((g > 200) & (r > 150))
    rouge = (r > 140) & (r > g + 50) & (r > b + 50)
    return bleu, rouge


def _max_pool(masque: np.ndarray, pas: int = POOL) -> np.ndarray:
    """Max-pooling pas x pas : un pixel actif rend le bloc actif."""
    h, w = masque.shape
    H, W = h - h % pas, w - w % pas
    return masque[:H, :W].reshape(H // pas, pas, W // pas, pas).any(axis=(1, 3))


def _composantes(petit: np.ndarray) -> list[np.ndarray]:
    """Composantes connexes du masque poolé (BFS) ; renvoie des masques de blocs."""
    h, w = petit.shape
    vu = np.zeros_like(petit, dtype=bool)
    zones = []
    for y0 in range(h):
        for x0 in range(w):
            if not petit[y0, x0] or vu[y0, x0]:
                continue
            pile = [(y0, x0)]
            vu[y0, x0] = True
            bloc = np.zeros_like(petit, dtype=bool)
            while pile:
                y, x = pile.pop()
                bloc[y, x] = True
                for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1),
                               (1, 1), (1, -1), (-1, 1), (-1, -1)):
                    ny, nx = y + dy, x + dx
                    if 0 <= ny < h and 0 <= nx < w and petit[ny, nx] and not vu[ny, nx]:
                        vu[ny, nx] = True
                        pile.append((ny, nx))
            zones.append(bloc)
    return zones


def _mesures_pca(ys: np.ndarray, xs: np.ndarray, m_px: float | None) -> dict:
    """Longueur / largeur / angle du nuage de pixels par axes principaux."""
    pts = np.stack([xs.astype(float), ys.astype(float)])
    centre = pts.mean(axis=1, keepdims=True)
    cov = np.cov(pts - centre)
    valeurs, vecteurs = np.linalg.eigh(cov)
    axe = vecteurs[:, np.argmax(valeurs)]              # axe long
    proj_l = (pts - centre).T @ axe
    proj_w = (pts - centre).T @ np.array([-axe[1], axe[0]])
    long_px = float(proj_l.max() - proj_l.min())
    larg_px = float(proj_w.max() - proj_w.min())
    mesures = {
        "centre_px": (float(centre[0, 0]), float(centre[1, 0])),
        "angle_deg": round(float(np.degrees(np.arctan2(axe[1], axe[0]))), 1),
        "coins_px": _coins(centre, axe, long_px, larg_px),
    }
    if m_px:
        mesures["longueur_m"] = round(long_px * m_px, 1)
        mesures["largeur_m"] = round(larg_px * m_px, 1)
    return mesures


def _coins(centre, axe, long_px, larg_px):
    """4 coins du rectangle orienté (pour le tracé du schéma)."""
    perp = np.array([-axe[1], axe[0]])
    c = centre[:, 0]
    demi_l, demi_w = long_px / 2, larg_px / 2
    return [tuple(c + sl * demi_l * axe + sw * demi_w * perp)
            for sl, sw in ((-1, -1), (1, -1), (1, 1), (-1, 1))]


# ------------------------------------------------------------------ pente

def _vers_image(x: float, y: float, largeur_pt: float, hauteur_pt: float,
                rotation: int) -> tuple[float, float]:
    """Point texte (espace page, y vers le haut) -> pixels du rendu.

    Les charbox de pdfium sont dans l'espace NON pivoté de la page ; le rendu
    applique /Rotate (les plans GVN sortent d'ArchiCAD en A3 portrait pivoté
    270°). Transformation vérifiée sur le plan Anse pour 0 et 270.
    """
    if rotation == 90:
        xi, yi = y, x
    elif rotation == 180:
        xi, yi = largeur_pt - x, y
    elif rotation == 270:
        xi, yi = hauteur_pt - y, largeur_pt - x
    else:
        xi, yi = x, hauteur_pt - y
    return xi / 72 * DPI, yi / 72 * DPI


def _positions_texte(chemin_pdf: Path, motif: str) -> list[tuple[float, float]]:
    """Centres (px rendu) des occurrences d'un texte sur la page 1."""
    doc = pdfium.PdfDocument(str(chemin_pdf))
    positions = []
    try:
        page = doc[0]
        largeur_pt, hauteur_pt = page.get_mediabox()[2], page.get_mediabox()[3]
        rotation = page.get_rotation()
        page_texte = page.get_textpage()
        try:
            chercheur = page_texte.search(motif, match_case=False)
            while True:
                trouve = chercheur.get_next()
                if trouve is None:
                    break
                index, longueur = trouve
                boites = [page_texte.get_charbox(i) for i in range(index, index + longueur)]
                cx = (min(b[0] for b in boites) + max(b[2] for b in boites)) / 2
                cy = (min(b[1] for b in boites) + max(b[3] for b in boites)) / 2
                positions.append(_vers_image(cx, cy, largeur_pt, hauteur_pt, rotation))
        finally:
            page_texte.close()
    finally:
        doc.close()
    return positions


# ------------------------------------------------------------------ analyse

def analyser_plan(chemin_pdf: Path) -> dict | None:
    """Extrait rangées (bleu + entraxes rouges), cotes et sens de pente.

    Renvoie None si aucune rangée n'est détectée (plan hors convention).
    """
    if chemin_pdf.suffix.lower() != ".pdf":
        return None
    try:
        image, texte = _rendre(chemin_pdf)
    except Exception:
        return None
    arr = np.asarray(image)
    m_px = _m_par_px(texte)
    aire_min_px = (SURFACE_MIN_M2 / (m_px * m_px)) if m_px \
        else (arr.shape[0] * arr.shape[1] * FRACTION_MIN)

    bleu, rouge = _masques(arr)
    petit_bleu = _max_pool(bleu)
    petit_rouge = _max_pool(rouge)

    rangees = []
    masque_rangees_petit = np.zeros_like(petit_bleu)
    for bloc in _composantes(petit_bleu):
        if bloc.sum() * POOL * POOL < aire_min_px:
            continue
        # une rangée contient des entraxes rouges ; une étiquette de texte non
        if not (bloc & petit_rouge).any():
            continue
        ys, xs = np.where(bloc)
        mesures = _mesures_pca(ys * POOL, xs * POOL, m_px)
        mesures["aire_px"] = int(bloc.sum() * POOL * POOL)
        rangees.append(mesures)
        masque_rangees_petit |= bloc

    if not rangees:
        return None

    # masques plein format restreints aux rangées retenues
    upsample = np.kron(masque_rangees_petit, np.ones((POOL, POOL), dtype=bool))
    upsample = np.pad(upsample, ((0, bleu.shape[0] - upsample.shape[0]),
                                 (0, bleu.shape[1] - upsample.shape[1])))
    bleu_retenu = bleu & upsample
    rouge_retenu = rouge & upsample

    haut = _positions_texte(chemin_pdf, "HAUT DE RAMPANT")
    bas = _positions_texte(chemin_pdf, "BAS DE RAMPANT")
    return {
        "rangees": rangees,
        "m_par_px": m_px,
        "pente_haut_px": haut[0] if haut else None,
        "pente_bas_px": bas[0] if bas else None,
        "taille_px": (arr.shape[1], arr.shape[0]),
        "_bleu": bleu_retenu, "_rouge": rouge_retenu,
    }


def resume(analyse: dict) -> str:
    """Résumé texte de l'implantation, injecté dans le prompt Gemini."""
    n = len(analyse["rangees"])
    bouts = [f"{n} rangée{'s' if n > 1 else ''}"]
    for z in analyse["rangees"]:
        if "longueur_m" in z:
            bouts.append(f"{z['longueur_m']:g} m x {z['largeur_m']:g} m".replace(".", ","))
    if analyse["pente_haut_px"] and analyse["pente_bas_px"]:
        bouts.append("sens de pente fléché sur le schéma")
    return " · ".join(bouts)


# ------------------------------------------------------------------ schéma

def generer_schema(chemin_pdf: Path, sortie: Path, analyse: dict | None = None) -> Path | None:
    """Schéma d'implantation épuré : bleu = panneaux, rouge = trame des
    poteaux, flèche = sens de descente de la pente, cotes réelles. Recadré
    sur la zone utile (l'orthophoto et le cartouche disparaissent)."""
    analyse = analyse or analyser_plan(chemin_pdf)
    if not analyse:
        return None
    bleu, rouge = analyse["_bleu"], analyse["_rouge"]
    H, W = bleu.shape

    # zone utile = rangées + flèche de pente, avec marge
    xs, ys = [], []
    for z in analyse["rangees"]:
        for cx, cy in z["coins_px"]:
            xs.append(cx)
            ys.append(cy)
    for cle in ("pente_haut_px", "pente_bas_px"):
        if analyse[cle]:
            xs.append(analyse[cle][0])
            ys.append(analyse[cle][1])
    marge = round(0.2 * max(max(xs) - min(xs), max(ys) - min(ys))) + 40
    x0, x1 = max(0, int(min(xs)) - marge), min(W, int(max(xs)) + marge)
    y0, y1 = max(0, int(min(ys)) - marge), min(H, int(max(ys)) + marge)

    canevas = np.full((y1 - y0, x1 - x0, 3), 255, dtype=np.uint8)
    canevas[bleu[y0:y1, x0:x1]] = BLEU_SCHEMA
    canevas[rouge[y0:y1, x0:x1]] = ROUGE_SCHEMA
    schema = Image.fromarray(canevas)
    dr = ImageDraw.Draw(schema)

    from .planches.base import police
    for z in analyse["rangees"]:
        coins = [(cx - x0, cy - y0) for cx, cy in z["coins_px"]]
        dr.polygon(coins, outline=ENCRE, width=3)
        if "longueur_m" in z:
            haut_txt = min(cy for _, cy in coins)
            dr.text((min(cx for cx, _ in coins), max(84, haut_txt - 34)),
                    f"{z['longueur_m']:g} m x {z['largeur_m']:g} m".replace(".", ","),
                    font=police(26, True), fill=ENCRE)

    # flèche HAUT -> BAS : direction tirée des étiquettes, tracée AU CENTRE
    # de la rangée principale (plus lisible qu'entre les étiquettes)
    if analyse["pente_haut_px"] and analyse["pente_bas_px"]:
        v = np.array([analyse["pente_bas_px"][0] - analyse["pente_haut_px"][0],
                      analyse["pente_bas_px"][1] - analyse["pente_haut_px"][1]], dtype=float)
        n = float(np.hypot(*v)) or 1.0
        v /= n
        principale = analyse["rangees"][0]
        cx, cy = principale["centre_px"][0] - x0, principale["centre_px"][1] - y0
        demi = 0.9 * min(x1 - x0, y1 - y0) / 4
        dep = np.array([cx, cy]) - demi * v
        fin = np.array([cx, cy]) + demi * v
        dr.line([tuple(dep), tuple(fin)], fill=ENCRE, width=6)
        p = np.array([-v[1], v[0]])
        for signe in (1, -1):
            pointe = fin - 28 * v + signe * 15 * p
            dr.line([tuple(fin), tuple(pointe)], fill=ENCRE, width=6)
        dr.text((14, schema.height - 40), "fleche = sens de descente de la pente (haut -> bas)",
                font=police(24, True), fill=ENCRE)

    titre = "SCHEMA D'IMPLANTATION (extrait du plan de masse)"
    legende = "bleu = panneaux · rouge = trame des poteaux"
    dr.text((14, 8), titre, font=police(24, True), fill=ENCRE)
    dr.text((14, 42), legende, font=police(22, False), fill=ENCRE)

    sortie.parent.mkdir(parents=True, exist_ok=True)
    schema.save(sortie)
    return sortie
