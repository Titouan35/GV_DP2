"""Contrôles de cohérence du dossier : traque les données qui se contredisent.

Pourquoi ce module existe (01/09/2026). L'audit d'un dossier réel a montré
qu'un PPTX pouvait sortir avec deux adresses différentes selon la planche, et
un Cerfa officiel déclarer des parcelles situées à cent kilomètres du terrain,
sans qu'aucun avertissement ne soit levé. Le montage réussissait, la checklist
affichait « 11/11 pièces prêtes », et rien ne signalait quoi que ce soit.

Trois mécanismes produisaient cela :

1. La MÊME donnée vit à deux endroits. L'adresse existe en champ vif
   (`localisation.adresse`, relu à chaque assemblage) et en copie gelée dans le
   texte de la notice. La génération de la notice ne remplit que les sections
   VIDES : une adresse corrigée après coup n'y est jamais reportée.
2. Rien ne relie les parcelles à la commune du terrain. Changer l'adresse
   n'invalide pas les parcelles choisies pour l'adresse précédente.
3. Une pièce est comptée « prête » dès que son code figure dans le projet,
   sans vérifier que le fichier existe encore sur le disque.

Le parti pris est celui retenu avec Florent : « brouillon avec trous
signalés ». On ne bloque pas, on ne devine pas, on DIT. Chaque anomalie porte
une gravité, un message qui explique le problème, et l'endroit où le corriger.

Ce module ne lit que le dictionnaire projet et le disque. Il ne modifie rien.
"""
from __future__ import annotations

import unicodedata

from . import config

# Gravités, par ordre décroissant.
#   bloquante : le dossier serait FAUX s'il partait en mairie
#   serieuse  : incohérence certaine, sans être nécessairement rédhibitoire
#   attention : donnée manquante ou à confirmer
BLOQUANTE = "bloquante"
SERIEUSE = "serieuse"
ATTENTION = "attention"

_ORDRE = {BLOQUANTE: 0, SERIEUSE: 1, ATTENTION: 2}


def _norm(texte: str | None) -> str:
    """Comparaison de libellés insensible à la casse et aux accents."""
    if not texte:
        return ""
    sans_accent = unicodedata.normalize("NFKD", texte)
    sans_accent = "".join(c for c in sans_accent if not unicodedata.combining(c))
    return " ".join(sans_accent.lower().split())


def _anomalie(code, gravite, message, ou):
    return {"code": code, "gravite": gravite, "message": message, "ou": ou}


# --------------------------------------------------------------- localisation

def _controler_parcelles(projet: dict) -> list[dict]:
    """Les parcelles doivent être sur la commune du terrain.

    Cas réel qui a motivé ce contrôle : un projet dont l'adresse a été changée
    de Anse (69) à Montréal-la-Cluse (01) a gardé les parcelles d'Anse. Le
    Cerfa ne reprend que les trois premières lignes de parcelles : il aurait
    déclaré un terrain dans l'Ain avec deux parcelles du Rhône.
    """
    loc = projet.get("localisation") or {}
    parcelles = loc.get("parcelles") or []
    if not parcelles:
        return []

    insee_terrain = (loc.get("code_insee") or "").strip()
    communes = {}
    for parc in parcelles:
        insee = (parc.get("code_insee") or "").strip()
        if insee:
            communes.setdefault(insee, parc.get("commune") or insee)

    anomalies = []
    if len(communes) > 1:
        libelles = ", ".join(f"{nom} ({insee})" for insee, nom in sorted(communes.items()))
        anomalies.append(_anomalie(
            "parcelles_multi_communes", BLOQUANTE,
            f"Les parcelles appartiennent à plusieurs communes : {libelles}. "
            "Un dossier de déclaration préalable porte sur un seul terrain.",
            "Étape 1, tableau des parcelles"))

    if insee_terrain and communes and insee_terrain not in communes:
        libelles = ", ".join(f"{nom} ({insee})" for insee, nom in sorted(communes.items()))
        anomalies.append(_anomalie(
            "parcelles_hors_commune", BLOQUANTE,
            f"L'adresse est à {loc.get('commune') or insee_terrain} ({insee_terrain}) "
            f"mais aucune parcelle n'y est située : {libelles}. "
            "L'adresse a probablement été modifiée après le choix des parcelles.",
            "Étape 1, adresse et parcelles"))

    return anomalies


def _controler_adresse(projet: dict) -> list[dict]:
    loc = projet.get("localisation") or {}
    anomalies = []
    if (loc.get("parcelles") or []) and not (loc.get("adresse") or "").strip():
        anomalies.append(_anomalie(
            "adresse_absente", SERIEUSE,
            "Des parcelles sont choisies mais aucune adresse n'est enregistrée. "
            "Une adresse tapée sans cliquer une proposition n'est pas mémorisée.",
            "Étape 1, adresse du parking"))
    return anomalies


