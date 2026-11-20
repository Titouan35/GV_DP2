"""Point 7 « Gestion des eaux pluviales » optionnel (demande du 12/11/2026).

Case décochée par défaut : le point 7 ne figure pas dans la notice et
« Chantier et remise en état » devient le point 7. Cochée : 8 sections.
"""
from app import notice
from app.models import Notice


def test_case_decochee_par_defaut():
    assert Notice().eaux_pluviales is False


def test_sans_point_7_numerotation_continue():
    actives = notice.sections_actives({"notice": {}})
    assert [c for c, _ in actives] == [
        "presentation", "etat_initial", "description", "insertion",
        "reglementaire", "acces_reseaux", "chantier"]
    assert [t.split(".")[0] for _, t in actives] == [str(i) for i in range(1, 8)]
    assert actives[-1][1] == "7. Chantier et remise en état"


def test_avec_point_7_comportement_actuel():
    actives = notice.sections_actives({"notice": {"eaux_pluviales": True}})
    assert actives == notice.SECTIONS
