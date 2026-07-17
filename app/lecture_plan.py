"""Lecture du plan de masse (DP2) : pré-remplissage des caractéristiques.

Les plans de masse du BE suivent le gabarit GREENVOLT NEXT FRANCE (export
CAO vectoriel, couche texte présente : pas d'OCR). On n'extrait QUE les
champs étiquetés du cartouche / bloc « Caractéristiques techniques »,
fiables car libellés constants :

- « Puissance DC = 105.84 kWc »        -> puissance_kwc
- « 216 modules AIKO-A490-... 490 Wc » -> module (nb, modèle, Wc)
- « 1762x1134x30mm »                   -> dimensions module
- « 6° »                               -> pente
- « +2.50m » / « +3.50m »              -> hauteurs bas / haut de rampant
- « SCALE: 1: 200 »                    -> échelle du plan
- « TITLE: ... » / « DRAWING N°: ... » -> traçabilité

Les cotes non étiquetées (longueur / largeur d'ombrière) ne sont PAS
déduites : trop ambiguës, elles restent à la saisie (décision 17/07/2026).
Résultat rangé dans projet.meta["plan_lecture"] ; les champs vides de
l'ombrière sont pré-remplis (jamais d'écrasement silencieux).
"""
from __future__ import annotations

import re
from pathlib import Path

import pypdfium2 as pdfium


def _texte_pdf(chemin: Path, max_pages: int = 3) -> str:
    """Texte brut des premières pages (couche texte vectorielle)."""
    doc = pdfium.PdfDocument(str(chemin))
    try:
        morceaux = []
        for i in range(min(len(doc), max_pages)):
            page_texte = doc[i].get_textpage()
            try:
                morceaux.append(page_texte.get_text_bounded() or "")
            finally:
                page_texte.close()
        return "\n".join(morceaux)
    finally:
        doc.close()


def _nombre(txt: str | None) -> float | None:
    if not txt:
        return None
    try:
        return float(txt.replace(",", "."))
    except ValueError:
        return None


def analyser_texte(texte: str) -> dict:
    """Extrait les champs étiquetés du cartouche. Tolère champs absents."""
    t = " ".join(texte.split())  # espaces normalisés

    def cherche(pattern: str, groupe: int = 1) -> str | None:
        m = re.search(pattern, t, re.IGNORECASE)
        return m.group(groupe).strip() if m else None

    lecture: dict = {}

    v = _nombre(cherche(r"Puissance\s*DC\s*=?\s*([\d.,]+)\s*kWc"))
    if v:
        lecture["puissance_kwc"] = v

    v = _nombre(cherche(r"([\d]{3,4})\s*Wc\b"))
    if v:
        lecture["module_puissance_wc"] = v

    m = re.search(r"(\d{3,4})\s*[x×]\s*(\d{3,4})\s*[x×]\s*\d+\s*mm", t, re.IGNORECASE)
    if m:
        lecture["module_dimensions"] = f"{m.group(1)} x {m.group(2)} mm"

    v = cherche(r"(\d+)\s*modules\b")
    if v:
        lecture["nb_modules"] = int(v)

    v = cherche(r"modules?\s+([A-Z][A-Z0-9][A-Z0-9\-]{3,})")
    if v:
        lecture["module_modele"] = v

    m = re.search(r"(\d+)\s*onduleurs?\s+([A-Za-z]+\s*[A-Za-z0-9\-]+)", t)
    if m:
        lecture["onduleurs"] = f"{m.group(1)} x {m.group(2).strip()}"

    # pente : un petit angle isolé en degrés (1 à 15°, hors températures etc.)
    m = re.search(r"(?<![\d.,])(\d{1,2})\s*[°º]", t)
    if m and 1 <= int(m.group(1)) <= 15:
        lecture["pente_deg"] = float(m.group(1))

    # hauteurs « +2.50m ... +3.50m » : bas et haut de rampant
    hauteurs = sorted({
        h for h in (_nombre(x) for x in re.findall(r"\+\s*([\d.,]+)\s*m\b", t))
        if h and 1.5 <= h <= 8.0
    })
    if len(hauteurs) >= 2:
        lecture["garde_au_sol_m"] = hauteurs[0]
        lecture["hauteur_hors_tout_m"] = hauteurs[-1]

    v = cherche(r"SCALE\s*:?\s*1\s*:\s*(\d+)")
    if v:
        lecture["echelle_plan"] = f"1/{v}"

    v = cherche(r"TITLE\s*:?\s*(.+?)\s+(?:DRAWN|DESCRIPTION|SCALE)")
    if v:
        lecture["titre_plan"] = v

    v = cherche(r"DRAWING\s*N\W*:?\s*([A-Z0-9_]+)")
    if v:
        lecture["reference_plan"] = v

    return lecture


# champs de la lecture reportables tels quels dans projet.ombriere
CHAMPS_OMBRIERE = (
    "puissance_kwc", "module_puissance_wc", "module_dimensions",
    "pente_deg", "garde_au_sol_m", "hauteur_hors_tout_m",
)


def appliquer_lecture(projet, lecture: dict) -> list[str]:
    """Pré-remplit les champs VIDES de l'ombrière depuis la lecture du plan.

    Renvoie la liste des champs effectivement remplis (surlignés « proposé »
    dans l'UI). Les valeurs déjà saisies ne sont jamais écrasées.
    """
    remplis: list[str] = []
    for champ in CHAMPS_OMBRIERE:
        if champ in lecture and getattr(projet.ombriere, champ, None) in (None, ""):
            setattr(projet.ombriere, champ, lecture[champ])
            remplis.append(champ)
    projet.meta["plan_lecture"] = lecture
    projet.meta["plan_champs_proposes"] = remplis
    return remplis


def lire_plan_masse(chemin: Path) -> dict:
    """Lecture complète d'un plan de masse PDF (vide si image ou PDF muet)."""
    if chemin.suffix.lower() != ".pdf":
        return {}
    try:
        return analyser_texte(_texte_pdf(chemin))
    except Exception:  # PDF corrompu ou chiffré : lecture silencieusement vide
        return {}
