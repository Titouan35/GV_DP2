"""Cas de recette Soufflenheim contre les vraies APIs (marqué live).

Lancement : .venv/Scripts/python -m pytest -m live -v -o addopts=""
Référence : dossier réel ../EXEMPLE/PC_SOUFFLENHEIM (Allée du Golf, 67620).

Valeurs vérifiées contre les APIs le 2026-07-13 :
- INSEE Soufflenheim = 67472 (le 67473 de la maquette était illustratif)
- parcelle du projet : section 30, n° 0464, contenance 30 201 m²
- le point géocodé de l'adresse tombe sur une parcelle voisine (30-0646) :
  c'est le piège documenté au plan §10, d'où la validation manuelle.
- zonage PLU au droit du golf : UC3t ; hors secteur ABF.
"""
import pytest

from app.geo import cadastre, geocode, georisques, gpu

pytestmark = pytest.mark.live

ADRESSE = "Allée du Golf, 67620 Soufflenheim"
INSEE = "67472"


def test_geocodage_soufflenheim():
    resultats = geocode.rechercher_adresse(ADRESSE)
    assert resultats, "aucun résultat de géocodage"
    premier = resultats[0]
    assert premier["code_insee"] == INSEE
    assert premier["commune"] == "Soufflenheim"
    assert 7.9 < premier["lon"] < 8.0
    assert 48.7 < premier["lat"] < 48.9


def test_suggestion_par_position():
    r = geocode.rechercher_adresse(ADRESSE)[0]
    parcelles = cadastre.parcelles_par_position(r["lon"], r["lat"])
    assert parcelles, "aucune parcelle sous le point géocodé"
    assert parcelles[0]["code_insee"] == INSEE
    assert parcelles[0]["geometry"] is not None


def test_parcelle_reference_soufflenheim():
    parcelles = cadastre.parcelle_par_reference(INSEE, "30", "464")
    assert parcelles, "parcelle 30 0464 introuvable"
    p = parcelles[0]
    assert p["section"] == "30"
    assert p["numero"] == "0464"
    assert p["idu"] == "67472000300464"
    assert p["contenance_m2"] == 30201
    assert p["geometry"] is not None


def test_zonage_gpu_soufflenheim():
    r = geocode.rechercher_adresse(ADRESSE)[0]
    zonage = gpu.zonage_plu(r["lon"], r["lat"])
    assert zonage["disponible"] is True
    assert zonage["couvert"] is True
    assert zonage["zones"], "aucune zone PLU renvoyée"


def test_abf_soufflenheim():
    r = geocode.rechercher_adresse(ADRESSE)[0]
    servitudes = gpu.servitudes_abf(r["lon"], r["lat"])
    assert servitudes["disponible"] is True
    assert servitudes["abf"] is False


def test_risques_soufflenheim():
    res = georisques.risques_commune(INSEE)
    assert res["disponible"] in (True, False)
    if res["disponible"]:
        assert isinstance(res["risques"], list)
