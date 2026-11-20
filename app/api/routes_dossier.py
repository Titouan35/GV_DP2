"""Notice, Cerfa et assemblage du dossier."""
from __future__ import annotations

import tempfile
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from .. import (cerfa_erp, coherence, config, decoupe_pdf, lecture_fve, nomenclature, notice,
               notices_erp, regles)
from ..assemblage import generer_dossier
from ..cerfa import NUMERO_CERFA, preremplir
from ..cerfa import rapport as cerfa_rapport
from ..export_pdf import exporter_pdf
from .routes_projets import _charger, exiger_projet_id, projet_a_modifier

# Toutes les routes portent {projet_id} : validé avant la route (exiger_projet_id)
router = APIRouter(prefix="/api", tags=["dossier"],
                   dependencies=[Depends(exiger_projet_id)])


@router.post("/projets/{projet_id}/notice/generer")
def generer_notice(projet_id: str, force: int = 0):
    # sous le verrou du projet, du chargement à l'écriture (projet_a_modifier)
    with projet_a_modifier(projet_id) as projet:
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
    # sous le verrou du projet, du chargement à l'écriture (projet_a_modifier)
    with projet_a_modifier(projet_id) as projet:
        # Les statuts viennent du moteur réglementaire : le bordereau de dépôt ne
        # coche que les pièces que la checklist de l'outil dit réellement prêtes.
        statuts = {p["code"]: p["statut"]
                   for p in regles.completude(projet)["pieces"]}
        try:
            chemin, champs, avertissements = preremplir(projet.model_dump(), statuts)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        except ValueError as exc:  # régime PC : le Cerfa DP ne s'applique pas
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        projet.documents["cerfa"] = {
            # nom PRÉSENTÉ (CERFA_16702-03_AMBERT.pdf) ; le fichier garde sur le
            # disque son nom technique, dont dépend le cache de régénération
            "nom_fichier": nomenclature.cerfa(projet.model_dump()),
            "fichier": str(chemin.relative_to(config.espace())).replace("\\", "/"),
            "date": datetime.now().isoformat(timespec="seconds"),
            "genere": True,
            "cerfa": NUMERO_CERFA,
        }
        projet.date_modification = datetime.now().isoformat(timespec="seconds")
    return {
        "projet": projet,
        "evaluation": regles.evaluer(projet),
        "champs_remplis": len(champs),
        "cerfa": NUMERO_CERFA,
        "avertissements": avertissements,
        # bilan détaillé : ce qui est porté au formulaire, ce qui manque
        # encore, et ce que l'outil ne remplira jamais (cf. cerfa.rapport)
        "rapport": cerfa_rapport(dict.fromkeys(champs, True), avertissements),
    }


@router.post("/projets/{projet_id}/cerfa-erp")
def generer_cerfa_erp(projet_id: str):
    """Cerfa 13824*04, autorisation de travaux ERP (app/cerfa_erp.py).

    Disponible sur TOUT projet existant : rien de nouveau n'est exigé, ce qui
    manque est signalé. Enregistré sous `documents["cerfa_erp"]`, hors de la
    checklist des 11 pièces pour ne pas changer la complétude des dossiers
    déjà montés.
    """
    # sous le verrou du projet, du chargement à l'écriture (projet_a_modifier)
    with projet_a_modifier(projet_id) as projet:
        statuts = {p["code"]: p["statut"] for p in regles.completude(projet)["pieces"]}
        try:
            chemin, champs, avertissements = cerfa_erp.preremplir(projet.model_dump(), statuts)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        projet.documents["cerfa_erp"] = {
            "nom_fichier": nomenclature.cerfa_erp(projet.model_dump()),
            "fichier": str(chemin.relative_to(config.espace())).replace("\\", "/"),
            "date": datetime.now().isoformat(timespec="seconds"),
            "genere": True,
            "cerfa": cerfa_erp.NUMERO_CERFA,
        }
        projet.date_modification = datetime.now().isoformat(timespec="seconds")
    return {
        "projet": projet,
        "evaluation": regles.evaluer(projet),
        "champs_remplis": len(champs),
        "cerfa": cerfa_erp.NUMERO_CERFA,
        "avertissements": avertissements,
        "rapport": cerfa_erp.rapport(champs, avertissements),
    }


