"""Perspective sol <-> image : emprise 3D d'une ombrière depuis UN tracé.

Répond au défaut n°1 constaté sur ~66 générations (19/07/2026, retour
Florent) : l'ombrière est mal placée ou mal dimensionnée parce que Gemini ne
reçoit que le bord avant et doit DEVINER la profondeur en perspective. Ici on
la calcule : caméra sténopé à hauteur d'œil au-dessus d'un sol plan (hypothèse
valable sur un parking), inclinaison déduite de la ligne d'horizon, échelle
calibrée par la longueur réelle du bord avant tracé.

Modèle (repère monde : Y vers le haut, Z vers l'avant ; image : y vers le bas,
point principal au centre) :
- horizon y_h  ->  inclinaison theta = atan((cy - y_h) / f)
- un point sol (X, Z) se projette en
    x = cx + f * X / (h*sin + Z*cos)
    y = cy - f * (Z*sin - h*cos) / (h*sin + Z*cos)
- l'inverse est fermé (voir image_vers_sol). Toutes les coordonnées sol sont
  proportionnelles à la hauteur caméra h : calibrer h suffit à fixer l'échelle.

La focale vient de l'EXIF (équivalent 35 mm) quand il existe, sinon d'un
défaut raisonnable (0,8 x largeur ~ objectif grand-angle de smartphone).
Tous les résultats passent des GARDE-FOUS (horizon au-dessus du tracé,
hauteur caméra plausible) : en cas d'échec on renvoie None et l'appelant
retombe sur l'ancienne approximation.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps

# hauteur d'œil de référence pour la calibration (la vraie hauteur est
# recalculée depuis la longueur réelle du bord avant, cf. calibrer)
HAUTEUR_OEIL_M = 1.6
# hauteur caméra plausible pour une photo de terrain tenue à la main.
# 19/07/2026 : la borne haute était à 30 m (drone), ce qui laissait passer
# des calibrations aberrantes — constaté sur Anse, 6,3 m déduits pour une
# photo prise debout, d'où un volume faux sans aucune alerte.
HAUTEUR_CAM_MIN = 0.8
HAUTEUR_CAM_MAX = 4.0
# au-delà : prise de vue surélevée assumée (drone, étage), acceptée seulement
# si l'utilisateur l'a déclarée explicitement
HAUTEUR_CAM_MAX_DECLAREE = 60.0


def focale_px(chemin_photo: Path, largeur_px: int) -> float:
    """Focale en pixels : EXIF FocalLengthIn35mmFilm si présent, sinon défaut.

    f_px = largeur * f35 / 36 (capteur 35 mm : 36 mm de large). Défaut 0,8 x
    largeur (~28 mm équivalent), typique d'un smartphone en grand-angle.
    """
    try:
        with Image.open(chemin_photo) as im:
            f35 = im.getexif().get(41989)  # FocalLengthIn35mmFilm
        if f35 and 10 <= float(f35) <= 200:
            return largeur_px * float(f35) / 36.0
    except OSError:
        pass
    return 0.8 * largeur_px


@dataclass
class Camera:
    """Caméra calibrée pour UNE photo (sol plan)."""
    W: int
    H: int
    f: float          # focale en pixels
    y_h: float        # ordonnée image de l'horizon (px)
    h_cam: float      # hauteur caméra au-dessus du sol (m)

    @property
    def theta(self) -> float:
        return math.atan2(self.cy - self.y_h, self.f)

    @property
    def cx(self) -> float:
        return self.W / 2.0

    @property
    def cy(self) -> float:
        return self.H / 2.0

    # ---- projections ----

    def image_vers_sol(self, x: float, y: float) -> tuple[float, float] | None:
        """Pixel -> point sol (X latéral, Z profondeur) en mètres.

        None si le pixel est sur ou au-dessus de l'horizon (pas de sol là).
        """
        s, c = math.sin(self.theta), math.cos(self.theta)
        a = (y - self.cy) / self.f
        den = a * c + s
        if den <= 1e-6:
            return None
        Z = self.h_cam * (c - a * s) / den
        fwd = self.h_cam * s + Z * c
        X = (x - self.cx) / self.f * fwd
        return (X, Z)

    def sol_vers_image(self, X: float, Z: float, hauteur: float = 0.0
                       ) -> tuple[float, float] | None:
        """Point (X, Z) du sol, élevé de `hauteur` m -> pixel image."""
        s, c = math.sin(self.theta), math.cos(self.theta)
        y_monde = hauteur - self.h_cam
        fwd = -y_monde * s + Z * c
        if fwd <= 1e-6:
            return None      # derrière la caméra
        u_ax = y_monde * c + Z * s
        return (self.cx + self.f * X / fwd, self.cy - self.f * u_ax / fwd)


def hauteur_pour_horizon(W: int, H: int, y_h_px: float, f: float,
                         bord_a: tuple[float, float], bord_b: tuple[float, float],
                         longueur_m: float) -> float | None:
    """Hauteur caméra impliquée par un horizon donné, SANS garde-fou.

    Sert au diagnostic (« ta photo aurait été prise à 6,3 m ») et à la
    recherche d'horizon par dichotomie. None si la géométrie est impossible.
    """
    if not longueur_m or longueur_m <= 0 or f <= 0:
        return None
    if y_h_px >= min(bord_a[1], bord_b[1]) - 4:
        return None                     # l'horizon doit être AU-DESSUS du tracé
    cam = Camera(W=W, H=H, f=f, y_h=y_h_px, h_cam=HAUTEUR_OEIL_M)
    pa = cam.image_vers_sol(*bord_a)
    pb = cam.image_vers_sol(*bord_b)
    if not pa or not pb:
        return None
    d_ref = math.hypot(pb[0] - pa[0], pb[1] - pa[1])
    if d_ref <= 1e-6:
        return None
    return HAUTEUR_OEIL_M * longueur_m / d_ref


def horizon_pour_hauteur(W: int, H: int, f: float,
                         bord_a: tuple[float, float], bord_b: tuple[float, float],
                         longueur_m: float, h_cible: float) -> float | None:
    """Ordonnée d'horizon (px) telle que la photo soit prise à `h_cible`.

    Réglage INVERSÉ (19/07/2026) : l'horizon est un paramètre invisible que
    l'utilisateur ne sait pas estimer, alors qu'il sait toujours s'il a
    photographié debout, accroupi ou depuis un drone. On cherche donc
    l'horizon par dichotomie ; la hauteur déduite décroît quand l'horizon
    descend vers le tracé (fonction monotone), ce qui rend la recherche sûre.
    None si aucune position d'horizon ne donne cette hauteur : c'est le signal
    que la longueur déclarée est incompatible avec la prise de vue.
    """
    if h_cible <= 0:
        return None
    bas = min(bord_a[1], bord_b[1]) - 5      # horizon au plus près du tracé
    haut = -2.0 * H                          # très au-dessus du cadre
    h_bas = hauteur_pour_horizon(W, H, bas, f, bord_a, bord_b, longueur_m)
    h_haut = hauteur_pour_horizon(W, H, haut, f, bord_a, bord_b, longueur_m)
    if h_bas is None or h_haut is None:
        return None
    if not (min(h_bas, h_haut) <= h_cible <= max(h_bas, h_haut)):
        return None                          # cible hors de portée
    for _ in range(60):
        mid = (bas + haut) / 2
        h_mid = hauteur_pour_horizon(W, H, mid, f, bord_a, bord_b, longueur_m)
        if h_mid is None:
            return None
        if (h_mid > h_cible) == (h_haut > h_cible):
            haut, h_haut = mid, h_mid
        else:
            bas, h_bas = mid, h_mid
    return (bas + haut) / 2


def calibrer(W: int, H: int, y_h_px: float, f: float,
             bord_a: tuple[float, float], bord_b: tuple[float, float],
             longueur_m: float, h_max: float = HAUTEUR_CAM_MAX) -> Camera | None:
    """Calibre la hauteur caméra pour que le bord avant tracé mesure
    `longueur_m` au sol. Les coordonnées sol étant proportionnelles à h_cam,
    une seule règle de trois suffit. None si la hauteur obtenue sort des
    bornes plausibles (tracé ou horizon incohérents).
    """
    h_cam = hauteur_pour_horizon(W, H, y_h_px, f, bord_a, bord_b, longueur_m)
    if h_cam is None or not (HAUTEUR_CAM_MIN <= h_cam <= h_max):
        return None
    return Camera(W=W, H=H, f=f, y_h=y_h_px, h_cam=h_cam)


def volume_ombriere(cam: Camera,
                    bord_a: tuple[float, float], bord_b: tuple[float, float],
                    profondeur_m: float, h_avant: float, h_fond: float
                    ) -> dict | None:
    """Emprise au sol + toiture d'une ombrière, en pixels image.

    Le bord avant tracé est projeté au sol ; la profondeur part
    perpendiculairement au bord, du côté qui S'ÉLOIGNE de la caméra (Z
    croissant) ; les huit coins sont reprojetés dans l'image, ce qui donne la
    VRAIE convergence perspective (le fond est plus court que l'avant).

    Renvoie {"sol": [av_g, av_d, fond_d, fond_g], "toit": [...]} (px) ou None.
    """
    pa = cam.image_vers_sol(*bord_a)
    pb = cam.image_vers_sol(*bord_b)
    if not pa or not pb or not profondeur_m or profondeur_m <= 0:
        return None
    vx, vz = pb[0] - pa[0], pb[1] - pa[1]
    n = math.hypot(vx, vz)
    if n <= 1e-6:
        return None
    # perpendiculaire au bord sur le PLAN DU SOL, orientée vers le fond
    # (celle des deux qui augmente la distance à la caméra, origine sol (0,0))
    px_, pz_ = -vz / n, vx / n
    milieu = ((pa[0] + pb[0]) / 2, (pa[1] + pb[1]) / 2)
    d_plus = math.hypot(milieu[0] + px_, milieu[1] + pz_)
    d_moins = math.hypot(milieu[0] - px_, milieu[1] - pz_)
    if d_plus < d_moins:
        px_, pz_ = -px_, -pz_

    sol_m = [pa, pb,
             (pb[0] + px_ * profondeur_m, pb[1] + pz_ * profondeur_m),
             (pa[0] + px_ * profondeur_m, pa[1] + pz_ * profondeur_m)]
    hauteurs = [h_avant, h_avant, h_fond, h_fond]
    sol, toit = [], []
    for (X, Z), ht in zip(sol_m, hauteurs):
        p_sol = cam.sol_vers_image(X, Z)
        p_toit = cam.sol_vers_image(X, Z, hauteur=ht)
        if not p_sol or not p_toit:
            return None
        sol.append(p_sol)
        toit.append(p_toit)
    return {"sol": sol, "toit": toit}


# ------------------------------------------------------------------ fuyantes

def proposer_horizon(chemin_photo: Path) -> float | None:
    """Propose une ordonnée d'horizon (0-1) depuis les fuyantes du marquage.

    Détection volontairement LÉGÈRE (numpy seul, pas d'OpenCV) : gradients de
    Sobel sur l'image réduite, votes de droites quantifiées (angle, offset)
    pour les orientations « fuyantes » (15-75° de l'horizontale), horizon =
    médiane des ordonnées d'intersection des meilleures paires. C'est une
    PROPOSITION : None dès que les votes sont maigres ou incohérents, et
    l'utilisateur garde la poignée d'ajustement.
    """
    import numpy as np

    try:
        with Image.open(chemin_photo) as brut:
            im = ImageOps.exif_transpose(brut).convert("L")
    except OSError:
        return None
    W0, H0 = im.size
    ech = 480 / max(W0, H0)
    im = im.resize((max(1, round(W0 * ech)), max(1, round(H0 * ech))))
    g = np.asarray(im, dtype=np.float32)
    H, W = g.shape

    gx = np.zeros_like(g)
    gy = np.zeros_like(g)
    gx[:, 1:-1] = g[:, 2:] - g[:, :-2]
    gy[1:-1, :] = g[2:, :] - g[:-2, :]
    mag = np.hypot(gx, gy)
    seuil = np.percentile(mag, 97)
    ys, xs = np.nonzero(mag > max(seuil, 20))
    if len(xs) < 200:
        return None
    # orientation de la LIGNE (perpendiculaire au gradient)
    ang = (np.degrees(np.arctan2(gy[ys, xs], gx[ys, xs])) + 90.0) % 180.0
    fuyante = ((ang > 15) & (ang < 75)) | ((ang > 105) & (ang < 165))
    xs, ys, ang = xs[fuyante], ys[fuyante], ang[fuyante]
    if len(xs) < 120:
        return None

    # accumulateur (angle 4°, rho 6 px) façon Hough épuré
    ang_q = (ang // 4).astype(np.int32)
    rad = np.radians(ang_q * 4 + 2)
    rho = xs * np.sin(rad) - ys * np.cos(rad)   # droite d'orientation `rad`
    rho_q = np.round(rho / 6).astype(np.int32)
    votes: dict[tuple[int, int], int] = {}
    for a, r in zip(ang_q, rho_q):
        votes[(int(a), int(r))] = votes.get((int(a), int(r)), 0) + 1
    lignes = sorted(votes.items(), key=lambda kv: -kv[1])[:8]
    lignes = [(a * 4 + 2, r * 6.0) for (a, r), v in lignes if v >= 25]
    if len(lignes) < 2:
        return None

    # intersections des paires d'orientations différentes
    inters = []
    for i in range(len(lignes)):
        for j in range(i + 1, len(lignes)):
            a1, r1 = lignes[i]
            a2, r2 = lignes[j]
            if abs(a1 - a2) < 12 or abs(abs(a1 - a2) - 180) < 12:
                continue
            t1, t2 = math.radians(a1), math.radians(a2)
            # x*sin(t) - y*cos(t) = r -> Cramer : y = (s1*r2 - s2*r1) / sin(t2-t1)
            det = math.sin(t2 - t1)
            if abs(det) < 1e-6:
                continue
            y = (math.sin(t1) * r2 - math.sin(t2) * r1) / det
            inters.append(y)
    if len(inters) < 2:
        return None
    inters.sort()
    y_med = inters[len(inters) // 2]
    y01 = y_med / H
    # un horizon plausible sur une photo de parking : ni tout en haut, ni bas
    if not (0.12 <= y01 <= 0.72):
        return None
    # cohérence : la moitié des intersections doit être proche de la médiane
    proches = sum(1 for y in inters if abs(y / H - y01) < 0.08)
    if proches < max(2, len(inters) // 2):
        return None
    return round(y01, 4)
