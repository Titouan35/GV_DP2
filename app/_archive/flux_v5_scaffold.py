"""ARCHIVE (19/07/2026) — flux v5 : scaffold / axes / vue aérienne.

Remplacé par le flux « un geste » (pose) le 17-18/07/2026. Conservé pour
référence (géométrie du scaffold, homographie, analyse aérienne) ; ce
fichier n'est PAS importé ni maintenu. Les tests associés ont été retirés.
"""
# flake8: noqa

DESCRIPTIONS_COUPE = {
    "START PLAINE Bas": "monopente, poteau unique côté haut du versant",
    "START PLAINE Haut": "monopente, poteau unique côté bas du versant",
    "START PLAINE Double": "poteau central unique, toiture en T à pente continue",
}


def construire_prompt(projet: dict, affinage: str = "",
                      plan_infos: dict | None = None,
                      guides: dict | None = None,
                      idx: dict | None = None,
                      coupe_be: bool = False,
                      mode: str = "libre",
                      plan_ref: bool = False) -> str:
    """Prompt Gemini v6 (17/07/2026). Trois modes selon la base envoyee :

    - "scaffold" : la photo contient deja l'ombrière posee en VOLUME GRIS a la
      bonne position. Consigne = habiller ce volume, sans le deplacer ni le
      redimensionner. C'est le mode fiable (placement garanti par nous).
    - "axes" : la photo porte des axes magenta (repli si pas d'echelle).
    - "libre" : aucune indication, placement au juge.

    Regles Google : verbe fort, images decrites sans numero, formulation
    positive, keep-explicit. La coupe (structure) et le plan de masse (legende
    couleurs) sont joints en reference.
    """
    omb = projet.get("ombriere") or {}
    ins = projet.get("insertion") or {}
    n = len(guides["ombrieres"]) if guides and guides.get("ombrieres") else 0
    mot = "les ombrières" if n > 1 else "l'ombrière"
    mot_de = "des ombrières" if n > 1 else "de l'ombrière"

    if mode == "scaffold":
        tete = (f"Transforme {'les' if n > 1 else 'la'} forme"
                f"{'s' if n > 1 else ''} grise"
                f"{'s' if n > 1 else ''} en volume, déjà présente"
                f"{'s' if n > 1 else ''} sur cette photographie, en "
                f"{'ombrières photovoltaïques de parking' if n > 1 else 'une ombrière photovoltaïque de parking'} "
                "photoréaliste"
                f"{'s' if n > 1 else ''}, comme si elle"
                f"{'s avaient' if n > 1 else ' avait'} toujours été là.")
        blocs = [tete]
        blocs.append(
            "PLACEMENT. " + ("Chaque forme grise" if n > 1 else "La forme grise")
            + " marque l'emplacement, la taille et l'orientation EXACTS "
            + mot_de + " : garde-les rigoureusement identiques, ne déplace pas et "
            "ne redimensionne pas. Remplace simplement le volume gris par la "
            "vraie structure et sa toiture."
        )
    elif mode == "axes":
        blocs = [f"Insère {n if n > 1 else 'une'} ombrière"
                 f"{'s' if n > 1 else ''} photovoltaïque"
                 f"{'s' if n > 1 else ''} de parking dans cette photographie, "
                 "de façon photoréaliste."]
        blocs.append(
            "PLACEMENT. Un trait magenta marque l'axe " + mot_de
            + " : construis " + mot + " le long de chaque trait, centrée sur le "
            "trait et posée au sol.")
    else:
        blocs = ["Insère une ombrière photovoltaïque de parking dans cette "
                 "photographie, de façon photoréaliste."]
        blocs.append("PLACEMENT. Implante l'ombrière sur la zone de "
                     "stationnement la plus dégagée et cohérente de la photo.")

    # cotes reelles
    if plan_infos and plan_infos.get("dims_m"):
        liste = " ; ".join(f"{L:g} m x {l:g} m".replace(".", ",")
                           for L, l in plan_infos["dims_m"])
        blocs[-1] += f" Dimensions réelles au sol : {liste}."

    # structure : la coupe
    if "coupe" in (idx or {}):
        origine = ("la coupe technique du projet, dessinée par le bureau d'études"
                   if coupe_be else "la coupe technique fournie")
        struct = [f"STRUCTURE. Reproduis fidèlement le profil de {origine} : "
                  "mêmes poteaux, même position des poteaux sous la toiture, "
                  "même pente, mêmes proportions. "]
    else:
        struct = ["STRUCTURE. Poteaux en acier galvanisé et toiture inclinée. "]
    h_bas, h_haut = omb.get("garde_au_sol_m"), omb.get("hauteur_hors_tout_m")
    if h_bas and h_haut:
        struct.append(f"Hauteur {_fmt(h_bas)} m au point bas et {_fmt(h_haut)} m "
                      "au point haut. ")
    struct.append("Structure en acier galvanisé gris clair, toiture de modules "
                  "photovoltaïques noirs et mats, sous-face claire.")
    blocs.append("".join(struct))

    # reference implantation : le plan de masse et sa legende couleurs
    if plan_ref:
        blocs.append(
            "REFERENCE. Le plan de masse joint (vue de dessus) confirme "
            "l'implantation : les zones bleues quadrillées sont les panneaux, "
            "les traits rouges la trame des poteaux, les carres gris les "
            "fondations, et les mentions HAUT/BAS DE RAMPANT le sens de descente "
            "de la toiture. Sers-t'en pour l'orientation et les proportions ; "
            "ne le recopie pas dans l'image."
        )

    # integration : positif + keep-explicit + photo
    blocs.append(
        "INTEGRATION. Garde le reste de la scène rigoureusement identique : les "
        "voitures, le revêtement du sol et ses marquages, les bordures, les "
        "arbres hors ombrière, les bâtiments et le ciel restent exactement a "
        "leur place. Reproduis le grand-angle, la lumière du jour et la "
        "direction des ombres de la photo ; ajoute une ombre portée douce sous "
        "chaque ombrière. Les poteaux sont verticaux et posés sur le bitume."
    )

    rendu = ["RENDU. Le résultat est une photographie plein cadre au meme "
             "cadrage que l'originale, montrant le parking avec ses ombrières."]
    libres = (ins.get("consignes") or "").strip()
    corrections = (affinage or ins.get("affinage") or "").strip()
    if libres:
        rendu.append(libres if libres.endswith((".", "!", "?")) else libres + ".")
    if corrections:
        rendu.append(corrections if corrections.endswith((".", "!", "?")) else corrections + ".")
    blocs.append(" ".join(rendu))

    return "\n\n".join(blocs)




