"""Export PDF du dossier : conversion du PPTX via PowerPoint (COM, Windows).

Sur Mac (pas de COM), l'export PDF se fait à la main depuis PowerPoint ;
le .pptx reste le format de travail (plan §16).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

FORMAT_PDF = 32  # ppSaveAsPDF


def exporter_pdf(chemin_pptx: Path) -> Path:
    if sys.platform != "win32":
        raise RuntimeError(
            "Export PDF automatique disponible sous Windows uniquement "
            "(ouvrir le .pptx dans PowerPoint et exporter en PDF)."
        )
    chemin_pdf = chemin_pptx.with_suffix(".pdf")
    if chemin_pdf.exists():
        chemin_pdf.unlink()
    script = f"""
$ErrorActionPreference = 'Stop'
$pp = New-Object -ComObject PowerPoint.Application
$pres = $pp.Presentations.Open("{chemin_pptx}", $true, $true, $false)
$pres.SaveAs("{chemin_pdf}", {FORMAT_PDF})
$pres.Close()
$pp.Quit()
"""
    resultat = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, timeout=240,
    )
    if not chemin_pdf.exists():
        raise RuntimeError(
            "Export PDF impossible (PowerPoint est-il installé ?) : "
            + (resultat.stderr or resultat.stdout)[:400]
        )
    return chemin_pdf
