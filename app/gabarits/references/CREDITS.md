# Photos de référence — provenance et licences

Ces photographies ne sont **pas** des visuels Greenvolt. Elles servent uniquement
de repère de réalisme envoyé au modèle d'image (aspect de l'acier galvanisé,
finesse des profilés, façon dont la lumière accroche la structure). Elles ne
figurent jamais dans un dossier livré.

## Fichiers

| Fichier | Provenance | Licence |
|---------|-----------|---------|
| `ombriere_mono_galva.jpg` | Wikimedia Commons — ombrières PV, Bellerive-sur-Allier (03) | **CC BY-SA 4.0** |
| `ombriere_double_galva.jpg` | Wikimedia Commons — parking photovoltaïque, UAM Madrid | **CC BY-SA 3.0** |

Récupérées en juillet 2026 via `OUTILS/DP/OUTIL_INSERTION/references/`, où elles
avaient été collectées pour l'outil d'insertion 3D archivé. Le nom d'origine de
chaque fichier portait sa licence (`..._ccbysa40.jpg`, `..._ccbysa30.jpg`).

## À compléter avant toute diffusion hors de l'entreprise

**Le nom des auteurs n'a pas pu être confirmé** auprès de Wikimedia Commons : les
fichiers ont été renommés lors de leur import et leur page source d'origine n'a
pas été conservée. Or le CC BY-SA impose de citer l'auteur.

En conséquence, tant que cette attribution n'est pas rétablie :

- ces deux fichiers **ne doivent pas être publiés** (dépôt public, site,
  documentation diffusée) ;
- le dépôt GV_DP doit rester **privé**, ce qui est le cas.

La clause **share-alike** du CC BY-SA impose en outre de partager les œuvres
dérivées sous la même licence. Le statut juridique des images produites par un
modèle génératif à partir d'une référence reste incertain : pour des visuels
commerciaux client, privilégier à terme des photos d'installations Greenvolt,
qui suppriment ces deux contraintes d'un coup.

## Remplacement

Pour substituer de vraies photos d'ombrières Greenvolt, déposer les fichiers
sous les **mêmes noms** dans ce dossier : le catalogue les référence par nom
(`app/catalogue.py`, clé `reference_photo`), aucun code n'est à modifier.
