"""Modèle de données du dossier DP (cf. PLAN_OUTIL_DP.md §7).

Tout est optionnel sauf le nom : le projet se remplit étape par étape
dans le wizard, et se sauvegarde en JSON dans PROJETS/ (gitignoré).
"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class MaitreOuvrage(BaseModel):
    type: Optional[str] = None  # société / collectivité / particulier
    raison_sociale: Optional[str] = None
    representant: Optional[str] = None
    siret: Optional[str] = None
    adresse: Optional[str] = None
    email: Optional[str] = None
    telephone: Optional[str] = None


class Parcelle(BaseModel):
    idu: Optional[str] = None
    section: Optional[str] = None
    numero: Optional[str] = None
    com_abs: str = "000"
    contenance_m2: Optional[int] = None
    commune: Optional[str] = None
    code_insee: Optional[str] = None
    geometry: Optional[dict] = None  # GeoJSON WGS84
    source: str = "suggestion"  # suggestion / manuelle


class Localisation(BaseModel):
    adresse: Optional[str] = None
    code_postal: Optional[str] = None
    commune: Optional[str] = None
    code_insee: Optional[str] = None
    departement: Optional[str] = None
    lon: Optional[float] = None
    lat: Optional[float] = None
    parcelles: list[Parcelle] = Field(default_factory=list)

    @property
    def surface_terrain_m2(self) -> Optional[int]:
        surfaces = [p.contenance_m2 for p in self.parcelles if p.contenance_m2]
        return sum(surfaces) if surfaces else None


class Ombriere(BaseModel):
    famille: Optional[str] = None  # coupe (clé catalogue : START PLAINE Bas/Haut/Double ; libellé Mono Bas/Mono Haut/Double)
    longueur_m: Optional[float] = None
    largeur_m: Optional[float] = None
    orientation: Optional[float] = None  # azimut en degrés (dérivé du plan de masse, modifiable)
    nb_travees: Optional[int] = None
    entraxe_m: Optional[float] = None
    pente_deg: Optional[float] = None
    garde_au_sol_m: Optional[float] = None      # hauteur bas de pente (UI)
    hauteur_hors_tout_m: Optional[float] = None  # hauteur maximale (UI)
    module_puissance_wc: Optional[float] = None  # puissance unitaire d'un module
    module_dimensions: Optional[str] = None      # ex. "1762 x 1134 mm"
    type_module: Optional[str] = None            # legacy (remplacé par puissance + dimensions)
    puissance_kwc: Optional[float] = None
    nb_places: Optional[int] = None
    # calibrage de l'outil de mesure sur le plan de masse (px/m)
    echelle_plan_px_par_m: Optional[float] = None


class Urbanisme(BaseModel):
    """Contexte réglementaire dérivé des APIs (GPU, Géorisques)."""
    zonage: Optional[dict] = None      # réponse zonage_plu
    servitudes: Optional[dict] = None  # réponse servitudes_abf
    rnu: Optional[dict] = None
    risques: Optional[dict] = None     # réponse risques_commune
    secteur_abf: Optional[bool] = None  # confirmable à la main dans l'UI


class Notice(BaseModel):
    sections: dict[str, str] = Field(default_factory=dict)  # 7 sections
    genere_par_ia: bool = False
    valide_humain: bool = False
    # Valeurs du projet au moment de la rédaction (app/notice.py:CLES_ANCRAGE).
    # Permet de dire si le texte a divergé des données, sans le deviner par
    # recherche de chaîne. Vide pour les notices écrites avant le 01/09/2026.
    valeurs: dict[str, str] = Field(default_factory=dict)


class Projet(BaseModel):
    id: Optional[str] = None
    nom: str
    regime: str = "DP"  # recalculé par le moteur réglementaire
    statut: str = "brouillon"
    date_creation: Optional[str] = None
    date_modification: Optional[str] = None
    modifie_par: Optional[str] = None  # auteur de la dernière sauvegarde (informatif)
    mo: MaitreOuvrage = Field(default_factory=MaitreOuvrage)
    localisation: Localisation = Field(default_factory=Localisation)
    ombriere: Ombriere = Field(default_factory=Ombriere)
    urbanisme: Urbanisme = Field(default_factory=Urbanisme)
    notice: Notice = Field(default_factory=Notice)
    # Uploads BE : {code_piece: {nom_fichier, chemin, date}}
    documents: dict[str, Any] = Field(default_factory=dict)
    meta: dict[str, Any] = Field(default_factory=dict)
