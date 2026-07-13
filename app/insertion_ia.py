"""Module Insertion IA (phase 5) : génération de visuels commerciaux.

Couche d'abstraction fournisseur (Gemini par défaut, Azure OpenAI en option).
La clé API reste côté serveur (variable d'environnement), jamais dans le
navigateur. Ces visuels sont réservés au commercial, étiquetés « visuel IA »,
et ne sont jamais utilisés comme pièce DP6 (plan §6 bis).

Fournisseur Gemini : API generateContent + responseModalities (voie stable).
Modèle par défaut « nano banana » (gemini-2.5-flash-image), surchargeable par
la variable GVDP_GEMINI_MODEL si Google renomme le modèle.
"""
from __future__ import annotations

import base64
import io
import os
from datetime import datetime

import httpx
from PIL import Image, ImageDraw

from . import config
from .catalogue import parametres_effectifs

FOURNISSEURS = ("gemini", "azure-openai")
GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"
GEMINI_MODELE_DEFAUT = "gemini-2.5-flash-image"
COULEUR_ZONE = (229, 57, 53)  # rouge de la zone d'implantation


class InsertionError(Exception):
    """Erreur du module Insertion IA (config, réseau, quota, sécurité)."""


# ---------------------------------------------------------------- statut

def _fournisseur() -> str:
    return os.environ.get("GVDP_IMAGE_PROVIDER", "gemini").lower()


def statut() -> dict:
    """État de configuration du module (jamais la clé elle-même)."""
    fournisseur = _fournisseur()
    cles = {
        "gemini": bool(os.environ.get("GEMINI_API_KEY")),
        "azure-openai": bool(
            os.environ.get("AZURE_OPENAI_API_KEY")
            and os.environ.get("AZURE_OPENAI_ENDPOINT")
        ),
    }
    return {
        "fournisseur": fournisseur,
        "modele": os.environ.get("GVDP_GEMINI_MODEL", GEMINI_MODELE_DEFAUT),
        "configure": cles.get(fournisseur, False),
        "cles_detectees": cles,
        "guide": [
            "1. Ouvrir aistudio.google.com/apikey (compte Google) et cliquer « Create API key ».",
            "2. Copier la clé (commence par AIza…).",
            "3. Sur ce poste : définir la variable d'environnement GEMINI_API_KEY avec cette valeur.",
            "4. Relancer GV_DP : l'étape Insertion IA s'active automatiquement.",
        ],
        "rappels": [
            "Visuels réservés au commercial, étiquetés « visuel IA » ; jamais en pièce DP6.",
            "Free tier gratuit dans les quotas Google ; ~0,05 à 0,15 € par image ensuite.",
            "Les photos transitent chez Google (peu sensible pour un parking).",
        ],
    }


# ---------------------------------------------------------------- prompt

def _prompt_systeme(projet: dict, avec_zone: bool, avec_plan: bool,
                    avec_coupe: bool) -> str:
    ins = projet.get("insertion") or {}
    omb = projet.get("ombriere") or {}
    p = parametres_effectifs(omb) if (omb.get("famille") or omb.get("puissance_kwc")) else None

    lignes = [
        "Tu es un outil de photomontage photoréaliste. À partir de la PHOTO du site "
        "fournie, insère une ombrière photovoltaïque de parking de façon crédible, "
        "en conservant intégralement le décor, la perspective, la lumière et le grain "
        "de la photo d'origine.",
    ]
    if avec_zone:
        lignes.append(
            "La PREMIÈRE image montre un rectangle rouge semi-transparent : implante "
            "l'ombrière EXACTEMENT dans cette zone rouge, puis efface totalement le "
            "tracé rouge du rendu final."
        )
    if avec_plan:
        lignes.append(
            "Une image de PLAN DE MASSE indique l'implantation souhaitée (orientation "
            "des rangées) : respecte-la."
        )
    if avec_coupe:
        lignes.append(
            "Une image de COUPE technique montre la structure exacte à reproduire "
            "(silhouette, proportions, inclinaison)."
        )
    if p:
        lignes.append(
            f"Caractéristiques de l'ombrière : type {p['famille']}, "
            f"{p['longueur_m']:g} m de long sur {p['profondeur_m']:g} m, "
            f"hauteur hors tout {p['h_haut_m']:.2f} m, pente {p['pente_deg']:g}°, "
            f"structure en acier galvanisé gris clair avec fines bandes bleues sur les "
            f"poteaux, couverture de modules photovoltaïques full black (noirs mats, "
            f"non réfléchissants)."
        )
    else:
        lignes.append(
            "Ombrière de parking classique : poteaux acier galvanisé gris clair, "
            "modules photovoltaïques full black."
        )

    dist = ins.get("repere_distance_m") or 2.5
    desc = ins.get("repere_desc") or "la largeur d'une place de stationnement"
    lignes.append(
        f"Repère d'échelle : {desc} vaut {dist:g} m ; dimensionne l'ombrière en "
        f"cohérence avec ce repère visible sur la photo."
    )
    lignes.append(
        "Cohérence lumineuse : les ombres portées de l'ombrière doivent avoir la même "
        "direction et la même longueur que celles des objets déjà présents (mâts, "
        "arbres, voitures). Harmonise le grain et la netteté avec la photo source."
    )
    consignes = (ins.get("consignes") or "").strip()
    if consignes:
        lignes.append(f"Consignes complémentaires de l'utilisateur : {consignes}")
    lignes.append("Rends uniquement l'image finale, photoréaliste, sans texte ajouté.")
    return "\n".join(lignes)