# ------------------------------------------------------------------ guides photo

def guides_actifs(projet: dict) -> dict | None:
    """Repères tracés sur la photo ACTIVE : une ombrière = 2 traits.

    Chaque ombrière = {longueur:[A,B] (bord avant / bas de rampant),
    largeur:[C,D] (trait tracé du BAS vers le HAUT de rampant, donne la
    profondeur et le sens de pente), longueur_m, largeur_m}. Plusieurs
    ombrières possibles. None si rien d'exploitable.
    """
    ins = projet.get("insertion") or {}
    photo = ins.get("photo")
    if not photo:
        return None
    g = (ins.get("guides") or {}).get(photo) or {}
    ombrieres = []
    for o in (g.get("ombrieres") or []):
        lo, la = o.get("longueur"), o.get("largeur")
        if lo and la and len(lo) == 2 and len(la) == 2:
            ombrieres.append({
                "longueur": lo, "largeur": la,
                "longueur_m": o.get("longueur_m"), "largeur_m": o.get("largeur_m"),
            })
    if not ombrieres:
        return None
    return {"ombrieres": ombrieres}


MAGENTA = (255, 0, 200)   # trait de LONGUEUR (bord avant)
CYAN = (0, 200, 255)      # trait de LARGEUR (profondeur, bas -> haut de rampant)


def _fleche(dr, a, b, coul, ep):
    dr.line([a, b], fill=coul, width=ep)
    ang = math.atan2(b[1] - a[1], b[0] - a[0])
    t = ep * 3
    for da in (-0.5, 0.5):
        dr.line([b, (b[0] - t * math.cos(ang - da), b[1] - t * math.sin(ang - da))],
                fill=coul, width=ep)