@router.get("/projets/{projet_id}/cerfa-erp.pdf")
def telecharger_cerfa_erp(projet_id: str):
    chemin = config.assets_dir(projet_id) / "cerfa_13824_prerempli.pdf"
    if not chemin.exists():
        raise HTTPException(status_code=404, detail="Cerfa 13824 non généré.")
    return FileResponse(chemin, media_type="application/pdf",
                        filename=nomenclature.cerfa_erp(_charger(projet_id).model_dump()))


@router.post("/projets/{projet_id}/notices-erp")
def generer_notices_erp(projet_id: str):
    """Notices accessibilité et sécurité de l'autorisation ERP (app/notices_erp.py).

    Les projets montés avant le 28/10/2026 ont une FVE déposée mais pas
    l'onduleur, que la lecture ne retenait pas : on le relit ici, en ne
    remplissant que les cases vides (même règle que la lecture de la FVE).
    """
    # sous le verrou du projet, du chargement à l'écriture (projet_a_modifier)
    with projet_a_modifier(projet_id) as projet:
        omb = projet.ombriere
        fve = (projet.documents or {}).get("fve")
        if fve and (not omb.modele_onduleur or not omb.nb_onduleurs):
            # confiné à l'espace : `fichier` vient du navigateur (config.chemin_dans_espace)
            chemin_fve = config.chemin_dans_espace(fve.get("fichier"))
            if chemin_fve is not None and chemin_fve.exists():
                for cle, valeur in lecture_fve.lire_onduleur(chemin_fve).items():
                    if getattr(omb, cle) in (None, ""):
                        setattr(omb, cle, valeur)
        try:
            chemins, avertissements = notices_erp.generer(projet.model_dump())
        except (FileNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        maintenant = datetime.now().isoformat(timespec="seconds")
        for quoi, chemin in chemins.items():
            projet.documents[f"notice_{quoi}"] = {
                "nom_fichier": nomenclature.notice_erp(projet.model_dump(), quoi),
                "fichier": str(chemin.relative_to(config.espace())).replace("\\", "/"),
                "date": maintenant,
                "genere": True,
            }
        projet.date_modification = maintenant
    return {
        "projet": projet,
        "evaluation": regles.evaluer(projet),
        "avertissements": avertissements,
        "telechargements": {q: f"/api/projets/{projet_id}/notice-{q}.docx" for q in chemins},
    }


@router.post("/projets/{projet_id}/notices-erp/pdf")
def exporter_notices_erp_pdf(projet_id: str):
    """Les deux notices ERP en PDF (Word sur le poste, LibreOffice ailleurs).

    Régénère d'abord les .docx : le PDF part ainsi toujours des données
    actuelles du projet (catégorie cochée, onduleurs…), sans exiger un premier
    clic sur « Notices ERP (Word) ».
    """
    data = generer_notices_erp(projet_id)
    assets = config.assets_dir(projet_id)
    for quoi in ("accessibilite", "securite"):
        try:
            exporter_pdf(assets / f"notice_{quoi}.docx")
        except RuntimeError as exc:
            raise HTTPException(status_code=501, detail=str(exc)) from exc
    data["telechargements"] = {q: f"/api/projets/{projet_id}/notice-{q}.pdf"
                               for q in ("accessibilite", "securite")}
    return data


@router.get("/projets/{projet_id}/notice-{quoi}.pdf")
def telecharger_notice_erp_pdf(projet_id: str, quoi: str):
    if quoi not in ("accessibilite", "securite"):
        raise HTTPException(status_code=404, detail="Notice inconnue.")
    chemin = config.assets_dir(projet_id) / f"notice_{quoi}.pdf"
    if not chemin.exists():
        raise HTTPException(status_code=404, detail="Notice PDF non générée.")
    nom = nomenclature.notice_erp(_charger(projet_id).model_dump(), quoi)
    return FileResponse(chemin, media_type="application/pdf",
                        filename=nom.removesuffix(".docx") + ".pdf")


@router.get("/projets/{projet_id}/notice-{quoi}.docx")
def telecharger_notice_erp(projet_id: str, quoi: str):
    if quoi not in ("accessibilite", "securite"):
        raise HTTPException(status_code=404, detail="Notice inconnue.")
    chemin = config.assets_dir(projet_id) / f"notice_{quoi}.docx"
    if not chemin.exists():
        raise HTTPException(status_code=404, detail="Notice non générée.")
    return FileResponse(
        chemin, filename=nomenclature.notice_erp(_charger(projet_id).model_dump(), quoi),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")


@router.get("/projets/{projet_id}/cerfa.pdf")
def telecharger_cerfa(projet_id: str):
    chemin = config.assets_dir(projet_id) / "cerfa_16702_prerempli.pdf"
    if not chemin.exists():
        raise HTTPException(status_code=404, detail="Cerfa non généré.")
    # Le projet est relu POUR SON NOM DE COMMUNE : sans lui, tous les Cerfa
    # sortaient sous « cerfa_16702_prerempli.pdf », impossibles à distinguer
    # dans un dossier de téléchargements (cf. app/nomenclature.py).
    return FileResponse(chemin, media_type="application/pdf",
                        filename=nomenclature.cerfa(_charger(projet_id).model_dump()))


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
        "fichier": nomenclature.dossier(projet.model_dump(), "pptx"),
        "telechargement": f"/api/projets/{projet_id}/dossier.pptx",
        "avertissements": avertissements,
        "depot": bool(depot),
    }


@router.get("/projets/{projet_id}/dossier.pptx")
def telecharger_dossier(projet_id: str):
    chemin = config.assets_dir(projet_id) / f"Dossier_DP_{projet_id}.pptx"
    if not chemin.exists():
        raise HTTPException(status_code=404, detail="Dossier non assemblé.")
    return FileResponse(chemin,
                        filename=nomenclature.dossier(_charger(projet_id).model_dump(), "pptx"))


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
        "fichier": nomenclature.dossier(_charger(projet_id).model_dump(), "pdf"),
        "telechargement": f"/api/projets/{projet_id}/dossier.pdf",
    }


