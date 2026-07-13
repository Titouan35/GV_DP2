"""Générateurs de planches : cartes DP1 + dessins paramétriques DP3/DP4."""
from . import cartes, ombriere

GENERATEURS = {
    "dp1_situation": cartes.planche_situation,
    "dp1_cadastral": cartes.planche_cadastrale,
    "dp1_aerien": cartes.planche_aerienne,
    "dp3_coupe": ombriere.dessiner_coupe,
    "dp4_facades": ombriere.dessiner_facades,
}
