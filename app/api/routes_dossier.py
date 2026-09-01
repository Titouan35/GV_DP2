"""Notice, Cerfa et assemblage du dossier."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from .. import coherence, config, notice, regles
from ..assemblage import generer_dossier
from ..cerfa import NUMERO_CERFA, preremplir
from ..export_pdf import exporter_pdf
from .routes_projets import _charger, _sauver

router = APIRouter(prefix="/api", tags=["dossier"])


@router.post("/projets/{projet_id}/notice/generer")
def generer_notice(projet_id: str, force: int = 0):
    projet = _charger(projet_id)
    data = projet.model_dump()
    sections = notice.generer_sections(data)
    for cle, texte in sections.items():
        if force or not (projet.notice.sections.get(cle) or "").strip():
            projet.notice.sections[cle] = texte
    projet.notice.genere_par_ia = False  # gabarit déterministe (pas de LLM)
    # empreinte des valeurs portées par le texte : c'est elle qui permettra de
    # dire plus tard si la notice a divergé des données du projet
    projet.notice.valeurs = notice.valeurs_ancrage(data)
    projet.date_modification = datetime.now().isoformat(timespec="seconds")
    _sauver(projet)
    return {
        "projet": projet,
        "evaluation": regles.evaluer(projet),
        "variables": notice.variables(data),
    }


@router.get("/projets/{projet_id}/notice/variables")
def variables_notice(projet_id: str):
    """Valeurs concrètes du projet à surligner dans la notice (relecture)."""
    projet = _charger(projet_id)
    return {"variables": notice.variables(projet.model_dump())}


@router.post("/projets/{projet_id}/cerfa")
def generer_cerfa(projet_id: str):
    projet = _charger(projet_id)
    try:
        chemin, champs, avertissements = preremplir(projet.model_dump())
    except FileNotFoundError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except ValueError as exc:  # régime PC : le Cerfa DP ne s'applique pas
        raise HTTPException(status_code=400, detail=str(exc)) from exc
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
        "avertissements": avertissements,
    }


@router.get("/projets/{projet_id}/cerfa.pdf")
def telecharger_cerfa(projet_id: str):
    chemin = config.assets_dir(projet_id) / "cerfa_16702_prerempli.pdf"
    if not chemin.exists():
        raise HTTPException(status_code=404, detail="Cerfa non généré.")
    return FileResponse(chemin, filename=chemin.name, media_type="application/pdf")


@router.post("/projets/{projet_id}/dossier")
def assembler_dossier(projet_id: str, depot: int = 0):
    """Assemble le PPTX. `depot=1` = contrôle bloquant : refuse (409) tant que
    toutes les pièces ne sont pas prêtes, pour qu'un dossier incomplet ne
    parte jamais en mairie par distraction. Sans `depot`, mode brouillon :
    on assemble et on signale les manques en avertissements."""
    projet = _charger(projet_id)
    if depot:
        # Deux refus distincts. L'incohérence passe en premier : un dossier
        # complet dont les données se contredisent est plus dangereux qu'un
        # dossier visiblement incomplet, parce que rien n'alerte le déposant.
        incoherences = coherence.bloquantes(projet.model_dump())
        if incoherences:
            raise HTTPException(
                status_code=409,
                detail="Dossier incohérent pour un dépôt : "
                       + " ; ".join(a["message"] for a in incoherences),
            )
        manquantes = [
            f"{p['titre']} ({p['detail']})"
            for p in regles.completude(projet)["pieces"] if p["statut"] != "prete"
        ]
        if manquantes:
            raise HTTPException(
                status_code=409,
                detail="Dossier incomplet pour un dépôt : " + " ; ".join(manquantes),
            )
    chemin, avertissements = generer_dossier(projet.model_dump())
    return {
        "fichier": chemin.name,
        "telechargement": f"/api/projets/{projet_id}/dossier.pptx",
        "avertissements": avertissements,
        "depot": bool(depot),
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
