"""Bâtiments existants sur les parcelles du projet → emprise au sol bâtie.

Source principale : BD TOPO de l'IGN (couche `batiment`, WFS Géoplateforme).
Contours relevés sur photo aérienne, au toit, débords compris : c'est la
définition de l'emprise au sol (art. R.420-1, « projection verticale du
volume, débords et surplomb inclus »). Mise à jour trimestrielle.

Secours : bâtiments du cadastre (PCI, export Etalab par commune), si la
Géoplateforme ne répond pas. Contours aux murs, sans débords : un peu plus
petits, et parfois anciens.

Seule la partie de chaque bâtiment SUR les parcelles sélectionnées compte :
un bâtiment à cheval sur la limite n'est retenu que pour sa part. Pas de
dépendance de géométrie (shapely, numpy) : l'intersection est mesurée par
balayage en bandes de 5 cm en mètres (projection équivalente locale),
l'aire totale par la formule exacte du polygone. L'écart sur une part
à cheval est de l'ordre du pour-mille.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import math

import httpx
from pyproj import Transformer

from .. import config
from .client import GeoApiError, get_json

# en dessous, c'est du bruit de numérisation le long d'une limite parcellaire
SEUIL_PART_M2 = 1.0
# bandes de balayage par bâtiment (une tous les 5 cm, dans ces bornes)
_BANDES_MIN, _BANDES_MAX = 200, 2000


# ---------------------------------------------------------------- géométrie

def _polygones(geom: dict | None) -> list[list[list[tuple[float, float]]]]:
    """GeoJSON Polygon/MultiPolygon → liste de polygones (liste d'anneaux 2D)."""
    if not geom:
        return []
    t, c = geom.get("type"), geom.get("coordinates") or []
    polys = [c] if t == "Polygon" else c if t == "MultiPolygon" else []
    return [[[(float(p[0]), float(p[1])) for p in anneau] for anneau in poly]
            for poly in polys if poly]


Anneau = list[tuple[float, float]]
Polys = list[list[Anneau]]


def _aire_anneau(a: Anneau) -> float:
    return 0.5 * sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(a, a[1:] + a[:1]))


def _aire(polys_m: Polys) -> float:
    """Aire exacte (m²) : extérieur moins trous, pour chaque polygone."""
    total = 0.0
    for poly in polys_m:
        ext = abs(_aire_anneau(poly[0]))
        trous = sum(abs(_aire_anneau(a)) for a in poly[1:])
        total += ext - trous
    return total


def _fusion(iv: list[tuple[float, float]]) -> list[tuple[float, float]]:
    out: list[list[float]] = []
    for a, b in sorted(iv):
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


def _intervalles(y: float, polys_m: Polys) -> list[tuple[float, float]]:
    """Segments [x1, x2] de la droite horizontale y situés dans le
    (multi)polygone : règle pair-impair par polygone (gère les trous)."""
    iv = []
    for poly in polys_m:
        xs = []
        for anneau in poly:
            for (x1, y1), (x2, y2) in zip(anneau, anneau[1:] + anneau[:1]):
                if (y1 > y) != (y2 > y):
                    xs.append(x1 + (y - y1) * (x2 - x1) / (y2 - y1))
        xs.sort()
        iv += list(zip(xs[0::2], xs[1::2]))
    return _fusion(iv)


def _longueur(iv) -> float:
    return sum(b - a for a, b in iv)


def _longueur_commune(u, v) -> float:
    i = j = 0
    total = 0.0
    while i < len(u) and j < len(v):
        a, b = max(u[i][0], v[j][0]), min(u[i][1], v[j][1])
        if b > a:
            total += b - a
        if u[i][1] < v[j][1]:
            i += 1
        else:
            j += 1
    return total


def _bbox(polys_m: Polys) -> tuple[float, float, float, float]:
    xs = [p[0] for poly in polys_m for p in poly[0]]
    ys = [p[1] for poly in polys_m for p in poly[0]]
    return min(xs), min(ys), max(xs), max(ys)


def _se_touchent(a, b) -> bool:
    return not (a[2] < b[0] or b[2] < a[0] or a[3] < b[1] or b[3] < a[1])


