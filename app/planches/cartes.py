"""DP1 : plans de situation, cadastral et vue aérienne à l'échelle.

Fonds via WMS GetMap Géoplateforme (couches libres), bbox calculée en
Lambert-93 (EPSG:2154, jamais de mesure en degrés) pour une échelle
exacte à l'impression A3 ; overlays parcelles + repère + nord + échelle
graphique dessinés par-dessus.
"""
from __future__ import annotations

import io

import httpx
from PIL import Image, ImageDraw
from pyproj import Transformer

from .. import config
from ..geo.client import GeoApiError
from . import base
from .base import NAVY, VERT, VIOLET, Planche, police

WMS_URL = "https://data.geopf.fr/wms-r"
_WGS84_VERS_L93 = Transformer.from_crs(4326, 2154, always_xy=True)

# échelles candidates (impression A3 paysage : largeur terrain = 0,42 m × X)
ECHELLES_PROCHES = [500, 1000, 2000, 2500, 5000]


def _centre_l93(projet: dict) -> tuple[float, float]:
    """Centre de la planche : centroïde des parcelles, sinon point géocodé."""
    loc = projet.get("localisation") or {}
    xs, ys = [], []
    for parc in loc.get("parcelles") or []:
        geom = parc.get("geometry")
        for x, y in _coords_geom(geom):
            xs.append(x); ys.append(y)
    if xs:
        return (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    if loc.get("lon") is None:
        raise ValueError("Localisation absente : géocodez l'adresse d'abord.")
    return _WGS84_VERS_L93.transform(loc["lon"], loc["lat"])


def _coords_geom(geom: dict | None):
    """Itère les sommets (L93) d'un Polygon/MultiPolygon GeoJSON WGS84."""
    if not geom:
        return
    coords = geom.get("coordinates") or []
    polys = coords if geom.get("type") == "MultiPolygon" else [coords]
    for poly in polys:
        for ring in poly:
            for lon, lat in ring:
                yield _WGS84_VERS_L93.transform(lon, lat)


def _anneaux_geom(geom: dict | None):
    """Itère les anneaux extérieurs (listes de points L93)."""
    if not geom:
        return
    coords = geom.get("coordinates") or []
    polys = coords if geom.get("type") == "MultiPolygon" else [coords]
    for poly in polys:
        if poly:
            yield [_WGS84_VERS_L93.transform(lon, lat) for lon, lat in poly[0]]


def _etendue_parcelles_m(projet: dict) -> float:
    """Plus grande dimension (m) de l'emprise des parcelles, 0 si aucune."""
    xs, ys = [], []
    for parc in (projet.get("localisation") or {}).get("parcelles") or []:
        for x, y in _coords_geom(parc.get("geometry")):
            xs.append(x); ys.append(y)
    if not xs:
        return 0.0
    return max(max(xs) - min(xs), max(ys) - min(ys))


def _echelle_adaptee(projet: dict, defaut: int) -> int:
    """Plus petite échelle « ronde » qui montre les parcelles avec de l'air."""
    etendue = _etendue_parcelles_m(projet)
    if not etendue:
        return defaut
    for e in ECHELLES_PROCHES:
        if 0.42 * e >= etendue * 1.8:
            return e
    return ECHELLES_PROCHES[-1]


def _getmap(couche: str, bbox: tuple, w_px: int, h_px: int) -> Image.Image:
    """GetMap WMS 1.3.0 en EPSG:2154 (axes est/nord : bbox minx,miny,maxx,maxy)."""
    # les serveurs WMS plafonnent la taille : on demande au plus 2048 px
    facteur = min(1.0, 2048 / max(w_px, h_px))
    params = {
        "SERVICE": "WMS", "VERSION": "1.3.0", "REQUEST": "GetMap",
        "LAYERS": couche, "STYLES": "",
        "CRS": "EPSG:2154",
        "BBOX": ",".join(f"{v:.2f}" for v in bbox),
        "WIDTH": int(w_px * facteur), "HEIGHT": int(h_px * facteur),
        "FORMAT": "image/png",
    }
    try:
        with httpx.Client(timeout=40.0) as client:
            resp = client.get(WMS_URL, params=params)
    except httpx.HTTPError as exc:
        raise GeoApiError("WMS Géoplateforme", f"appel impossible ({exc.__class__.__name__})") from exc
    if resp.status_code != 200 or "image" not in resp.headers.get("content-type", ""):
        raise GeoApiError("WMS Géoplateforme", f"HTTP {resp.status_code} ({couche})",
                          status=resp.status_code)
    img = Image.open(io.BytesIO(resp.content)).convert("RGB")
    if facteur < 1.0:
        img = img.resize((w_px, h_px), Image.LANCZOS)
    return img


def _planche_carte(projet: dict, titre: str, couche: str, echelle: int,
                   source: str, parcelles_en_evidence: bool = True) -> Image.Image:
    planche = Planche(titre, projet)
    x0, y0, x1, y1 = planche.content_box
    w_px, h_px = x1 - x0, y1 - y0

    # bbox L93 pour l'échelle demandée sur A3 (m/px constant sur la planche)
    m_par_px = echelle / base.PX_PAR_MM / 1000.0
    cx, cy = _centre_l93(projet)
    demi_w, demi_h = w_px * m_par_px / 2, h_px * m_par_px / 2
    bbox = (cx - demi_w, cy - demi_h, cx + demi_w, cy + demi_h)

    fond = _getmap(couche, bbox, w_px, h_px)
    planche.coller_contenu(fond)

    def vers_px(x: float, y: float) -> tuple[int, int]:
        return (round(x0 + (x - bbox[0]) / m_par_px),
                round(y0 + (bbox[3] - y) / m_par_px))

    # parcelles : contour vert + remplissage léger
    calque = Image.new("RGBA", planche.img.size, (0, 0, 0, 0))
    dr_calque = ImageDraw.Draw(calque)
    a_parcelles = False
    for parc in (projet.get("localisation") or {}).get("parcelles") or []:
        for anneau in _anneaux_geom(parc.get("geometry")):
            pts = [vers_px(x, y) for x, y in anneau]
            if len(pts) >= 3:
                a_parcelles = True
                if parcelles_en_evidence:
                    dr_calque.polygon(pts, fill=(5, 219, 121, 60), outline=(4, 150, 84, 255))
                    dr_calque.line(pts + [pts[0]], fill=(4, 150, 84, 255), width=5)
                else:
                    dr_calque.line(pts + [pts[0]], fill=(119, 109, 248, 255), width=5)
    planche.img.paste(calque, (0, 0), calque)

    # repère du site (croix) si pas de parcelle dessinée
    if not a_parcelles:
        px, py = vers_px(cx, cy)
        d = planche.draw
        d.line([(px - 26, py), (px + 26, py)], fill=VIOLET, width=6)
        d.line([(px, py - 26), (px, py + 26)], fill=VIOLET, width=6)
        d.ellipse([px - 10, py - 10, px + 10, py + 10], outline=VIOLET, width=5)

    planche.echelle_txt = f"Échelle 1/{echelle:,} (A3)".replace(",", " ")
    planche.fleche_nord()
    planche.echelle_graphique(m_par_px)
    planche.source(source)
    return planche.finaliser()


def planche_situation(projet: dict) -> Image.Image:
    """DP1a : plan de situation (Plan IGN v2, 1/10000)."""
    return _planche_carte(
        projet, "DP1 · Plan de situation", config.LAYER_PLAN, 10000,
        "Fond : Plan IGN v2 — IGN, Géoplateforme", parcelles_en_evidence=True,
    )


def planche_cadastrale(projet: dict) -> Image.Image:
    """DP1b : plan cadastral (Parcellaire Express, échelle adaptée)."""
    return _planche_carte(
        projet, "DP1 · Plan cadastral", config.LAYER_PARCELLES,
        _echelle_adaptee(projet, 2000),
        "Fond : Parcellaire Express (PCI) — IGN, Géoplateforme",
    )


def planche_aerienne(projet: dict) -> Image.Image:
    """DP1c : vue aérienne (BD ORTHO, échelle adaptée)."""
    return _planche_carte(
        projet, "DP1 · Vue aérienne", config.LAYER_ORTHO,
        _echelle_adaptee(projet, 2000),
        "Fond : BD ORTHO — IGN, Géoplateforme",
    )
