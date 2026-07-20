"""Scaffold : l'ombrière dessinée en VOLUME PLEIN sur la photo du site.

Décision du 20/07/2026, après quatre jours de réglages de prompt : décrire une
géométrie avec des mots à un modèle génératif ne donne pas un résultat
reproductible (« deux versants » -> profil en Y, « dessine un T » -> papillon,
structure de 3 places au lieu de 40 m...). On inverse la charge : l'outil pose
lui-même la structure au bon endroit, à la bonne taille et à la bonne forme,
et Gemini ne fait plus que l'HABILLAGE photoréaliste (matière, lumière,
ombres) — ce qu'il fait bien.

C'est le principe du scaffold v5 (archivé), abandonné le 18/07 parce qu'il
réclamait deux tracés par ombrière. Il redevient gratuit : depuis la
perspective calibrée, le volume se déduit du seul cliqué-glissé du bord avant.

Différence majeure avec l'archive : la géométrie n'est plus approximative
(extrusion parallèle à échelle constante) mais vient de
`perspective.volume_ombriere`, qui reprojette les huit coins réels.

Vu du parking, on regarde l'ombrière PAR EN DESSOUS : c'est la sous-face et
sa charpente qu'on dessine, pas les panneaux (invisibles depuis le sol).
"""
from __future__ import annotations

import math

from PIL import Image, ImageDraw, ImageFilter

# Teintes d'une charpente acier vue de dessous, à contre-jour du ciel : nettement
# plus sombres que le bitume, sinon le volume se confond avec le sol et le
# modèle ne le distingue pas (constaté au premier essai, 20/07/2026).
SOUS_FACE = (44, 48, 54)
PANNE = (72, 78, 86)
POUTRE = (58, 62, 70)
POTEAU = (74, 79, 86)
POTEAU_ARETE = (38, 41, 46)
FASCIA = (104, 110, 118)       # tranche de la dalle, accroche la lumière
FASCIA_ARETE = (36, 39, 44)
MODULE = (26, 28, 34)          # face supérieure (vue plongeante seulement)
MODULE_JOINT = (48, 52, 60)

# épaisseur de la dalle (platelage + pannes) rapportée à la hauteur libre :
# ~0,35 m sous 3,5 m. C'est elle qu'on voit surtout quand l'ombrière est loin
# et vue presque de face, la sous-face étant alors très rasante.
RATIO_DALLE = 0.10


def _bilin(quad, u: float, v: float):
    """Point du quadrilatère à la position (u le long du bord, v en profondeur).

    `quad` = [avant_gauche, avant_droit, fond_droit, fond_gauche] : c'est
    l'ordre que produit `perspective.volume_ombriere`.
    """
    av = (quad[0][0] + (quad[1][0] - quad[0][0]) * u,
          quad[0][1] + (quad[1][1] - quad[0][1]) * u)
    ar = (quad[3][0] + (quad[2][0] - quad[3][0]) * u,
          quad[3][1] + (quad[2][1] - quad[3][1]) * u)
    return (av[0] + (ar[0] - av[0]) * v, av[1] + (ar[1] - av[1]) * v)


def _longueur_px(quad) -> float:
    return math.hypot(quad[1][0] - quad[0][0], quad[1][1] - quad[0][1])


def _ombre_au_sol(dr, sol, decal: float):
    """Ombre portée douce, décalée depuis l'emprise (soleil haut, sans azimut
    connu : on décale légèrement vers le spectateur, comme à la mi-journée)."""
    dr.polygon([(x, y + decal) for x, y in sol], fill=(0, 0, 0, 70))


def _poteaux(dr, sol, toit, nb: int, v_pos: float, largeur: float):
    """File de poteaux verticaux à la profondeur `v_pos` (0 = bord avant,
    1 = fond, 0,5 = axe médian pour une Double).

    Chaque poteau relie le sol à la toiture AU MÊME point (u, v) : la hauteur
    apparente vient donc de la perspective, aucune conversion mètre/pixel.
    """
    for k in range(nb + 1):
        u = k / nb if nb else 0.5
        pied = _bilin(sol, u, v_pos)
        tete = _bilin(toit, u, v_pos)
        # les poteaux lointains sont plus fins : on module par la hauteur vue
        h = max(1.0, abs(pied[1] - tete[1]))
        w = max(2.0, largeur * h / max(1.0, abs(sol[0][1] - toit[0][1])))
        dr.polygon([(pied[0] - w, pied[1]), (pied[0] + w, pied[1]),
                    (tete[0] + w * 0.8, tete[1]), (tete[0] - w * 0.8, tete[1])],
                   fill=POTEAU, outline=POTEAU_ARETE)


