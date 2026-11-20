"""Notices ERP accessibilité et sécurité (app/notices_erp.py)."""
import zipfile

import pytest

from app import notices_erp


def _texte(chemin):
    with zipfile.ZipFile(chemin) as z:
        return z.read("word/document.xml").decode("utf-8")


def _projet(**ombriere):
    omb = {"nb_modules_total": 322, "module_puissance_wc": 490, "puissance_kwc": 157.78,
           "nb_ombrieres": 2, "nb_onduleurs": 1, "modele_onduleur": "SUNGROW SG125CX-P2"}
    omb.update(ombriere)
    return {"id": "test-notices-erp", "documents": {}, "erp": {"type": "M", "categorie": 5},
            "localisation": {"adresse": "79 Rue Alexandre Bérard 01500 Ambérieu-en-Bugey",
                             "code_postal": "01500", "commune": "Ambérieu-en-Bugey"},
            "ombriere": omb}


@pytest.mark.parametrize("modele, fiche", [
    ("SUNGROW SG50CX-P2", "SUNGROW_SG36_40_50CX-P2.pdf"),
    ("SG36CX-P2", "SUNGROW_SG36_40_50CX-P2.pdf"),
    ("SUNGROW 125 CX-P2", "SUNGROW_SG125CX-P2.pdf"),
    ("Sungrow SG150CX", "SUNGROW_SG150CX.pdf"),
    ("Huawei SUN2000", None),
    (None, None),
])
def test_fiche_onduleur(modele, fiche):
    trouvee = notices_erp.fiche_onduleur(modele)
    assert (trouvee.name if trouvee else None) == fiche


def test_les_fiches_existent():
    for _, chemin in notices_erp.FICHES_ONDULEUR:
        assert chemin.exists()
    assert notices_erp.FICHE_MODULE.exists()


def test_notices_adaptees_au_projet(tmp_path):
    acc, sec = tmp_path / "acc.docx", tmp_path / "sec.docx"
    assert notices_erp.generer_accessibilite(_projet(), acc) == []
    avert = notices_erp.generer_securite(_projet(), sec)
    assert avert == ["À compléter : plan de masse en annexe 7.2 (pièce DP2, étape 4)."]
    for chemin in (acc, sec):
        xml = _texte(chemin)
        assert "MEHUN" not in xml and "Châtelet" not in xml
        assert "01500 AMBÉRIEU-EN-BUGEY" in xml
        assert "type M – 5e catégorie" in xml
        assert "21/10/2026" not in xml
    xml = _texte(sec)
    assert "322" in xml and "157,78" in xml and " ombrières de parking" in xml
    assert " onduleur photovoltaïque positionné" in xml
    with zipfile.ZipFile(sec) as z:     # fiches modules + onduleur, 2 pages chacune
        assert len([n for n in z.namelist() if "annexe_erp_" in n]) == 4


def test_case_type_m_notice_accessibilite(tmp_path):
    """Case « Type M » : les deux notices portent « Type M » seul, même sans
    catégorie."""
    p = _projet()
    p["erp"]["type_m_seul"] = True
    acc, sec = tmp_path / "acc.docx", tmp_path / "sec.docx"
    assert notices_erp.generer_accessibilite(p, acc) == []
    xml = _texte(acc)
    assert "Type M" in xml and "catégorie</w:t>" not in xml.split("Type et catégorie")[-1][:400]
    assert "5e catégorie" not in xml
    p["erp"]["categorie"] = None
    assert notices_erp.generer_accessibilite(p, acc) == []
    avert = notices_erp.generer_securite(p, sec)
    assert not any("catégorie" in a for a in avert)
    xml = _texte(sec)
    assert "Type M" in xml and "5e catégorie" not in xml


def test_donnees_manquantes_signalees(tmp_path):
    p = _projet(nb_ombrieres=None, modele_onduleur=None)
    p["erp"]["categorie"] = None
    avert = notices_erp.generer_securite(p, tmp_path / "sec.docx")
    assert any("catégorie d'ERP" in a for a in avert)
    assert any("nombre d'ombrières" in a for a in avert)
    assert any("onduleur" in a for a in avert)
    assert notices_erp.A_COMPLETER in _texte(tmp_path / "sec.docx")


def test_export_word_choisi_pour_un_docx(monkeypatch, tmp_path):
    """Sous Windows, un .docx part dans Word et non dans PowerPoint."""
    from app import export_pdf
    appels = []
    monkeypatch.setattr(export_pdf.sys, "platform", "win32")
    monkeypatch.setattr(export_pdf, "_export_word",
                        lambda src, pdf: (appels.append("word"), pdf.write_bytes(b"%PDF")))
    monkeypatch.setattr(export_pdf, "_export_powerpoint",
                        lambda src, pdf: (appels.append("pptx"), pdf.write_bytes(b"%PDF")))
    source = tmp_path / "notice_securite.docx"
    source.write_bytes(b"")
    assert export_pdf.exporter_pdf(source) == tmp_path / "notice_securite.pdf"
    assert appels == ["word"]


def test_pieds_de_page_numerotes_par_champs(tmp_path):
    """Les pieds des sections Annexes étaient figés (« 12 / 14 ») dans le
    gabarit : toutes les pages de fiches affichaient le même numéro."""
    sec = tmp_path / "sec.docx"
    notices_erp.generer_securite(_projet(), sec)
    with zipfile.ZipFile(sec) as z:
        pieds = [z.read(n).decode() for n in z.namelist() if n.startswith("word/footer")]
        doc = z.read("word/document.xml").decode()
    assert pieds and all(" PAGE " in x and " NUMPAGES " in x for x in pieds if "/</w:t>" in x)
    assert 'w:start="1"' not in doc
