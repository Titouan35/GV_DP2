"""Modèle de données du dossier DP (cf. PLAN_OUTIL_DP.md §7).

Tout est optionnel sauf le nom : le projet se remplit étape par étape
dans le wizard, et se sauvegarde en JSON dans PROJETS/ (gitignoré).
"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field

from . import config


class MaitreOuvrage(BaseModel):
    """Maître d'ouvrage du dossier — Greenvolt Next France par défaut.

    Les valeurs viennent de config.MAITRE_OUVRAGE_DEFAUT : elles sont les
    mêmes pour tous les dossiers de l'outil, et les ressaisir à la main dans
    chaque projet était un risque de faute dans un formulaire officiel. Les
    champs restent modifiables pour un dossier monté pour un tiers.
    """
    type: Optional[str] = config.MAITRE_OUVRAGE_DEFAUT["type"]  # société / collectivité / particulier
    raison_sociale: Optional[str] = config.MAITRE_OUVRAGE_DEFAUT["raison_sociale"]
    representant: Optional[str] = config.MAITRE_OUVRAGE_DEFAUT["representant"]
    siret: Optional[str] = config.MAITRE_OUVRAGE_DEFAUT["siret"]
    adresse: Optional[str] = config.MAITRE_OUVRAGE_DEFAUT["adresse"]
    email: Optional[str] = config.MAITRE_OUVRAGE_DEFAUT["email"]
    telephone: Optional[str] = config.MAITRE_OUVRAGE_DEFAUT["telephone"]

    def completer_defauts(self) -> "MaitreOuvrage":
        """Comble les champs vides avec les valeurs Greenvolt, en place.

        Les défauts ci-dessus ne servent qu'à la CRÉATION : un projet
        antérieur au 16/09/2026 a été enregistré avec des `null` explicites,
        qui écrasent les défauts à la relecture du JSON. Appelé au chargement
        (api/routes_projets.py), jamais à la sauvegarde — sans quoi une valeur
        effacée à l'écran se verrait réécrire sous les doigts du rédacteur.
        """
        for champ, valeur in config.MAITRE_OUVRAGE_DEFAUT.items():
            if not str(getattr(self, champ) or "").strip():
                setattr(self, champ, valeur)
        return self


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


class Batiment(BaseModel):
    """Bâtiment existant sur les parcelles (app/geo/batiments.py).

    `inclus` : le rédacteur peut écarter un bâtiment (auvent, local
    technique, erreur de la source) ; seuls les inclus font l'emprise bâtie.
    """
    id: Optional[str] = None
    source: Optional[str] = None
    nature: Optional[str] = None
    usage: Optional[str] = None
    hauteur_m: Optional[float] = None
    surface_m2: Optional[float] = None            # bâtiment entier
    surface_parcelles_m2: Optional[float] = None  # part sur les parcelles
    a_cheval: bool = False
    inclus: bool = True
    geometry: Optional[dict] = None  # GeoJSON WGS84


class Localisation(BaseModel):
    adresse: Optional[str] = None
    code_postal: Optional[str] = None
    commune: Optional[str] = None
    code_insee: Optional[str] = None
    departement: Optional[str] = None
    lon: Optional[float] = None
    lat: Optional[float] = None
    parcelles: list[Parcelle] = Field(default_factory=list)
    batiments: list[Batiment] = Field(default_factory=list)
    batiments_source: Optional[str] = None  # « BD TOPO (IGN) » / « Cadastre (PCI) »

    @property
    def emprise_batie_m2(self) -> Optional[int]:
        """Emprise des bâtiments existants retenus, sur les parcelles (m²)."""
        if not self.batiments_source:
            return None
        return round(sum(b.surface_parcelles_m2 or 0 for b in self.batiments if b.inclus))

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
    # Nombre de modules sur le rampant (« Nombre de panneaux en largeur » sur
    # la FVE). Avec le point bas, il désigne la coupe type du catalogue à
    # déposer en DP3 (app/coupes_dp3.py).
    nb_modules_rampant: Optional[int] = None
    # Nombre TOTAL de modules de l'installation (« Nombre » du bloc Modules de
    # la FVE). Sert à estimer l'emprise au sol créée du Cerfa, à ne pas
    # confondre avec nb_modules_rampant, qui n'en compte qu'une rangée.
    nb_modules_total: Optional[int] = None
    type_module: Optional[str] = None            # legacy (remplacé par puissance + dimensions)
    puissance_kwc: Optional[float] = None
    nb_places: Optional[int] = None
    # Emprise au sol, cadre 4.3 du Cerfa : « la projection verticale du volume
    # de la construction, tout débords et surplomb inclus » (art. R.420-1). Le
    # formulaire en demande trois, et le Cerfa partait sans aucune (relevé le
    # 16/09/2026). L'outil ne les DEDUIT pas : une ombrière peut être en
    # plusieurs rangées, et le parking peut déjà porter des constructions.
    # L'étape 3 affiche la surface longueur x largeur comme simple repère.
    emprise_creee_m2: Optional[float] = None
    emprise_avant_m2: Optional[float] = None
    emprise_supprimee_m2: Optional[float] = None
    # Puissance de raccordement (cadre 4.2.1, si > 12 kVA mono / 36 kVA tri).
    # Distincte de la puissance crête : c'est la puissance de soutirage du
    # projet, que seule l'étude électrique donne.
    puissance_raccordement_kva: Optional[float] = None
    # Destination de l'électricité produite. Détermine le texte de la section
    # « Accès, réseaux et raccordement » de la notice, et remplit la case
    # « destination principale de l'énergie produite » du Cerfa (cadre 4.2.1).
    # Le texte de la notice affirmait l'injection réseau pour TOUS les projets,
    # y compris en autoconsommation totale (signalé par Florent le 01/09/2026).
    # Valeurs : autoconsommation_totale | autoconsommation_surplus | vente_totale
    destination_energie: Optional[str] = None
    # Notice de sécurité ERP, §4.1 (app/notices_erp.py, 28/10/2026). Le modèle
    # et le nombre d'onduleurs sont lus sur la FVE (bloc « Onduleurs ») ; le
    # modèle désigne aussi la fiche technique Sungrow mise en annexe 7.4.
    nb_ombrieres: Optional[int] = None
    nb_onduleurs: Optional[int] = None
    modele_onduleur: Optional[str] = None


class Urbanisme(BaseModel):
    """Contexte réglementaire dérivé des APIs (GPU, Géorisques)."""
    zonage: Optional[dict] = None      # réponse zonage_plu
    servitudes: Optional[dict] = None  # réponse servitudes_abf
    rnu: Optional[dict] = None
    risques: Optional[dict] = None     # réponse risques_commune
    secteur_abf: Optional[bool] = None  # confirmable à la main dans l'UI


class Erp(BaseModel):
    """Classement de l'établissement recevant du public desservi par le parking.

    Le type est M (magasin de vente) sur tous nos sites ; la catégorie (1 à 5)
    dépend de l'effectif, donnée de l'exploitant, cochée à l'étape 5 (cases
    M1 à M5). Alimente les notices accessibilité et sécurité.
    """
    type: str = "M"
    categorie: Optional[int] = None
    # Case « Type M » de l'étape 5 (demande du 12/11/2026), exclusive avec
    # M1 à M5 : les deux notices indiquent alors « Type M » seul, sans catégorie.
    type_m_seul: bool = False


class Notice(BaseModel):
    sections: dict[str, str] = Field(default_factory=dict)  # 8 sections (app/notice.py:SECTIONS)
    # Point 7 « Gestion des eaux pluviales » : optionnel (demande du
    # 12/11/2026), décoché par défaut. Décoché, la section n'est ni affichée
    # ni exportée, et « Chantier » devient le point 7. Le texte reste stocké
    # dans `sections` pour réapparaître tel quel si on recoche.
    eaux_pluviales: bool = False
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
    erp: Erp = Field(default_factory=Erp)
    # Uploads BE : {code_piece: {nom_fichier, chemin, date}}
    documents: dict[str, Any] = Field(default_factory=dict)
    meta: dict[str, Any] = Field(default_factory=dict)