@router.post("/projets/{projet_id}/dossier/pages")
def decouper_dossier_en_pages(projet_id: str):
    """Une page par pièce, regroupées en archive.

    Les portails d'urbanisme réclament les pièces une par une : sans ça, le BE
    redécoupait le PDF à la main avant chaque dépôt.
    """
    assets = config.assets_dir(projet_id)
    chemin_pdf = assets / f"Dossier_DP_{projet_id}.pdf"
    if not chemin_pdf.exists():
        raise HTTPException(status_code=404,
                            detail="Exportez d'abord le dossier en PDF.")
    projet = _charger(projet_id).model_dump()
    # Les pages sont découpées dans un dossier TEMPORAIRE : seule l'archive
    # reste dans les assets. Le dépôt vit dans OneDrive, y laisser quinze PDF
    # régénérables à chaque export ferait grossir la synchro pour rien.
    with tempfile.TemporaryDirectory() as tmp:
        pages = decoupe_pdf.decouper(
            chemin_pdf, assets / f"Dossier_DP_{projet_id}.pptx", Path(tmp))
        if not pages:
            raise HTTPException(status_code=500, detail="Le PDF ne contient aucune page.")
        noms = [p.name for p in pages]
        decoupe_pdf.archiver(pages, assets / f"pages_{projet_id}.zip")
    return {
        "fichier": nomenclature.pages_separees(projet),
        "telechargement": f"/api/projets/{projet_id}/dossier-pages.zip",
        "pages": noms,
    }


@router.get("/projets/{projet_id}/dossier-pages.zip")
def telecharger_pages(projet_id: str):
    chemin = config.assets_dir(projet_id) / f"pages_{projet_id}.zip"
    if not chemin.exists():
        raise HTTPException(status_code=404, detail="Pages non découpées.")
    return FileResponse(chemin, media_type="application/zip",
                        filename=nomenclature.pages_separees(_charger(projet_id).model_dump()))


@router.get("/projets/{projet_id}/dossier.pdf")
def telecharger_dossier_pdf(projet_id: str):
    chemin = config.assets_dir(projet_id) / f"Dossier_DP_{projet_id}.pdf"
    if not chemin.exists():
        raise HTTPException(status_code=404, detail="PDF non généré.")
    return FileResponse(chemin, media_type="application/pdf",
                        filename=nomenclature.dossier(_charger(projet_id).model_dump(), "pdf"))
