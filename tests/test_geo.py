"""Noyau géo : parsing des réponses API (mocks) + padding des références."""
from app.geo import cadastre, georisques
from app.geo.cadastre import _feature_vers_parcelle
from app.geo.georisques import _depuis_gaspar, _depuis_rapport

FEATURE_SOUFFLENHEIM = {
    "type": "Feature",
    "geometry": {"type": "MultiPolygon", "coordinates": [[[[7.95, 48.83]]]]},
    "properties": {
        "idu": "67473000300464",
        "section": "30",
        "numero": "0464",
        "com_abs": "000",
        "code_dep": "67",
        "code_com": "473",
        "nom_com": "Soufflenheim",
        "contenance": 30201,
    },
}


def test_feature_vers_parcelle():
    p = _feature_vers_parcelle(FEATURE_SOUFFLENHEIM)
    assert p["section"] == "30"
    assert p["numero"] == "0464"
    assert p["code_insee"] == "67473"
    assert p["contenance_m2"] == 30201
    assert p["geometry"]["type"] == "MultiPolygon"


def test_feature_vide_ne_plante_pas():
    p = _feature_vers_parcelle({"properties": {}})
    assert p["com_abs"] == "000"
    assert p["contenance_m2"] is None


def test_lookup_pad_section_et_numero(monkeypatch):
    """La saisie '30' / '464' doit partir en section '30', numéro '0464'."""
    capture = {}

    def faux_get_json(service, url, params=None):
        capture.update(params)
        return {"features": [FEATURE_SOUFFLENHEIM]}

    monkeypatch.setattr(cadastre, "get_json", faux_get_json)
    res = cadastre.parcelle_par_reference("67473", "30", "464")
    assert capture["section"] == "30"
    assert capture["numero"] == "0464"
    assert capture["com_abs"] == "000"
    assert res[0]["idu"] == "67473000300464"


def test_lookup_pad_section_une_lettre(monkeypatch):
    monkeypatch.setattr(
        cadastre, "get_json", lambda s, u, params=None: {"features": []}
    )
    capture = {}

    def faux_get_json(service, url, params=None):
        capture.update(params)
        return {"features": []}

    monkeypatch.setattr(cadastre, "get_json", faux_get_json)
    cadastre.parcelle_par_reference("67473", "b", "12")
    assert capture["section"] == "0B"
    assert capture["numero"] == "0012"


def test_rapport_risque_parsing():
    data = {
        "risquesNaturels": {
            "inondation": {"present": True, "libelle": "Inondation"},
            "seisme": {"present": False, "libelle": "Séisme"},
        },
        "risquesTechnologiques": {
            "icpe": {"present": True, "libelle": "ICPE"},
        },
    }
    risques = _depuis_rapport(data)
    libelles = {r["libelle"] for r in risques}
    assert libelles == {"Inondation", "ICPE"}


def test_gaspar_parsing_dedoublonne():
    data = {
        "data": [
            {"libelle_risque_long": "Inondation"},
            {"libelle_risque_long": "Inondation"},
            {"libelle_risque_long": "Séisme"},
        ]
    }
    assert len(_depuis_gaspar(data)) == 2


def test_risques_fallback_gaspar(monkeypatch):
    """Si rapport_risque échoue, on bascule sur GASPAR sans erreur."""
    from app.geo.client import GeoApiError

    appels = []

    def faux_get_json(service, url, params=None):
        appels.append(url)
        if "rapport_risque" in url:
            raise GeoApiError("Géorisques", "HTTP 500", status=500)
        return {"data": [{"libelle_risque_long": "Inondation"}]}

    monkeypatch.setattr(georisques, "get_json", faux_get_json)
    res = georisques.risques_commune("67473")
    assert res["disponible"] is True
    assert res["source"] == "gaspar"
    assert len(res["risques"]) == 1
