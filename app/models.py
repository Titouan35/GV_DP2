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


class Insertion(BaseModel):
    """Insertion IA — génération directe via l'API Gemini (flux 17/07/2026).

    Les insertions sélectionnées (`dans_dossier`) entrent au dossier DP en
    planches « visuel d'illustration » ; le photomontage DP6 du BE, s'il est
    fourni, garde la priorité sur la planche avant/après.
    """
    photo: Optional[str] = None            # photo du site active (base de génération)
    photos: list[str] = Field(default_factory=list)  # photos du site déposées (multi)
    consignes: str = ""                    # consignes libres pour le prompt
    affinage: str = ""                     # dernières corrections demandées
    echelle_desc: str = ""                 # ce que représente le repère d'échelle (ex. « largeur d'une place »)
    echelle_distance_m: Optional[float] = None  # distance réelle du repère (m), reportée au prompt
    prompt: Optional[str] = None           # dernier prompt envoyé (traçabilité)
    images: list[dict] = Field(default_factory=list)  # images générées {fichier, date, etiquette, modele}
    retenue: Optional[str] = None          # image retenue (fiche d'emprise)
    dans_dossier: list[str] = Field(default_factory=list)  # images incluses au dossier DP
    nb_images_generees: int = 0            # compteur de dépense locale (projet)
    # repères tracés sur les photos, par chemin de photo (coordonnées 0-1) :
    # {photo: {"segments": [[[x,y] début, [x,y] fin], ...],  # 1 segment = 1 ombrière
    #          "calibrage": {"a": [x,y], "b": [x,y], "distance_m": f, "libelle": s}}}
    guides: dict[str, Any] = Field(default_factory=dict)
    # emprise sur la vue aérienne (crop du plan), coordonnées 0-1 du crop :
    # {"emprises": [[[x,y] x4], ...], "auto": bool} — vide = auto (extraction plan)
    aerienne: dict[str, Any] = Field(default_factory=dict)


class Projet(BaseModel):
    id: Optional[str] = None
    nom: str
    regime: str = "DP"  # recalculé par le moteur réglementaire
    statut: str = "brouillon"
    date_creation: Optional[str] = None
    date_modification: Optional[str] = None
    mo: MaitreOuvrage = Field(default_factory=MaitreOuvrage)
    localisation: Localisation = Field(default_factory=Localisation)
    ombriere: Ombriere = Field(default_factory=Ombriere)
    urbanisme: Urbanisme = Field(default_factory=Urbanisme)
    notice: Notice = Field(default_factory=Notice)
    insertion: Insertion = Field(default_factory=Insertion)
    # Uploads BE : {code_piece: {nom_fichier, chemin, date}}
    documents: dict[str, Any] = Field(default_factory=dict)
    meta: dict[str, Any] = Field(default_factory=dict)
