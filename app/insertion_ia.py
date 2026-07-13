"""Module Insertion IA (phase 5) : couche d'abstraction fournisseur.

La clé API reste côté serveur (variables d'environnement), jamais dans le
navigateur. Fournisseur par défaut : Gemini ; bascule Azure OpenAI possible
sans toucher au reste (plan §6 bis).

Squelette : tant qu'aucune clé n'est configurée, le module expose son
statut et le guide de configuration ; la génération renvoie une erreur
propre. Le branchement effectif se fera quand Florent aura créé la clé.
"""
from __future__ import annotations

import os

FOURNISSEURS = ("gemini", "azure-openai")


def statut() -> dict:
    """État de configuration du module (jamais la clé elle-même)."""
    fournisseur = os.environ.get("GVDP_IMAGE_PROVIDER", "gemini").lower()
    cles = {
        "gemini": bool(os.environ.get("GEMINI_API_KEY")),
        "azure-openai": bool(
            os.environ.get("AZURE_OPENAI_API_KEY")
            and os.environ.get("AZURE_OPENAI_ENDPOINT")
        ),
    }
    configure = cles.get(fournisseur, False)
    return {
        "fournisseur": fournisseur,
        "configure": configure,
        "cles_detectees": cles,
        "guide": [
            "1. Créer une clé API Gemini sur aistudio.google.com (ou une ressource Azure OpenAI via l'IT).",
            "2. Définir la variable d'environnement GEMINI_API_KEY sur ce poste (ou AZURE_OPENAI_API_KEY + AZURE_OPENAI_ENDPOINT).",
            "3. Optionnel : GVDP_IMAGE_PROVIDER=azure-openai pour basculer de fournisseur.",
            "4. Relancer GV_DP : l'étape Insertion IA s'activera automatiquement.",
        ],
        "rappels": [
            "Visuels réservés au commercial, étiquetés « visuel IA » ; jamais en pièce DP6.",
            "Coût estimé 0,05 à 0,15 € par image, 10 à 30 s par génération.",
        ],
    }


def generer(projet: dict, consignes: str) -> dict:
    """Génération d'insertion (non disponible tant que la clé n'existe pas)."""
    etat = statut()
    if not etat["configure"]:
        raise RuntimeError(
            "Module Insertion IA non configuré : aucune clé API détectée. "
            "Suivez le guide de l'étape 5."
        )
    raise NotImplementedError(
        "Branchement du fournisseur d'images prévu en phase 5 (après création de la clé)."
    )