def part_sur_parcelles(bat_m: Polys, parcelles_m: list[Polys]) -> float:
    """Fraction (0-1) du bâtiment située sur l'union des parcelles.

    Balayage par bandes horizontales : sur chaque ligne médiane, longueur
    commune bâtiment ∩ parcelles. Les contours étant rectilignes, l'erreur ne
    vient que des bandes qui contiennent un sommet."""
    bb = _bbox(bat_m)
    proches = [p for p in parcelles_m if _se_touchent(bb, _bbox(p))]
    if not proches:
        return 0.0
    n = min(_BANDES_MAX, max(_BANDES_MIN, int((bb[3] - bb[1]) / 0.05)))
    pas = (bb[3] - bb[1]) / n
    l_bat = l_commun = 0.0
    for k in range(n):
        y = bb[1] + (k + 0.5) * pas
        ib = _intervalles(y, bat_m)
        if not ib:
            continue
        ip = _fusion([s for p in proches for s in _intervalles(y, p)])
        l_bat += _longueur(ib)
        l_commun += _longueur_commune(ib, ip)
    return l_commun / l_bat if l_bat else 0.0


# ---------------------------------------------------------------- projection

class _Projection:
    """WGS84 ↔ azimutale équivalente locale (aires justes, métropole et DROM)."""

    def __init__(self, lon0: float, lat0: float):
        laea = f"+proj=laea +lat_0={lat0} +lon_0={lon0} +datum=WGS84 +units=m"
        self._t = Transformer.from_crs("EPSG:4326", laea, always_xy=True)

    def polys(self, polys_wgs) -> Polys:
        out = []
        for poly in polys_wgs:
            anneaux = []
            for anneau in poly:
                x, y = self._t.transform([p[0] for p in anneau], [p[1] for p in anneau])
                anneaux.append(list(zip(x, y)))
            out.append(anneaux)
        return out


# ---------------------------------------------------------------- sources

def _bbox_wgs(parcelles_wgs) -> tuple[float, float, float, float]:
    pts = [pt for polys in parcelles_wgs for poly in polys for pt in poly[0]]
    lons, lats = [p[0] for p in pts], [p[1] for p in pts]
    return min(lons), min(lats), max(lons), max(lats)


def _remettre_lon_lat(geom: dict, bbox: tuple) -> dict:
    """Certains serveurs WFS rendent l'EPSG:4326 en (lat, lon) : on remet
    l'ordre GeoJSON (lon, lat) si le premier point tombe mieux ainsi."""
    polys = _polygones(geom)
    if not polys:
        return geom
    x, y = polys[0][0][0]
    cx, cy = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
    if abs(y - cx) + abs(x - cy) < abs(x - cx) + abs(y - cy):
        return {"type": "MultiPolygon",
                "coordinates": [[[[p[1], p[0]] for p in a] for a in poly] for poly in polys]}
    return geom


def _bdtopo(bbox: tuple) -> list[dict]:
    lon1, lat1, lon2, lat2 = bbox
    params = {
        "SERVICE": "WFS", "VERSION": "2.0.0", "REQUEST": "GetFeature",
        "TYPENAMES": config.BDTOPO_BATIMENT_LAYER,
        "OUTPUTFORMAT": "application/json",
        "SRSNAME": "EPSG:4326",
        # WFS 2.0 + URN EPSG:4326 : ordre des axes (lat, lon)
        "BBOX": f"{lat1},{lon1},{lat2},{lon2},urn:ogc:def:crs:EPSG::4326",
        "COUNT": "2000",
    }
    data = get_json("Géoplateforme WFS (BD TOPO)", config.GEOPF_WFS_URL, params)
    out = []
    for f in data.get("features", []):
        p = f.get("properties") or {}
        if str(p.get("etat_de_l_objet") or "").lower().startswith("en projet"):
            continue  # bâtiment pas encore construit
        out.append({
            "id": p.get("cleabs") or f.get("id"),
            "nature": p.get("nature"),
            "usage": p.get("usage_1"),
            "hauteur_m": p.get("hauteur"),
            "geometry": _remettre_lon_lat(f.get("geometry") or {}, bbox),
        })
    return out