# ---------------------------------------------------------------- images

def _photo_annotee(chemin_photo, zone) -> bytes:
    """Photo du site avec la zone d'implantation en rouge semi-transparent."""
    img = Image.open(chemin_photo).convert("RGB")
    if zone and len(zone) == 4:
        calque = Image.new("RGBA", img.size, (0, 0, 0, 0))
        dr = ImageDraw.Draw(calque)
        w, h = img.size
        x0, y0, x1, y1 = zone
        box = [min(x0, x1) * w, min(y0, y1) * h, max(x0, x1) * w, max(y0, y1) * h]
        dr.rectangle(box, fill=COULEUR_ZONE + (70,), outline=COULEUR_ZONE + (255,), width=6)
        img = Image.alpha_composite(img.convert("RGBA"), calque).convert("RGB")
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=92)
    return buf.getvalue()


def _part_image(donnees: bytes, mime: str = "image/jpeg") -> dict:
    return {"inline_data": {"mime_type": mime, "data": base64.b64encode(donnees).decode()}}


def _extraire_image(reponse: dict) -> bytes:
    """Récupère la première image d'une réponse generateContent."""
    for cand in reponse.get("candidates", []):
        for part in (cand.get("content") or {}).get("parts", []):
            inline = part.get("inlineData") or part.get("inline_data")
            if inline and inline.get("data"):
                return base64.b64decode(inline["data"])
    # pas d'image : souvent un blocage sécurité, on remonte le texte explicatif
    for cand in reponse.get("candidates", []):
        for part in (cand.get("content") or {}).get("parts", []):
            if part.get("text"):
                raise InsertionError(f"Aucune image renvoyée : {part['text'][:200]}")
    raise InsertionError("Aucune image renvoyée par le modèle (réponse vide).")


def _appel_gemini(parts: list[dict]) -> bytes:
    cle = os.environ.get("GEMINI_API_KEY")
    if not cle:
        raise InsertionError("Clé GEMINI_API_KEY absente : suivez le guide de l'étape 5.")
    modele = os.environ.get("GVDP_GEMINI_MODEL", GEMINI_MODELE_DEFAUT)
    url = f"{GEMINI_BASE}/models/{modele}:generateContent"
    corps = {
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {"responseModalities": ["TEXT", "IMAGE"]},
    }
    try:
        with httpx.Client(timeout=120.0) as client:
            resp = client.post(url, headers={"x-goog-api-key": cle}, json=corps)
    except httpx.HTTPError as exc:
        raise InsertionError(f"Appel Gemini impossible ({exc.__class__.__name__}).") from exc
    if resp.status_code == 429:
        raise InsertionError("Quota Gemini atteint : réessayez dans quelques instants.")
    if resp.status_code == 404:
        raise InsertionError(
            f"Modèle « {modele} » introuvable : définissez GVDP_GEMINI_MODEL "
            f"(ex. gemini-3.1-flash-image)."
        )
    if resp.status_code != 200:
        detail = resp.text[:200]
        raise InsertionError(f"Gemini a répondu HTTP {resp.status_code} : {detail}")
    return _extraire_image(resp.json())