def photo_emprise(projet: dict) -> Path | None:
    """Copie de la photo active avec, par ombrière, les 2 traits tracés.

    Longueur en MAGENTA (bord avant), largeur en CYAN fléchée du bas vers le
    haut de rampant (sens de pente). Aucun texte (déteint sur le rendu).
    """
    from PIL import ImageDraw

    guides = guides_actifs(projet)
    photo = image_kit(projet, "photo")
    if not guides or not photo:
        return None

    image = _ouvrir_image(photo).convert("RGB")
    dr = ImageDraw.Draw(image)
    l, h = image.size
    ep = max(5, round(min(l, h) / 220))

    for o in guides["ombrieres"]:
        la0, lb0 = o["longueur"]
        a = (la0[0] * l, la0[1] * h)
        b = (lb0[0] * l, lb0[1] * h)
        dr.line([a, b], fill=MAGENTA, width=ep)
        for px, py in (a, b):
            dr.ellipse([px - ep * 2, py - ep * 2, px + ep * 2, py + ep * 2], fill=MAGENTA)
        w0, w1 = o["largeur"]
        c = (w0[0] * l, w0[1] * h)
        d = (w1[0] * l, w1[1] * h)
        _fleche(dr, c, d, CYAN, ep)

    sortie = config.assets_dir(projet.get("id")) / "photo_emprise.png"
    sortie.parent.mkdir(parents=True, exist_ok=True)
    image.save(sortie)
    return sortie


# ------------------------------------------------------------------ scaffold 3D

# le placement au pixel est impossible à obtenir du modèle (constaté à
# répétition 17/07/2026) : on POSE nous-mêmes l'ombrière en volume sur la photo,
# à partir des 2 traits tracés (longueur + largeur), et Gemini ne fait plus que
# l'habillage photoréaliste. Échelle = longueur px / longueur réelle du plan ;
# sens de pente = sens du trait de largeur (bas -> haut de rampant).
#
# 17/07/2026 (soir) : le toit n'est plus un aplat gris mais une TEXTURE de
# modules PV sombres (grille de cellules en perspective) + fascia et poteaux
# galvanisés. Un scaffold qui ressemble déjà à l'ombrière finie réduit la
# latitude de Gemini : il n'a plus à « inventer » une ombrière (d'où les
# redimensionnements constatés), seulement à rendre celle-ci photoréaliste.
GRIS_PANNEAU = (24, 26, 32)         # module PV (quasi noir, mat)
GRIS_CELLULE = (46, 50, 58)         # liseré entre cellules / modules
GRIS_POTEAU = (188, 192, 198)       # acier galvanisé clair
GRIS_FASCIA = (150, 154, 160)       # panne/fascia sous le bord avant
COUL_TRAIT_TOIT = (14, 15, 18, 255)


def _geometrie_ombrieres(projet: dict, W: int, H: int):
    """Génère la géométrie 3D de chaque ombrière tracée, en pixels image.

    Source unique pour le dessin du scaffold ET le contrôle géométrique
    post-génération. Pour chaque ombrière tracée renvoie un dict :
      a, b       : pieds du bord avant (bas de rampant), file gauche->droite
      vx, vy     : vecteur profondeur (bas -> haut de rampant)
      scale      : px/m (longueur du trait / longueur réelle, repli largeur)
      h_bas,h_haut : hauteurs (m) au bord avant et au fond
      av0,av1,ar0,ar1 : coins du toit (avant-G, avant-D, fond-G, fond-D)
      L_m        : longueur réelle (m) si connue
    """
    guides = guides_actifs(projet)
    if not guides or not guides.get("ombrieres"):
        return
    omb = projet.get("ombriere") or {}
    h_bas = omb.get("garde_au_sol_m") or 2.5
    h_haut = omb.get("hauteur_hors_tout_m") or 3.5

    for o in guides["ombrieres"]:
        a = (o["longueur"][0][0] * W, o["longueur"][0][1] * H)
        b = (o["longueur"][1][0] * W, o["longueur"][1][1] * H)
        c = (o["largeur"][0][0] * W, o["largeur"][0][1] * H)
        d = (o["largeur"][1][0] * W, o["largeur"][1][1] * H)
        long_px = math.hypot(b[0] - a[0], b[1] - a[1])
        # échelle px/m : longueur du trait / longueur réelle (repli : largeur)
        L_m = o.get("longueur_m")
        if L_m and L_m > 0:
            scale = long_px / L_m
        else:
            larg_px = math.hypot(d[0] - c[0], d[1] - c[1])
            l_m = o.get("largeur_m") or 8.0
            scale = larg_px / l_m if l_m else long_px / 18.0
        vx, vy = d[0] - c[0], d[1] - c[1]          # vecteur profondeur (bas->haut)
        a_far, b_far = (a[0] + vx, a[1] + vy), (b[0] + vx, b[1] + vy)

        def haut(pt, hm):
            return (pt[0], pt[1] - hm * scale)

        yield {
            "a": a, "b": b, "vx": vx, "vy": vy, "scale": scale,
            "h_bas": h_bas, "h_haut": h_haut, "L_m": L_m,
            "av0": haut(a, h_bas), "av1": haut(b, h_bas),         # avant (bas)
            "ar0": haut(a_far, h_haut), "ar1": haut(b_far, h_haut),  # fond (haut)
        }