def _sous_face(dr, toit, longueur_m: float, profondeur_m: float, ep_trait: int):
    """Dessous de la toiture : surface pleine + trame de pannes et de poutres.

    C'est ce que voit un piéton. Les pannes courent dans le sens de la
    longueur, les poutres dans celui de la profondeur (au droit des poteaux).
    """
    dr.polygon(toit, fill=SOUS_FACE)
    n_pannes = max(2, min(14, round((profondeur_m or 5) / 1.7)))
    n_poutres = max(2, min(30, round((longueur_m or 20) / 6)))
    for j in range(1, n_pannes):
        v = j / n_pannes
        dr.line([_bilin(toit, 0.0, v), _bilin(toit, 1.0, v)],
                fill=PANNE, width=ep_trait)
    for i in range(1, n_poutres):
        u = i / n_poutres
        dr.line([_bilin(toit, u, 0.0), _bilin(toit, u, 1.0)],
                fill=POUTRE, width=ep_trait)


def _face_superieure(dr, toit, longueur_m: float, profondeur_m: float, ep_trait: int):
    """Champ de modules (uniquement en vue plongeante : drone, étage)."""
    dr.polygon(toit, fill=MODULE)
    ncol = max(4, min(40, round((longueur_m or 20) / 2.3)))
    nrow = max(2, min(20, round((profondeur_m or 5) / 1.7)))
    for i in range(1, ncol):
        u = i / ncol
        dr.line([_bilin(toit, u, 0.0), _bilin(toit, u, 1.0)],
                fill=MODULE_JOINT, width=ep_trait)
    for j in range(1, nrow):
        v = j / nrow
        dr.line([_bilin(toit, 0.0, v), _bilin(toit, 1.0, v)],
                fill=MODULE_JOINT, width=ep_trait)


def poser(image: Image.Image, volumes: list[dict | None],
          infos: list[dict]) -> Image.Image:
    """Dessine chaque ombrière en volume plein sur une copie de la photo.

    `volumes` : sortie de `volumes_poses` (sol + toit en px, None si non
    calculable). `infos` : un dict par ombrière avec `v_poteaux` (0/0,5/1),
    `longueur_m`, `profondeur_m` et `vue_dessus` (bool).
    """
    base = image.convert("RGBA")
    W, H = base.size
    ombres = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    calque = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    dr_ombre = ImageDraw.Draw(ombres)
    dr = ImageDraw.Draw(calque)
    pose = False

    for vol, info in zip(volumes, infos):
        if not vol:
            continue
        pose = True
        sol, toit = [tuple(p) for p in vol["sol"]], [tuple(p) for p in vol["toit"]]
        L = _longueur_px(sol)
        ep_trait = max(1, round(L / 400))
        _ombre_au_sol(dr_ombre, sol, decal=max(4.0, 0.012 * H))
        if info.get("vue_dessus"):
            _face_superieure(dr, toit, info.get("longueur_m"),
                             info.get("profondeur_m"), ep_trait)
        else:
            _sous_face(dr, toit, info.get("longueur_m"),
                       info.get("profondeur_m"), ep_trait)
        # TRANCHE de la dalle sur le bord avant : c'est elle qui donne son
        # épaisseur à l'ouvrage. Proportionnelle à la hauteur libre apparente,
        # donc juste quelle que soit la distance (une valeur en pixels fixe
        # rendait la toiture infiniment mince sur les vues lointaines).
        h_libre = abs(_bilin(sol, 0.5, 0.0)[1] - _bilin(toit, 0.5, 0.0)[1])
        ep_dalle = max(2.0, RATIO_DALLE * h_libre)
        dr.polygon([toit[0], toit[1],
                    (toit[1][0], toit[1][1] + ep_dalle),
                    (toit[0][0], toit[0][1] + ep_dalle)],
                   fill=FASCIA, outline=FASCIA_ARETE)
        _poteaux(dr, sol, toit,
                 nb=max(1, round((info.get("longueur_m") or 20) / 6)),
                 v_pos=info.get("v_poteaux", 0.5),
                 largeur=max(3.0, L / 90))

    if not pose:
        return image
    ombres = ombres.filter(ImageFilter.GaussianBlur(max(2, round(W / 400))))
    return Image.alpha_composite(Image.alpha_composite(base, ombres),
                                 calque).convert("RGB")
