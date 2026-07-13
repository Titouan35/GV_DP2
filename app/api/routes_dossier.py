"""Notice, Cerfa, assemblage du dossier et statut du module Insertion IA."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from .. import config, insertion_ia, notice, regles
from ..assemblage import generer_dossier
from ..cerfa import NUMERO_CERFA, preremplir
from ..export_pdf import exporter_pdf
from .routes_projets import _charger, _sauver

router = APIRouter(prefix="/api", tags=["dossier"])


@router.post("/projets/{projet_id}/notice/generer")
def generer_notice(projet_id: str, force: int = 0):
    projet = _charger(projet_id)
    sections = notice.generer_sections(projet.model_dump())
    for cle, texte in sections.items():
        if force or not (projet.notice.sections.get(cle) or "").strip():
            projet.notice.sections[cle] = texte
    projet.notice.genere_par_ia = False  # gabarit déterministe (pas de LLM)
    projet.date_modification = datetime.now().isoformat(timespec="seconds")
    _sauver(projet)
    return {"projet": projet, "evaluation": regles.evaluer(projet)}


@router.post("/projets/{projet_id}/cerfa")
def generer_cerfa(projet_id: str):
    projet = _charger(projet_id)
    try:
        chemin, champs = preremplir(projet.model_dump())
    except FileNotFoundError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    projet.documents["cerfa"] = {
        "nom_fichier": chemin.name,
        "fichier": str(chemin.relative_to(config.PROJETS_DIR)).replace("\\", "/"),
        "date": datetime.now().isoformat(timespec="seconds"),
        "genere": True,
        "cerfa": NUMERO_CERFA,
    }
    projet.date_modification = datetime.now().isoformat(timespec="seconds")
    _sauver(projet)
    return {
        "projet": projet,
        "evaluation": regles.evaluer(projet),
        "champs_remplis": len(champs),
        "cerfa": NUMERO_CERFA,
    }


@router.get("/projets/{projet_id}/cerfa.pdf")
def telecharger_cerfa(projet_id: str):
    chemin = config.assets_dir(projet_id) / "cerfa_16702_prerempli.pdf"
    if not chemin.exists():
        raise HTTPException(status_code=404, detail="Cerfa non généré.")
    return FileResponse(chemin, filename=chemin.name, media_type="application/pdf")


@router.post("/projets/{projet_id}/dossier")
def assembler_dossier(projet_id: str):
    projet = _charger(projet_id)
    chemin, avertissements = generer_dossier(projet.model_dump())
    return {
        "fichier": chemin.name,
        "telechargement": f"/api/projets/{projet_id}/dossier.pptx",
        "avertissements": avertissements,
    }


@router.get("/projets/{projet_id}/dossier.pptx")
def telecharger_dossier(projet_id: str):
    chemin = config.assets_dir(projet_id) / f"Dossier_DP_{projet_id}.pptx"
    if not chemin.exists():
        raise HTTPException(status_code=404, detail="Dossier non assemblé.")
    return FileResponse(chemin, filename=chemin.name)


@router.post("/projets/{projet_id}/dossier/pdf")
def convertir_dossier_pdf(projet_id: str):
    chemin_pptx = config.assets_dir(projet_id) / f"Dossier_DP_{projet_id}.pptx"
    if not chemin_pptx.exists():
        raise HTTPException(status_code=404, detail="Assemblez d'abord le dossier (PPTX).")
    try:
        chemin_pdf = exporter_pdf(chemin_pptx)
    except RuntimeError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    return {
        "fichier": chemin_pdf.name,
        "telechargement": f"/api/projets/{projet_id}/dossier.pdf",
    }


@router.get("/projets/{projet_id}/dossier.pdf")
def telecharger_dossier_pdf(projet_id: str):
    chemin = config.assets_dir(projet_id) / f"Dossier_DP_{projet_id}.pdf"
    if not chemin.exists():
        raise HTTPException(status_code=404, detail="PDF non généré.")
    return FileResponse(chemin, filename=chemin.name, media_type="application/pdf")


@router.get("/insertion/statut")
def statut_insertion():
    return insertion_ia.statut()