# --------------------------------------------------------------------- notice

# Valeurs du projet reprises telles quelles dans le texte de la notice. Si
# l'une d'elles a changé depuis la rédaction, le texte ment.
_ANCRAGES_NOTICE = [
    ("commune", lambda p: ((p.get("localisation") or {}).get("commune")),
     "la commune actuelle"),
    ("adresse", lambda p: ((p.get("localisation") or {}).get("adresse")),
     "l'adresse actuelle"),
]

# En dessous de cette longueur, le texte n'est pas une notice mais une amorce
# ou une note de travail. On ne peut rien conclure de l'absence d'une adresse
# dans trois mots, et crier au loup ferait ignorer les vraies alertes.
_LONGUEUR_NOTICE_JUGEABLE = 200


def _controler_notice(projet: dict) -> list[dict]:
    """La notice fige une copie des valeurs : détecter qu'elle a divergé.

    La génération ne remplit que les sections vides (choix volontaire : ne
    jamais écraser un texte relu par le BE). Conséquence : après correction de
    l'adresse, la notice garde l'ancienne. On ne peut pas la réécrire d'office
    sans détruire le travail de relecture, donc on signale.
    """
    sections = (projet.get("notice") or {}).get("sections") or {}
    texte = " ".join((v or "") for v in sections.values()).strip()
    if len(texte) < _LONGUEUR_NOTICE_JUGEABLE:
        return []

    texte_norm = _norm(texte)
    anomalies = []
    for code, extraire, libelle in _ANCRAGES_NOTICE:
        valeur = (extraire(projet) or "").strip()
        if not valeur or len(valeur) < 3:
            continue
        if _norm(valeur) not in texte_norm:
            anomalies.append(_anomalie(
                f"notice_perimee_{code}", BLOQUANTE,
                f"La notice ne mentionne pas {libelle} du projet "
                f"(« {valeur} »). Elle a été rédigée avec d'autres valeurs et "
                "n'est pas resynchronisée automatiquement, pour ne pas écraser "
                "une relecture. Régénérez-la, ou corrigez le texte à la main.",
                "Étape 4, notice descriptive"))
    return anomalies


# -------------------------------------------------------------------- pièces

def _controler_fichiers(projet: dict) -> list[dict]:
    """Une pièce déclarée dont le fichier a disparu du disque.

    Constaté sur un projet réel : la checklist affichait « 11/11 pièces
    prêtes » alors que le fichier de la DP6 n'existait plus. Le dossier se
    déclarait complet en étant incomplet.
    """
    anomalies = []
    for code, doc in sorted((projet.get("documents") or {}).items()):
        rel = (doc or {}).get("fichier")
        if not rel:
            continue
        if not (config.PROJETS_DIR / rel).exists():
            nom = (doc or {}).get("nom_fichier") or rel
            anomalies.append(_anomalie(
                f"fichier_manquant_{code}", BLOQUANTE,
                f"La pièce {code.upper()} est enregistrée sous le nom « {nom} » "
                "mais le fichier est introuvable sur le disque. Elle est comptée "
                "comme fournie alors qu'elle ne sortira pas au dossier.",
                "Étape 2, pièces du bureau d'études"))
    return anomalies


# ------------------------------------------------------------------ ombrière

def _controler_ombriere(projet: dict) -> list[dict]:
    omb = projet.get("ombriere") or {}
    anomalies = []
    if not omb.get("famille"):
        anomalies.append(_anomalie(
            "type_ombriere_absent", ATTENTION,
            "Aucun type d'ombrière n'est choisi. Les cotes du catalogue ne "
            "seront pas affirmées dans le Cerfa ni dans la notice.",
            "Étape 3, type d'ombrière"))
    if not omb.get("puissance_kwc"):
        anomalies.append(_anomalie(
            "puissance_absente", SERIEUSE,
            "La puissance n'est pas renseignée : elle détermine le régime "
            "d'urbanisme (déclaration préalable en dessous de 3 MWc).",
            "Étape 3, puissance"))
    return anomalies


# --------------------------------------------------------------------- entrée

CONTROLES = (
    _controler_parcelles,
    _controler_adresse,
    _controler_notice,
    _controler_fichiers,
    _controler_ombriere,
)


def controler(projet: dict) -> list[dict]:
    """Toutes les anomalies du dossier, les plus graves d'abord.

    `projet` est le dictionnaire du projet (model_dump()), pas le modèle.
    """
    anomalies: list[dict] = []
    for controle in CONTROLES:
        anomalies.extend(controle(projet))
    anomalies.sort(key=lambda a: (_ORDRE[a["gravite"]], a["code"]))
    return anomalies


def bloquantes(projet: dict) -> list[dict]:
    """Les seules anomalies qui rendraient le dossier faux en mairie."""
    return [a for a in controler(projet) if a["gravite"] == BLOQUANTE]
