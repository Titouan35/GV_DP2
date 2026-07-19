"""Export PDF du dossier : PPTX → PDF, selon la plateforme.

- Windows (poste BE) : conversion via PowerPoint (COM), rendu fidèle à la charte.
- Linux (conteneur Azure) : conversion via LibreOffice headless (`soffice`).
- Ailleurs sans outil : on lève une erreur claire, le .pptx reste le format de
  travail (plan §16) et l'utilisateur exporte à la main depuis PowerPoint.

Fiabilité (audit 19/07/2026) : le code de retour est vérifié et stderr remonté
dans l'erreur (avant, tout échec devenait un « Export PDF impossible » muet) ;
le script PowerShell ferme TOUJOURS PowerPoint (try/finally), sans quoi des
POWERPNT.EXE orphelins s'accumulaient à chaque échec jusqu'à bloquer le poste.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

FORMAT_PDF = 32  # ppSaveAsPDF (PowerPoint COM)
TIMEOUT_S = 240


def _export_powerpoint(chemin_pptx: Path, chemin_pdf: Path) -> None:
    # try/finally : Quit() est appelé même quand Open/SaveAs lève, sinon le
    # process COM PowerPoint survit à l'erreur (constaté : accumulation).
    script = f"""
$ErrorActionPreference = 'Stop'
$pp = New-Object -ComObject PowerPoint.Application
try {{
    $pres = $pp.Presentations.Open("{chemin_pptx}", $true, $true, $false)
    try {{
        $pres.SaveAs("{chemin_pdf}", {FORMAT_PDF})
    }} finally {{
        $pres.Close()
    }}
}} finally {{
    $pp.Quit()
}}
"""
    try:
        res = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        # on ne tue PAS les POWERPNT existants (l'utilisateur peut avoir un
        # fichier ouvert avec des modifications) : message actionnable.
        raise RuntimeError(
            "Export PDF : PowerPoint n'a pas répondu dans les temps. Fermez les "
            "fenêtres/processus PowerPoint résiduels puis réessayez, ou exportez "
            "le .pptx en PDF à la main."
        ) from None
    if res.returncode != 0 and not chemin_pdf.exists():
        detail = (res.stderr or res.stdout or "").strip()[:400]
        raise RuntimeError(f"Export PDF via PowerPoint échoué : {detail or 'erreur inconnue'}")


def _export_libreoffice(chemin_pptx: Path, chemin_pdf: Path) -> None:
    """Conversion Linux/macOS via LibreOffice headless (soffice/libreoffice)."""
    binaire = shutil.which("soffice") or shutil.which("libreoffice")
    if not binaire:
        raise RuntimeError(
            "LibreOffice introuvable : installez-le (paquet libreoffice) ou exportez "
            "le .pptx en PDF à la main."
        )
    # profil utilisateur isolé : sans lui, deux conversions simultanées se
    # disputent le profil par défaut (« another instance is running »).
    profil = Path(tempfile.gettempdir()) / f"gvdp_lo_{uuid.uuid4().hex[:8]}"
    try:
        res = subprocess.run(
            [binaire, "--headless",
             f"-env:UserInstallation=file://{profil.as_posix()}",
             "--convert-to", "pdf", "--outdir",
             str(chemin_pdf.parent), str(chemin_pptx)],
            capture_output=True, text=True, timeout=TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError("Export PDF : LibreOffice n'a pas répondu dans les temps.") from None
    finally:
        shutil.rmtree(profil, ignore_errors=True)
    if res.returncode != 0 and not chemin_pdf.exists():
        detail = (res.stderr or res.stdout or "").strip()[:400]
        raise RuntimeError(f"Export PDF via LibreOffice échoué : {detail or 'erreur inconnue'}")


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