def _telecharger_json_gz(service: str, url: str) -> dict:
    try:
        with httpx.Client(timeout=max(config.HTTP_TIMEOUT, 30), follow_redirects=True) as c:
            resp = c.get(url)
    except httpx.HTTPError as exc:
        raise GeoApiError(service, f"appel impossible ({exc.__class__.__name__})") from exc
    if resp.status_code != 200:
        raise GeoApiError(service, f"HTTP {resp.status_code}", status=resp.status_code)
    brut = resp.content
    if brut[:2] == b"\x1f\x8b":
        brut = gzip.decompress(brut)
    try:
        return json.loads(brut)
    except ValueError as exc:
        raise GeoApiError(service, "réponse non JSON") from exc


def _cadastre_etalab(codes_insee: set[str]) -> list[dict]:
    out = []
    for insee in sorted(codes_insee):
        dep = insee[:3] if insee.startswith("97") else insee[:2]
        url = config.ETALAB_BATIMENTS_URL.format(dep=dep, insee=insee)
        data = _telecharger_json_gz("Cadastre Etalab (bâtiments)", url)
        for f in data.get("features", []):
            p = f.get("properties") or {}
            geom = f.get("geometry") or {}
            ident = p.get("id") or hashlib.md5(
                json.dumps(geom.get("coordinates"))[:400].encode()).hexdigest()[:16]
            out.append({
                "id": f"PCI-{ident}",
                "nature": {"01": "Bâti dur", "02": "Bâti léger"}.get(str(p.get("type")), None),
                "usage": p.get("nom"),
                "hauteur_m": None,
                "geometry": geom,
            })
    return out


# ---------------------------------------------------------------- entrée

def batiments_sur_parcelles(parcelles: list[dict]) -> dict:
    """Bâtiments (BD TOPO, sinon cadastre) posés sur les parcelles données.

    `parcelles` : dicts avec `geometry` (GeoJSON WGS84) et `code_insee`.
    Renvoie {source, avertissement, batiments: [...], emprise_batie_m2}.
    Chaque bâtiment : id, nature, usage, hauteur_m, surface_m2 (totale),
    surface_parcelles_m2 (part sur les parcelles), a_cheval, geometry.
    """
    parcelles_wgs = [_polygones(p.get("geometry")) for p in parcelles]
    parcelles_wgs = [p for p in parcelles_wgs if p]
    if not parcelles_wgs:
        return {"source": None, "avertissement": "Aucune géométrie de parcelle.",
                "batiments": [], "emprise_batie_m2": 0}
    bbox = _bbox_wgs(parcelles_wgs)

    avertissement = None
    try:
        bruts, source = _bdtopo(bbox), "BD TOPO (IGN)"
    except GeoApiError as exc:
        codes = {p.get("code_insee") for p in parcelles if p.get("code_insee")}
        if not codes:
            raise
        bruts, source = _cadastre_etalab(codes), "Cadastre (PCI)"
        avertissement = (f"BD TOPO indisponible ({exc}) : bâtiments du cadastre, "
                         "contours aux murs, sans débords.")

    proj = _Projection((bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2)
    parcelles_m = [proj.polys(p) for p in parcelles_wgs]
    bb_parc = [_bbox(p) for p in parcelles_m]

    batiments, vus = [], set()
    for b in bruts:
        polys = _polygones(b["geometry"])
        if not polys or b["id"] in vus:
            continue
        bat_m = proj.polys(polys)
        bb = _bbox(bat_m)
        if not any(_se_touchent(bb, x) for x in bb_parc):
            continue
        surface = _aire(bat_m)
        part = part_sur_parcelles(bat_m, parcelles_m)
        sur = surface * part
        if sur < SEUIL_PART_M2:
            continue
        vus.add(b["id"])
        batiments.append({
            "id": b["id"],
            "source": source,
            "nature": b["nature"],
            "usage": b["usage"],
            "hauteur_m": b["hauteur_m"],
            "surface_m2": round(surface, 1),
            "surface_parcelles_m2": round(surface if part > 0.995 else sur, 1),
            "a_cheval": part <= 0.995,
            "geometry": {"type": "MultiPolygon",
                         "coordinates": [[[list(pt) for pt in a] for a in poly] for poly in polys]},
        })
    batiments.sort(key=lambda x: -x["surface_parcelles_m2"])
    total = round(sum(b["surface_parcelles_m2"] for b in batiments))
    return {"source": source, "avertissement": avertissement,
            "batiments": batiments, "emprise_batie_m2": total}
