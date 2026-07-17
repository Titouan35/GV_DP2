"""Générateurs de planches : cartes DP1 (la coupe DP3 est fournie par le BE)."""
from . import cartes

GENERATEURS = {
    "dp1_situation": cartes.planche_situation,
    "dp1_cadastral": cartes.planche_cadastrale,
    "dp1_aerien": cartes.planche_aerienne,
}