# ---------------------------------------------------------------- génération

def _dossier_insertion(projet_id: str):
    d = config.assets_dir(projet_id) / "insertion"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _references(projet: dict) -> tuple[list[dict], dict]:
    """Construit les parts image (photo annotée + plan + coupe) et les flags."""
    ins = projet.get("insertion") or {}
    if not ins.get("photo"):
        raise InsertionError("Ajoutez d'abord une photo du site (étape 5).")
    chemin_photo = config.PROJETS_DIR / ins["photo"]
    if not chemin_photo.exists():
        raise InsertionError("Photo du site introuvable sur le disque.")

    flags = {"zone": bool(ins.get("zone")), "plan": False, "coupe": False}
    parts_images = [_part_image(_photo_annotee(chemin_photo, ins.get("zone")))]

    # plan de masse (upload BE) en référence d'implantation
    plan = (projet.get("documents") or {}).get("dp2")
    if plan:
        chemin_plan = config.PROJETS_DIR / plan["fichier"]
        if chemin_plan.exists() and chemin_plan.suffix.lower() in (".png", ".jpg", ".jpeg"):
            parts_images.append(_part_image(chemin_plan.read_bytes(),
                                            "image/png" if chemin_plan.suffix.lower() == ".png" else "image/jpeg"))
            flags["plan"] = True

    # coupe DP3 générée (structure exacte du type choisi)
    coupe = config.assets_dir(projet["id"]) / "dp3_coupe.png"
    if coupe.exists():
        parts_images.append(_part_image(coupe.read_bytes(), "image/png"))
        flags["coupe"] = True
    return parts_images, flags


def _rel(chemin) -> str:
    return str(chemin.relative_to(config.PROJETS_DIR)).replace("\\", "/")


def generer(projet: dict, nb_variantes: int = 2, horodatage: str | None = None) -> list[dict]:
    """Génère nb_variantes insertions. Renvoie la liste des variantes créées."""
    if _fournisseur() != "gemini":
        raise InsertionError("Seul le fournisseur Gemini est branché pour l'instant.")
    if not statut()["configure"]:
        raise InsertionError("Module non configuré : clé GEMINI_API_KEY absente.")

    parts_images, flags = _references(projet)
    prompt = _prompt_systeme(projet, flags["zone"], flags["plan"], flags["coupe"])
    parts = [{"text": prompt}, *parts_images]

    dossier = _dossier_insertion(projet["id"])
    stamp = horodatage or datetime.now().strftime("%Y%m%d-%H%M%S")
    variantes = []
    erreurs = []
    for i in range(max(1, min(nb_variantes, 3))):
        try:
            image = _appel_gemini(parts)
        except InsertionError as exc:
            erreurs.append(str(exc))
            continue
        nom = f"insertion_{stamp}_{i + 1}.png"
        (dossier / nom).write_bytes(image)
        variantes.append({
            "fichier": _rel(dossier / nom),
            "prompt": prompt,
            "date": datetime.now().isoformat(timespec="seconds"),
            "etiquette": "visuel IA — usage commercial",
        })
    if not variantes:
        raise InsertionError(erreurs[0] if erreurs else "Aucune variante générée.")
    return variantes


def retoucher(projet: dict, fichier_variante: str, instruction: str,
              horodatage: str | None = None) -> dict:
    """Retouche une variante existante selon une instruction en langage naturel."""
    if not statut()["configure"]:
        raise InsertionError("Module non configuré : clé GEMINI_API_KEY absente.")
    chemin = config.PROJETS_DIR / fichier_variante
    if not chemin.exists():
        raise InsertionError("Variante introuvable.")
    prompt = (
        "Modifie l'image fournie selon cette instruction, en conservant le reste "
        f"inchangé et le réalisme : {instruction.strip()}. Rends uniquement l'image."
    )
    image = _appel_gemini([{"text": prompt}, _part_image(chemin.read_bytes(), "image/png")])
    dossier = _dossier_insertion(projet["id"])
    stamp = horodatage or datetime.now().strftime("%Y%m%d-%H%M%S")
    nom = f"retouche_{stamp}.png"
    (dossier / nom).write_bytes(image)
    return {
        "fichier": _rel(dossier / nom),
        "prompt": prompt,
        "date": datetime.now().isoformat(timespec="seconds"),
        "etiquette": "visuel IA — usage commercial",
    }
