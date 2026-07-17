"""Assemblage PPTX (offline : générateurs de cartes neutralisés)."""
from pptx import Presentation

from app import assemblage, config
from tests.test_notice_cerfa import PROJET


def test_dossier_pptx_offline(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROJETS_DIR", tmp_path)
    # pas de réseau dans les tests : on neutralise les cartes DP1,
    # la coupe paramétrique DP3 reste réelle (offline)
    generateurs = {
        code: fn for code, fn in assemblage.GENERATEURS.items()
        if code.startswith("dp3")
    }
    monkeypatch.setattr(assemblage, "GENERATEURS", generateurs)

    projet = {**PROJET, "notice": {"sections": {"presentation": "Texte de test."},
                                   "valide_humain": True}}
    chemin, avertissements = assemblage.generer_dossier(projet)
    assert chemin.exists()
    assert avertissements == []

    prs = Presentation(str(chemin))
    # maquette : garde + DP1 fusion + aérienne + DP2 + DP3 + notice
    # + DP6 + photos DP7/8 = 8 planches (DP4 retirée le 17/07/2026)
    assert len(prs.slides) == 8
    # format 16:9 de la maquette (1280 x 720 px)
    assert prs.slide_width == assemblage.P(1280)


def test_insertions_selectionnees_ajoutent_des_planches(tmp_path, monkeypatch):
    """2 insertions IA cochées, pas de DP6 BE : la 1re sert d'état projeté,
    la 2e a sa propre planche « visuel d'illustration »."""
    from PIL import Image

    monkeypatch.setattr(config, "PROJETS_DIR", tmp_path)
    monkeypatch.setattr(assemblage, "GENERATEURS", {})
    dossier = tmp_path / "p-test.assets" / "insertion"
    dossier.mkdir(parents=True)
    rels = []
    for i in range(2):
        f = dossier / f"insertion_{i}.png"
        Image.new("RGB", (320, 180), (30 * i + 40, 60, 80)).save(f)
        rels.append(f"p-test.assets/insertion/insertion_{i}.png")

    projet = {**PROJET, "id": "p-test",
              "insertion": {"dans_dossier": rels}}
    chemin, _ = assemblage.generer_dossier(projet)
    prs = Presentation(str(chemin))
    # 8 planches de base - 1 (DP3 sans générateur ici, remplacée par placeholder)
    # => base 8, + 1 planche insertion supplémentaire = 9
    assert len(prs.slides) == 9
