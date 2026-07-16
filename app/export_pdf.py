"""Export PDF du dossier : PPTX → PDF, selon la plateforme.

- Windows (poste BE) : conversion via PowerPoint (COM), rendu fidèle à la charte.
- Linux (conteneur Azure) : conversion via LibreOffice headless (`soffice`).
- Ailleurs sans outil : on lève une erreur claire, le .pptx reste le format de
  travail (plan §16) et l'utilisateur exporte à la main depuis PowerPoint.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

FORMAT_PDF = 32  # ppSaveAsPDF (PowerPoint COM)


def _export_powerpoint(chemin_pptx: Path, chemin_pdf: Path) -> None:
    script = f"""
$ErrorActionPreference = 'Stop'
$pp = New-Object -ComObject PowerPoint.Application
$pres = $pp.Presentations.Open("{chemin_pptx}", $true, $true, $false)
$pres.SaveAs("{chemin_pdf}", {FORMAT_PDF})
$pres.Close()
$pp.Quit()
"""
    subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, timeout=240,
    )


def _export_libreoffice(chemin_pptx: Path, chemin_pdf: Path) -> None:
    """Conversion Linux/macOS via LibreOffice headless (soffice/libreoffice)."""
    binaire = shutil.which("soffice") or shutil.which("libreoffice")
    if not binaire:
        raise RuntimeError(
            "LibreOffice introuvable : installez-le (paquet libreoffice) ou exportez "
            "le .pptx en PDF à la main."
        )
    subprocess.run(
        [binaire, "--headless", "--convert-to", "pdf", "--outdir",
         str(chemin_pdf.parent), str(chemin_pptx)],
        capture_output=True, text=True, timeout=240,
    )


def exporter_pdf(chemin_pptx: Path) -> Path:
    chemin_pdf = chemin_pptx.with_suffix(".pdf")
    if chemin_pdf.exists():
        chemin_pdf.unlink()

    if sys.platform == "win32":
        _export_powerpoint(chemin_pptx, chemin_pdf)
    else:
        _export_libreoffice(chemin_pptx, chemin_pdf)

    if not chemin_pdf.exists():
        raise RuntimeError(
            "Export PDF impossible (PowerPoint ou LibreOffice indisponible ?). "
            "Le .pptx reste exportable à la main depuis PowerPoint."
        )
    return chemin_pdf
