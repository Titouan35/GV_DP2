"""Ouvrir une image que PIL refuse, sans faire tomber l'assemblage.

Pourquoi ce module (16/09/2026). L'assemblage rendait une erreur 500 sur un
projet réel : `OSError: [Errno 22] Invalid argument` levé dès `Image.open`
d'une photo DP8, avant même d'avoir touché aux pixels.

CAUSE RÉELLE, établie après coup : PIL importe `TiffImagePlugin` PARESSEUSEMENT
pour décoder le bloc EXIF d'un JPEG. Le dépôt vit dans OneDrive, dont le
fournisseur de fichiers cloud refusait ce jour-là de servir certains fichiers
du runtime — le même `Errno 22` frappait aussi pytest et un codec de la
bibliothèque standard. La photo n'y était pour rien : les mêmes octets, relus
plus tard, s'ouvrent parfaitement.

D'où deux remèdes, dans cet ordre :

1. RÉESSAYER. Une hydratation OneDrive qui échoue réussit souvent au coup
   suivant, et c'est le cas de loin le plus fréquent.
2. À défaut, retirer les segments APPn (EXIF, XMP, vignettes constructeur) et
   garder le flux compressé tel quel, dans une COPIE. Ce détour reste utile :
   il couvre aussi les EXIF réellement malformés, qui existent. Aucun pixel
   n'est touché, aucune recompression n'a lieu.

La copie n'est jamais préférée à l'original : dès que celui-ci redevient
lisible, c'est lui qui sert. Ce qui est perdu dans la copie, c'est
l'orientation EXIF — or c'est justement ce que PIL n'arrivait pas à lire.
"""
from __future__ import annotations

import logging
from pathlib import Path

from PIL import Image

logger = logging.getLogger(__name__)

# APP0 à APP15 : tous les segments de métadonnée d'un JPEG.
_MARQUEURS_APP = frozenset(range(0xE0, 0xF0))
_SOI = b"\xff\xd8"
_DEBUT_FLUX = 0xDA          # Start Of Scan : au-delà, ce sont les pixels


def _sans_segments_app(donnees: bytes) -> bytes:
    """Recopie un JPEG sans ses segments APPn.

    S'arrête au Start Of Scan et recopie tout le reste sans l'analyser : les
    données compressées, et ce qu'un constructeur a pu ajouter derrière (les
    photos Samsung portent une queue « SEFT » après la fin d'image).
    """
    if donnees[:2] != _SOI:
        return donnees
    sortie, i = bytearray(_SOI), 2
    while i < len(donnees) - 3:
        if donnees[i] != 0xFF:
            break                       # flux inattendu : on ne bricole pas
        marqueur = donnees[i + 1]
        if marqueur == _DEBUT_FLUX:
            sortie += donnees[i:]
            return bytes(sortie)
        taille = int.from_bytes(donnees[i + 2:i + 4], "big")
        if taille < 2:
            break                       # taille aberrante : on abandonne
        if marqueur not in _MARQUEURS_APP:
            sortie += donnees[i:i + 2 + taille]
        i += 2 + taille
    return donnees                      # jamais atteint le SOS : on rend l'original


def lisible(chemin: Path, essais: int = 2) -> bool:
    """Vrai si PIL sait ouvrir ce fichier.

    Deux tentatives : la cause la plus fréquente d'échec ici est une
    hydratation OneDrive ratée d'un module PIL chargé paresseusement, et elle
    ne se reproduit en général pas au coup suivant. Réessayer évite de
    dégrader une photo parfaitement saine.
    """
    for reste in range(essais - 1, -1, -1):
        try:
            with Image.open(chemin) as im:
                im.size
            return True
        except (OSError, ValueError):
            if reste:
                logger.info("Relecture de %s après un échec d'ouverture", chemin.name)
    return False


def rendre_lisible(chemin: Path, assets: Path) -> Path:
    """Chemin d'une version que PIL sait ouvrir.

    Rend le chemin d'origine s'il est déjà lisible — le cas courant, et on ne
    touche à rien. Sinon écrit une copie réparée dans les assets et rend
    celle-ci : le fichier déposé par le bureau d'études n'est JAMAIS modifié,
    c'est sa pièce.

    Si la réparation échoue elle aussi, on rend le chemin d'origine : c'est à
    l'appelant de dégrader proprement, pas à ce module de décider.
    """
    chemin = Path(chemin)
    if lisible(chemin):
        return chemin
    try:
        repare = assets / f"repare_{chemin.stem}.jpg"
        if repare.exists() and repare.stat().st_mtime >= chemin.stat().st_mtime:
            return repare if lisible(repare) else chemin
        donnees = _sans_segments_app(chemin.read_bytes())
        repare.parent.mkdir(parents=True, exist_ok=True)
        repare.write_bytes(donnees)
        if lisible(repare):
            logger.warning("Image %s illisible par PIL (métadonnée) : copie "
                           "réparée sans ses segments APPn.", chemin.name)
            return repare
        repare.unlink(missing_ok=True)
    except OSError:
        logger.warning("Réparation impossible pour %s", chemin.name, exc_info=True)
    return chemin
