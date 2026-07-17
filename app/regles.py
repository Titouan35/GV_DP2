"""Moteur réglementaire : régime DP/PC, pièces attendues, complétude.

Références (PLAN_OUTIL_DP.md §2 et §4) :
- Décret 2024-1023 (demandes >= 01/12/2024) : ombrière PV >= 3 kWc et
  < 3 MWc → DP quelle que soit la hauteur ; PC si >= 3 MWc ou secteur ABF.
- Pièces du dossier DP : R.431-36 du code de l'urbanisme.
"""
from __future__ import annotations

from . import config
from .models import Projet

REGIME_DP = "DP"
REGIME_PC = "PC"

# Pièces du dossier DP : code, titre, mode de production.
# mode : auto (générée) / mixte (paramétrique ou tracé, repli upload) / be (upload)
PIECES_DP = [
    {"code": "garde", "titre": "Page de garde", "mode": "auto"},
    {"code": "dp1_situation", "titre": "DP1 · Plan de situation", "mode": "auto"},
    {"code": "dp1_cadastral", "titre": "DP1 · Plan cadastral", "mode": "auto"},
    {"code": "dp1_aerien", "titre": "DP1 · Vue aérienne", "mode": "auto"},
    {"code": "dp2", "titre": "DP2 · Plan de masse", "mode": "mixte"},
    {"code": "dp3", "titre": "DP3 · Plan en coupe", "mode": "be"},
    {"code": "dp6", "titre": "DP6 · Insertion paysagère", "mode": "be"},
    {"code": "dp7", "titre": "DP7 · Photo environnement proche", "mode": "be"},
    {"code": "dp8", "titre": "DP8 · Photo paysage lointain", "mode": "be"},
    {"code": "dp11", "titre": "DP11 · Notice descriptive", "mode": "auto"},
    {"code": "cerfa", "titre": "Cerfa 16702 (DP) pré-rempli", "mode": "auto"},
]

# Renumérotation 2025-2026 des formulaires d'urbanisme : la DP « autres
# constructions » n'est plus le 13404 mais le 16702*03 (vérifié le
# 13/07/2026 sur entreprendre.service-public.gouv.fr, fiche R2028).
CERFA_DP = "16702*03"
CERFA_PC = "16700 (à vérifier)"


def determiner_regime(puissance_kwc: float | None, secteur_abf: bool | None) -> dict:
    """Régime d'urbanisme et raisons, à partir de la puissance et du secteur ABF."""
    raisons = []
    regime = REGIME_DP
    if puissance_kwc is not None and puissance_kwc >= config.SEUIL_PC_KWC:
        regime = REGIME_PC
        raisons.append("Puissance ≥ 3 MWc (décret 2024-1023)")
    if secteur_abf:
        regime = REGIME_PC
        raisons.append("Secteur protégé ABF (abords MH, site, SPR)")
    if regime == REGIME_DP:
        raisons.append("Ombrière PV < 3 MWc hors secteur protégé → Déclaration Préalable")
    return {
        "regime": regime,
        "raisons": raisons,
        "cerfa": CERFA_DP if regime == REGIME_DP else CERFA_PC,
    }


def _statut_piece(piece: dict, projet: Projet) -> dict:
    """Statut d'une pièce selon l'avancement du projet (heuristiques Phase 1)."""
    code, mode = piece["code"], piece["mode"]
    loc = projet.localisation
    loc_ok = bool(loc.code_insee and loc.parcelles)

    if code in projet.documents:
        return {**piece, "statut": "prete", "detail": "fournie"}

    if mode == "auto":
        if code in ("garde",):
            statut = "prete" if projet.nom and projet.mo.raison_sociale else "a_completer"
            detail = "générée depuis le projet" if statut == "prete" else "renseigner projet + MO"
        elif code.startswith("dp1_"):
            statut = "prete" if loc_ok else "a_completer"
            detail = "générée (IGN)" if loc_ok else "localisation à valider"
        elif code == "dp11":
            sections = projet.notice.sections or {}
            if any((v or "").strip() for v in sections.values()):
                if projet.notice.valide_humain:
                    statut, detail = "prete", "relue et validée"
                else:
                    statut, detail = "a_completer", "brouillon à relire et valider"
            else:
                statut, detail = "a_generer", "brouillon à générer (étape 6)"
        else:  # cerfa
            statut = "a_generer"
            detail = "à pré-remplir (étape 7)"
        return {**piece, "statut": statut, "detail": detail}

    # mixte (DP2) et be : pièce attendue du bureau d'études
    return {**piece, "statut": "en_attente", "detail": "upload BE attendu"}


def completude(projet: Projet) -> dict:
    """Checklist des pièces avec statuts + ratio de complétude."""
    pieces = [_statut_piece(p, projet) for p in PIECES_DP]
    pretes = sum(1 for p in pieces if p["statut"] == "prete")
    return {"pieces": pieces, "pretes": pretes, "total": len(pieces)}


def evaluer(projet: Projet) -> dict:
    """Évaluation réglementaire complète d'un projet (régime + checklist)."""
    regime = determiner_regime(
        projet.ombriere.puissance_kwc, projet.urbanisme.secteur_abf
    )
    return {"regime": regime, "completude": completude(projet)}
