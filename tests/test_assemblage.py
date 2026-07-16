"""Assemblage PPTX (offline : générateurs de cartes neutralisés)."""
from pptx import Presentation

from app import assemblage, config
from tests.test_notice_cerfa import PROJET


def test_dossier_pptx_offline(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROJETS_DIR", tmp_path)
    # pas de réseau dans les tests : on neutralise les cartes DP1,
    # les dessins paramétriques DP3/DP4 restent réels (offline)
    generateurs = {
        code: fn for code, fn in assemblage.GENERATEURS.items()
        if code.startswith("dp3") or code.startswith("dp4")
    }
    monkeypatch.setattr(assemblage, "GENERATEURS", generateurs)

    projet = {**PROJET, "notice": {"sections": {"presentation": "Texte de test."},
                                   "valide_humain": True}}
    chemin, avertissements = assemblage.generer_dossier(projet)
    assert chemin.exists()
    assert avertissements == []

    prs = Presentation(str(chemin))
    # maquette : garde + DP1 fusion + aérienne + DP2 + DP3 + DP4 + notice
    # + DP6 + photos DP7/8 + cerfa + checklist = 11 planches
    assert len(prs.slides) == 11
    # format 16:9 de la maquette (1280 x 720 px)
    assert prs.slide_width == assemblage.P(1280)
