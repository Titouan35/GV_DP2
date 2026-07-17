"""Générateurs de planches : cartes DP1 + coupe paramétrique DP3."""
from . import cartes, ombriere

GENERATEURS = {
    "dp1_situation": cartes.planche_situation,
    "dp1_cadastral": cartes.planche_cadastrale,
    "dp1_aerien": cartes.planche_aerienne,
    "dp3_coupe": ombriere.dessiner_coupe,
}