def _toit_panneaux(dr, av0, av1, ar1, ar0, scale, L_m):
    """Dessine le toit en modules PV : fond sombre + grille de cellules.

    Le toit est le quad (avant-G, avant-D, fond-D, fond-G). On subdivise en
    perspective par interpolation bilinéaire : colonnes le long du bord (u),
    rangées en profondeur (v). Colonnes ~ une file de modules par place de
    parking (2,3 m), rangées ~ modules de 1,7 m. Ça se lit comme des panneaux.
    """
    dr.polygon([av0, av1, ar1, ar0], fill=GRIS_PANNEAU + (255,),
               outline=COUL_TRAIT_TOIT)

    def bilin(u, v):
        av = (av0[0] + (av1[0] - av0[0]) * u, av0[1] + (av1[1] - av0[1]) * u)
        ar = (ar0[0] + (ar1[0] - ar0[0]) * u, ar0[1] + (ar1[1] - ar0[1]) * u)
        return (av[0] + (ar[0] - av[0]) * v, av[1] + (ar[1] - av[1]) * v)

    largeur_m = (L_m or (math.hypot(av1[0] - av0[0], av1[1] - av0[1]) / scale)) or 12.0
    prof_px = math.hypot(ar0[0] - av0[0], ar0[1] - av0[1])
    prof_m = prof_px / scale if scale else 8.0
    ncol = max(4, min(40, round(largeur_m / 2.3)))
    nrow = max(2, min(20, round(prof_m / 1.7)))
    ep = max(1, round(scale * 0.03))

    for i in range(1, ncol):
        u = i / ncol
        dr.line([bilin(u, 0.0), bilin(u, 1.0)], fill=GRIS_CELLULE + (255,), width=ep)
    for j in range(1, nrow):
        v = j / nrow
        dr.line([bilin(0.0, v), bilin(1.0, v)], fill=GRIS_CELLULE + (255,), width=ep)


