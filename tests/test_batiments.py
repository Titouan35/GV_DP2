"""Emprise bâtie : géométrie hors ligne + appel réel (marqueur live)."""
import math

import pytest

from app.geo import batiments as B
from app.geo.client import GeoApiError

LON0, LAT0 = 2.35, 48.85
M_LAT = 1 / 111_320                      # degré de latitude par mètre
M_LON = 1 / (111_320 * math.cos(math.radians(LAT0)))


def carre(x0, y0, x1, y1, trou=None):
    """Rectangle en mètres autour de (LON0, LAT0) → GeoJSON WGS84."""
    def pt(x, y):
        return [LON0 + x * M_LON, LAT0 + y * M_LAT]
    anneaux = [[pt(x0, y0), pt(x1, y0), pt(x1, y1), pt(x0, y1), pt(x0, y0)]]
    if trou:
        a, b, c, d = trou
        anneaux.append([pt(a, b), pt(a, d), pt(c, d), pt(c, b), pt(a, b)])
    return {"type": "Polygon", "coordinates": anneaux}


PARCELLE = {"code_insee": "75056", "geometry": carre(0, 0, 100, 100)}


def _bdtopo(monkeypatch, geoms):
    monkeypatch.setattr(B, "_bdtopo", lambda bbox: [
        {"id": f"B{i}", "nature": "Indifférenciée", "usage": "Commercial et services",
         "hauteur_m": 6.0, "geometry": g} for i, g in enumerate(geoms)])


def test_batiment_entier_sur_la_parcelle(monkeypatch):
    _bdtopo(monkeypatch, [carre(10, 10, 50, 30)])  # 40 x 20 = 800 m²
    r = B.batiments_sur_parcelles([PARCELLE])
    assert r["source"] == "BD TOPO (IGN)"
    (b,) = r["batiments"]
    assert b["surface_m2"] == pytest.approx(800, rel=0.01)
    assert b["surface_parcelles_m2"] == b["surface_m2"]
    assert not b["a_cheval"]
    assert r["emprise_batie_m2"] == pytest.approx(800, rel=0.01)


def test_batiment_a_cheval_compte_pour_sa_part(monkeypatch):
    _bdtopo(monkeypatch, [carre(80, 10, 120, 30)])  # moitié dehors
    (b,) = B.batiments_sur_parcelles([PARCELLE])["batiments"]
    assert b["a_cheval"]
    assert b["surface_m2"] == pytest.approx(800, rel=0.01)
    assert b["surface_parcelles_m2"] == pytest.approx(400, rel=0.02)


def test_batiment_voisin_ignore_et_trou_deduit(monkeypatch):
    _bdtopo(monkeypatch, [carre(150, 10, 190, 30),
                          carre(10, 10, 50, 50, trou=(20, 20, 30, 30))])
    r = B.batiments_sur_parcelles([PARCELLE])
    assert len(r["batiments"]) == 1
    assert r["batiments"][0]["surface_m2"] == pytest.approx(1600 - 100, rel=0.01)


def test_ordre_lat_lon_remis_dans_l_ordre(monkeypatch):
    g = carre(10, 10, 50, 30)
    inverse = {"type": "Polygon",
               "coordinates": [[[p[1], p[0]] for p in a] for a in g["coordinates"]]}
    _bdtopo(monkeypatch, [inverse])
    bbox = B._bbox_wgs([B._polygones(PARCELLE["geometry"])])
    remis = B._remettre_lon_lat(inverse, bbox)
    assert remis["coordinates"][0][0][0] == pytest.approx(g["coordinates"][0][0])


def test_secours_cadastre_si_bdtopo_tombe(monkeypatch):
    def panne(bbox):
        raise GeoApiError("Géoplateforme WFS (BD TOPO)", "HTTP 503", 503)
    monkeypatch.setattr(B, "_bdtopo", panne)
    monkeypatch.setattr(B, "_cadastre_etalab", lambda codes: [
        {"id": "PCI-1", "nature": "Bâti dur", "usage": None, "hauteur_m": None,
         "geometry": carre(10, 10, 30, 30)}])
    r = B.batiments_sur_parcelles([PARCELLE])
    assert r["source"] == "Cadastre (PCI)"
    assert r["avertissement"]
    assert r["emprise_batie_m2"] == pytest.approx(400, rel=0.01)


@pytest.mark.live
def test_live_bdtopo_mehun():
    """Parcelle réelle : récupérée par API Carto puis bâtiments BD TOPO."""
    from app.geo import cadastre
    parcelles = cadastre.parcelles_par_position(2.2205, 47.1442)  # Mehun-sur-Yèvre
    assert parcelles
    r = B.batiments_sur_parcelles(parcelles)
    assert r["source"] == "BD TOPO (IGN)"
    assert isinstance(r["batiments"], list)
