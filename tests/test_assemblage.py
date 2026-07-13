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
    # garde + 3 placeholders DP1 + DP2 + DP3 + DP4 + DP6/7/8 + notice (4) + cerfa + checklist
    assert len(prs.slides) >= 12
