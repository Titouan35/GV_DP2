"""Composition des fonds de carte WMS.

Bug constaté le 01/09/2026 sur le plan cadastral réel : la planche sortait en
APLAT ORANGE OPAQUE, illisible, sans parcelle repérable.

Cause : le Parcellaire Express de la Géoplateforme est une SURCOUCHE
semi-transparente. Son remplissage de parcelle est un orange à 20 % d'opacité
(255, 130, 0, 51), destiné à être composité sur un fond. `convert("RGB")`
jetait le canal alpha, ce qui transformait ce voile léger en aplat plein
recouvrant toute la planche.

Ces tests n'appellent pas le réseau : la réponse WMS est simulée.
"""
from __future__ import annotations

import io

import httpx
import pytest
from PIL import Image

from app.planches import cartes

# La couleur exacte renvoyée par la Géoplateforme pour un intérieur de parcelle.
ORANGE_PARCELLAIRE = (255, 130, 0, 51)


def _reponse_wms(image: Image.Image) -> httpx.Response:
    buf = io.BytesIO()
    image.save(buf, "PNG")
    return httpx.Response(200, content=buf.getvalue(),
                          headers={"content-type": "image/png"})


@pytest.fixture()
def wms(monkeypatch):
    """Remplace l'appel réseau par une image fournie par le test."""
    etat = {"image": None, "params": None}

    def faux_get(self, url, params=None, **kw):
        etat["params"] = params
        return _reponse_wms(etat["image"])

    monkeypatch.setattr(httpx.Client, "get", faux_get)
    return etat


def test_la_surcouche_cadastrale_ne_devient_pas_un_aplat_opaque(wms):
    """LE test de ce fichier : le voile orange doit rester un voile.

    Sur blanc, un orange à 20 % d'opacité donne un rose très clair. S'il
    ressort en (255, 130, 0), c'est que l'alpha a été jeté et que la planche
    est illisible.
    """
    # Arrange : une tuile entièrement remplie du voile de parcelle
    wms["image"] = Image.new("RGBA", (64, 64), ORANGE_PARCELLAIRE)

    # Act
    img = cartes._getmap("CADASTRALPARCELS.PARCELLAIRE_EXPRESS",
                         (0, 0, 100, 100), 64, 64)

    # Assert
    pixel = img.getpixel((32, 32))
    assert pixel != ORANGE_PARCELLAIRE[:3], "l'alpha a été jeté : aplat orange"
    assert all(c > 200 for c in pixel), f"fond trop sombre pour un voile à 20 % : {pixel}"


def test_la_transparence_est_demandee_au_serveur(wms):
    """Sans TRANSPARENT, certains serveurs remplissent eux-mêmes le hors-couche."""
    wms["image"] = Image.new("RGBA", (32, 32), (255, 255, 255, 255))
    cartes._getmap("UNE.COUCHE", (0, 0, 100, 100), 32, 32)
    assert wms["params"]["TRANSPARENT"] == "TRUE"


def test_une_couche_opaque_n_est_pas_alteree(wms):
    """Ortho et plan IGN ont un alpha à 255 partout : la composition doit être
    sans effet, sinon on aurait délavé toutes les photos aériennes."""
    # Arrange
    couleur = (34, 78, 120)
    wms["image"] = Image.new("RGBA", (32, 32), couleur + (255,))

    # Act
    img = cartes._getmap("ORTHOIMAGERY.ORTHOPHOTOS", (0, 0, 100, 100), 32, 32)

    # Assert
    assert img.getpixel((16, 16)) == couleur


def test_le_resultat_est_en_rvb_sans_canal_alpha(wms):
    """Le reste de la chaîne (collage, JPEG) attend du RVB."""
    wms["image"] = Image.new("RGBA", (32, 32), ORANGE_PARCELLAIRE)
    assert cartes._getmap("X", (0, 0, 100, 100), 32, 32).mode == "RGB"


def test_une_tuile_semi_transparente_laisse_voir_le_blanc_dessous(wms):
    """Vérifie la composition elle-même, pas seulement l'absence d'aplat :
    un pixel totalement transparent doit ressortir blanc."""
    # Arrange : moitié gauche opaque rouge, moitié droite transparente
    image = Image.new("RGBA", (32, 32), (255, 0, 0, 255))
    for x in range(16, 32):
        for y in range(32):
            image.putpixel((x, y), (0, 0, 0, 0))
    wms["image"] = image

    # Act
    img = cartes._getmap("X", (0, 0, 100, 100), 32, 32)

    # Assert
    assert img.getpixel((8, 16)) == (255, 0, 0)
    assert img.getpixel((24, 16)) == (255, 255, 255)