def scaffold_photo(projet: dict) -> Path | None:
    """Photo avec l'ombrière posée en VOLUME, depuis les 2 traits tracés.

    longueur = bord avant (bas de rampant), largeur = profondeur tracée du bas
    vers le haut de rampant. Le footprint est le parallélogramme (avant + vecteur
    largeur) ; la toiture monte de h_bas (avant) à h_haut (fond) ; poteaux
    verticaux. Toit texturé en modules PV (voir _toit_panneaux). Placement EXACT
    (c'est le tracé de l'utilisateur). None sans tracé.
    """
    from PIL import ImageDraw

    photo = image_kit(projet, "photo")
    geoms = list(_geometrie_ombrieres(projet, *_ouvrir_image(photo).size)) if photo else []
    if not photo or not geoms:
        return None

    image = _ouvrir_image(photo).convert("RGB")
    W, H = image.size
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    dr = ImageDraw.Draw(overlay)

    for g in geoms:
        a, b = g["a"], g["b"]
        vx, vy, scale = g["vx"], g["vy"], g["scale"]
        h_bas, h_haut = g["h_bas"], g["h_haut"]
        av0, av1, ar0, ar1 = g["av0"], g["av1"], g["ar0"], g["ar1"]

        def haut(pt, hm):
            return (pt[0], pt[1] - hm * scale)

        # poteaux : file avant + file fond, environ tous les 6 m
        nb = max(1, int(round((g["L_m"] or math.hypot(b[0] - a[0], b[1] - a[1]) / scale) / 6)))
        for k in range(nb + 1):
            t = k / nb
            pied_av = (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
            pied_ar = (pied_av[0] + vx, pied_av[1] + vy)
            for pied, hm in ((pied_av, h_bas), (pied_ar, h_haut)):
                tete = haut(pied, hm)
                w = max(4, 0.22 * scale)
                dr.polygon([(pied[0] - w, pied[1]), (pied[0] + w, pied[1]),
                            (tete[0] + w * 0.85, tete[1]), (tete[0] - w * 0.85, tete[1])],
                           fill=GRIS_POTEAU + (255,), outline=(90, 94, 100, 255))

        ep = max(5, 0.35 * scale)
        dr.polygon([av0, av1, (av1[0], av1[1] + ep), (av0[0], av0[1] + ep)],
                   fill=GRIS_FASCIA + (255,))
        _toit_panneaux(dr, av0, av1, ar1, ar0, scale, g["L_m"])

    image = Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB")
    sortie = config.assets_dir(projet.get("id")) / "scaffold.png"
    sortie.parent.mkdir(parents=True, exist_ok=True)
    image.save(sortie)
    return sortie


# ------------------------------------------------------------------ vue aérienne

def _analyse_plan(projet: dict):
    """Analyse (implantation) du plan de masse PDF, ou None."""
    source = _document_image(projet, "dp2")
    if not source or source.suffix.lower() != ".pdf":
        return None, None
    from . import implantation as mod_implantation
    return mod_implantation.analyser_plan(source), source


def _aerienne_crop(analyse) -> tuple[int, int, int, int]:
    """Fenêtre de crop (px plan) autour des rangées + flèche, marge 55 %."""
    xs, ys = [], []
    for z in analyse["rangees"]:
        for cx, cy in z["coins_px"]:
            xs.append(cx)
            ys.append(cy)
    for cle in ("pente_haut_px", "pente_bas_px"):
        if analyse[cle]:
            xs.append(analyse[cle][0])
            ys.append(analyse[cle][1])
    W, H = analyse["taille_px"]
    marge = round(0.55 * max(max(xs) - min(xs), max(ys) - min(ys))) + 60
    return (max(0, int(min(xs)) - marge), max(0, int(min(ys)) - marge),
            min(W, int(max(xs)) + marge), min(H, int(max(ys)) + marge))


def aerienne_donnees(projet: dict) -> dict | None:
    """Fond aérien (crop du plan) + emprises (stockées ou auto) + flèche.

    Renvoie {fond: Path, emprises: [[[x,y] x4]...] (0-1 crop), fleche: {a, b}
    ou None, auto: bool}. None si pas de plan analysable.
    """
    analyse, source = _analyse_plan(projet)
    if not analyse:
        return None
    x0, y0, x1, y1 = _aerienne_crop(analyse)
    lc, hc = x1 - x0, y1 - y0

    # fond nu, cache par date du plan
    assets = config.assets_dir(projet.get("id"))
    fond = assets / "aerienne_fond.png"
    if not (fond.exists() and fond.stat().st_mtime >= source.stat().st_mtime):
        with config.PDFIUM_LOCK:
            doc = pdfium.PdfDocument(str(source))
            try:
                image = doc[0].render(scale=150 / 72).to_pil().convert("RGB")
            finally:
                doc.close()
        assets.mkdir(parents=True, exist_ok=True)
        image.crop((x0, y0, x1, y1)).save(fond)

    # emprises : override utilisateur sinon rectangles PCA des rangées
    stocke = (projet.get("insertion") or {}).get("aerienne") or {}
    if stocke.get("emprises"):
        emprises = stocke["emprises"]
        auto = False
    else:
        emprises = [[[(cx - x0) / lc, (cy - y0) / hc] for cx, cy in z["coins_px"]]
                    for z in analyse["rangees"]]
        auto = True

    fleche = None
    if analyse["pente_haut_px"] and analyse["pente_bas_px"]:
        # direction haut->bas, tracée au centre de la 1re emprise
        hx, hy = analyse["pente_haut_px"]
        bx, by = analyse["pente_bas_px"]
        v = np.array([bx - hx, by - hy], dtype=float)
        n = float(np.hypot(*v)) or 1.0
        v /= n
        pts = np.array([[px * lc, py * hc] for px, py in emprises[0]])
        centre = pts.mean(axis=0)
        demi = 0.5 * min(lc, hc) / 3
        a = centre - demi * v
        b = centre + demi * v
        fleche = {"a": [float(a[0] / lc), float(a[1] / hc)],
                  "b": [float(b[0] / lc), float(b[1] / hc)]}

    return {"fond": fond, "emprises": emprises, "fleche": fleche, "auto": auto}


def aerienne_emprise(projet: dict) -> Path | None:
    """Vue aérienne annotée pour Gemini : emprises MAGENTA + flèche de pente.

    Aucun texte (anti-contamination). La flèche est sombre à liseré blanc.
    """
    from PIL import ImageDraw

    donnees = aerienne_donnees(projet)
    if not donnees:
        return None
    image = Image.open(donnees["fond"]).convert("RGB")
    dr = ImageDraw.Draw(image)
    l, h = image.size
    ep = max(4, round(min(l, h) / 160))

    for emprise in donnees["emprises"]:
        pts = [(x * l, y * h) for x, y in emprise]
        dr.line(pts + [pts[0]], fill=MAGENTA, width=ep)

    if donnees["fleche"]:
        a = np.array([donnees["fleche"]["a"][0] * l, donnees["fleche"]["a"][1] * h])
        b = np.array([donnees["fleche"]["b"][0] * l, donnees["fleche"]["b"][1] * h])
        v = b - a
        n = float(np.hypot(*v)) or 1.0
        v /= n
        p = np.array([-v[1], v[0]])
        for coul, larg in (((255, 255, 255), ep + 6), ((20, 20, 30), ep)):
            dr.line([tuple(a), tuple(b)], fill=coul, width=larg)
            for signe in (1, -1):
                pointe = b - (4.5 * ep) * v + signe * (2.6 * ep) * p
                dr.line([tuple(b), tuple(pointe)], fill=coul, width=larg)

    sortie = config.assets_dir(projet.get("id")) / "aerienne_emprise.png"
    image.save(sortie)
    return sortie


def resume_guides(guides: dict) -> str:
    """Résumé texte des repères pour l'UI."""
    n = len(guides["ombrieres"])
    return f"{n} ombrière{'s' if n > 1 else ''} tracée{'s' if n > 1 else ''}" if n else ""


def plan_dims(projet: dict) -> list[tuple[float, float]]:
    """Cotes (longueur, largeur) en m des rangées lues sur le plan de masse."""
    analyse, _ = _analyse_plan(projet)
    if not analyse:
        return []
    return [(z["longueur_m"], z["largeur_m"])
            for z in analyse["rangees"] if "longueur_m" in z]




def controle_couverture(projet: dict, photo_propre: Path, image_generee: bytes) -> dict | None:
    """Vérifie que Gemini a bien construit l'ombrière SUR toute l'emprise tracée.

    Panne constatée (17/07, Carrefour Anse) : le modèle raccourcit l'ombrière ou
    laisse un trou entre deux structures, malgré le scaffold. On rasterise
    l'emprise du toit attendue (les quads du scaffold) et on la compare à la zone
    réellement modifiée par Gemini (diff contre la photo propre). Le taux de
    recouvrement de l'emprise attendue donne un verdict :
      >= 0,82  ok        (l'ombrière couvre bien le tracé)
      >= 0,60  partiel   (structure raccourcie / trou partiel)
      <  0,60  faible    (placement raté, à régénérer)
    Renvoie None si aucun tracé exploitable (mode libre : rien à vérifier).
    """
    import io

    from PIL import Image, ImageDraw, ImageFilter

    try:
        orig = _ouvrir_image(photo_propre).convert("RGB")
        gen = Image.open(io.BytesIO(image_generee)).convert("RGB")
    except OSError:
        return None
    W0, H0 = orig.size
    geoms = list(_geometrie_ombrieres(projet, W0, H0))
    if not geoms:
        return None

    # résolution de travail (bornée pour la vitesse)
    ech = min(1.0, 1000 / max(W0, H0))
    W, H = max(1, round(W0 * ech)), max(1, round(H0 * ech))

    # emprise attendue = union des quads de toit
    masque_img = Image.new("L", (W, H), 0)
    dr = ImageDraw.Draw(masque_img)
    for g in geoms:
        quad = [g["av0"], g["av1"], g["ar1"], g["ar0"]]
        dr.polygon([(x * ech, y * ech) for x, y in quad], fill=255)
    attendu = np.asarray(masque_img) > 0
    aire_attendue = int(attendu.sum())
    if aire_attendue == 0:
        return None

    # zone réellement modifiée = diff (exposition alignée) gen vs photo propre
    gen = gen.resize((W, H), Image.LANCZOS)
    orig_s = orig.resize((W, H), Image.LANCZOS)
    o = np.asarray(orig_s.convert("L"), dtype=np.float32)
    g_arr = np.asarray(gen.convert("L"), dtype=np.float32)
    g_arr = (g_arr - g_arr.mean()) / (g_arr.std() or 1.0) * (o.std() or 1.0) + o.mean()
    diff = Image.fromarray(np.clip(np.abs(g_arr - o), 0, 255).astype(np.uint8))
    diff = diff.filter(ImageFilter.GaussianBlur(3))
    modifie = np.asarray(diff) > 16

    inter = int((attendu & modifie).sum())
    couverture = inter / aire_attendue
    verdict = "ok" if couverture >= 0.82 else "partiel" if couverture >= 0.60 else "faible"
    return {"couverture": round(couverture, 3), "verdict": verdict,
            "n_ombrieres": len(geoms)}




def _preparer_requete(projet: dict, affinage: str = "") -> dict:
    """Assemble le lot d'images v5 + le prompt auto (sans appeler Gemini).

    Méthode officielle Nano Banana : lot MINIMAL, rôles décrits en langage
    naturel (jamais numérotés). Deux images seulement :
      - base à éditer = la photo, annotée des axes magenta si tracés
      - référence structure = la coupe DP3 du BE (repli catalogue)
    La vue aérienne et le plan brut ne sont plus envoyés (source de confusion) ;
    l'implantation vient des axes que Florent trace sur la photo.
    Renvoie {chemins, roles, prompt, base_propre}. Lève InsertionError sans photo.
    """
    base_propre = image_kit(projet, "photo")
    if not base_propre:
        raise InsertionError("Ajoutez d'abord une photo du site (upload ou reprise d'une pièce BE).")

    # base à éditer : PRIORITÉ au scaffold (ombrière déjà posée en volume gris ;
    # Gemini n'a plus qu'à l'habiller sans la déplacer). Repli : axes magenta,
    # puis photo nue.
    guides = guides_actifs(projet)
    scaffold = scaffold_photo(projet) if guides else None
    if scaffold:
        base, mode = scaffold, "scaffold"
    elif guides and (annotee := photo_emprise(projet)):
        base, mode = annotee, "axes"
    else:
        base, mode = base_propre, "libre"

    roles = ["photo"]
    chemins: list[Path] = [base]

    # cotes réelles du plan
    plan_infos = None
    analyse, _ = _analyse_plan(projet)
    if analyse:
        dims = [(z["longueur_m"], z["largeur_m"])
                for z in analyse["rangees"] if "longueur_m" in z]
        if dims:
            plan_infos = {"dims_m": dims}

    # référence structure : la coupe DP3 du BE (repli catalogue)
    coupe_be = False
    coupe = image_kit(projet, "coupe_be")
    if coupe:
        coupe_be = True
    else:
        coupe = image_kit(projet, "coupe")
    if coupe:
        roles.append("coupe")
        chemins.append(coupe)

    # référence implantation : le plan de masse (crop nettoyé, avec sa légende)
    plan_ref = None
    aer = aerienne_donnees(projet)
    if aer and aer.get("fond"):
        plan_ref = aer["fond"]
        roles.append("plan")
        chemins.append(plan_ref)

    idx = {role: i + 1 for i, role in enumerate(roles)}
    prompt = construire_prompt(projet, affinage=affinage, plan_infos=plan_infos,
                               guides=guides, idx=idx, coupe_be=coupe_be,
                               mode=mode, plan_ref=bool(plan_ref))
    return {"chemins": chemins, "roles": roles, "prompt": prompt,
            "base_propre": base_propre, "mode": mode}


