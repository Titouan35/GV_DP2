"""Notices ERP (accessibilité et sécurité) adaptées au projet, en .docx.

Elles accompagnent le Cerfa 13824 (app/cerfa_erp.py) : le bordereau de
l'autorisation ERP réclame une notice d'accessibilité et une notice de
sécurité incendie. Le bureau d'études les rédigeait à la main en repartant du
dossier de MEHUN-SUR-YÈVRE ; les deux documents de ce dossier sont devenus les
gabarits (app/gabarits/notices_erp/, demande du 28/10/2026).

On ne régénère PAS le document : on ouvre le .docx du gabarit et on ne change
que les paragraphes propres au site, repérés par leur texte. Tout le reste
(mise en page, sommaire, pictogrammes, annexe 7.1) sort tel que le BE l'a écrit.
Ce qui change :

- les deux notices : adresse du site (page de garde, §1.1 ou §1.2), indice daté
  du jour, type et catégorie d'ERP (cases M1 à M5 de l'étape 5 ; avec la case
  « Type M », les deux notices portent « Type M » seul) ;
- la notice sécurité : §4.1 (modules, puissance, ombrières, onduleurs) et les
  annexes 7.2 (plan de masse DP2), 7.3 (fiche technique des modules) et 7.4
  (fiche de l'onduleur choisie d'après son modèle).

Le maître d'ouvrage (Greenvolt Next France) et l'exploitant restent ceux du
gabarit : ils ne changent pas d'un site à l'autre. Une donnée manquante n'est
jamais devinée : sa place est laissée surlignée en jaune « [À COMPLÉTER] » et
signalée en avertissement, comme pour le Cerfa.

Les .docx sont manipulés directement en XML (lxml, déjà installé avec
python-pptx) : pas de dépendance supplémentaire dans le runtime portable.
"""
from __future__ import annotations

import io
import logging
import re
import zipfile
from datetime import date
from pathlib import Path

from lxml import etree
from PIL import Image

from . import config
from .cerfa import _RE_ADRESSE

logger = logging.getLogger(__name__)

GABARITS = config.REPO_ROOT / "app" / "gabarits" / "notices_erp"
GABARIT_ACCESSIBILITE = GABARITS / "notice_accessibilite.docx"
GABARIT_SECURITE = GABARITS / "notice_securite.docx"
FICHES = config.REPO_ROOT / "app" / "gabarits" / "fiches_techniques"
FICHE_MODULE = FICHES / "AIKO_Neostar-3Pplus54_465-495W.pdf"

# Modèle d'onduleur -> fiche technique Sungrow. Le modèle vient de la FVE
# (« SUNGROW SG50CX-P2 ») ou de l'étape 3 ; on compare sans espace ni tiret.
FICHES_ONDULEUR = [
    # « SG » facultatif : une FVE écrit « SUNGROW 125 CX-P2 »
    (re.compile(r"(SG|SUNGROW)150CX"), FICHES / "SUNGROW_SG150CX.pdf"),
    (re.compile(r"(SG|SUNGROW)125CX"), FICHES / "SUNGROW_SG125CX-P2.pdf"),
    (re.compile(r"(SG|SUNGROW)(36|40|50)CX"), FICHES / "SUNGROW_SG36_40_50CX-P2.pdf"),
]
MODELES_ONDULEUR = ["SG36CX-P2", "SG40CX-P2", "SG50CX-P2", "SG125CX-P2", "SG150CX"]

TYPE_ERP = "M"            # magasin de vente : tous nos sites sont des Carrefour Market
CATEGORIES_ERP = (1, 2, 3, 4, 5)
A_COMPLETER = "[À COMPLÉTER]"

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
WP = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
PIC = "http://schemas.openxmlformats.org/drawingml/2006/picture"
REL = "http://schemas.openxmlformats.org/package/2006/relationships"
CT = "http://schemas.openxmlformats.org/package/2006/content-types"
NS = {"w": W}
TYPE_IMAGE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/image"

EMU_CM = 360000
# Dernière section de la notice sécurité (annexes 7.2 à 7.4) : A4 portrait,
# marges 1133/850 twips -> 17,5 cm utiles. Hauteur bornée à 22 cm pour que la
# première page d'une fiche tienne sous son titre d'annexe.
LARGEUR_MAX_CM = 17.0
HAUTEUR_MAX_CM = 22.0


# --- données du projet -------------------------------------------------------

def _nombre_fr(v) -> str:
    """120.05 -> « 120,05 », 490.0 -> « 490 »."""
    if v is None or v == "":
        return ""
    v = float(v)
    if v == int(v):
        return str(int(v))
    return f"{v:.2f}".rstrip("0").rstrip(".").replace(".", ",")


def lignes_adresse(projet: dict) -> tuple[str, str]:
    """(« Avenue Jean Chatelet », « 18500 MEHUN-SUR-YÈVRE ») pour l'en-tête.

    Même découpage que le Cerfa (app/cerfa.py:_RE_ADRESSE), sans sa coupe à
    40 caractères. La commune du géocodage prime sur celle lue dans le texte.
    """
    loc = projet.get("localisation") or {}
    adresse = (loc.get("adresse") or "").strip()
    m = _RE_ADRESSE.match(adresse) if adresse else None
    if m:
        voie = " ".join(x for x in ((m.group("numero") or "").strip(), (m.group("voie") or "").strip()) if x)
        cp = m.group("cp")
        commune = loc.get("commune") or m.group("localite") or ""
    else:
        voie, cp, commune = adresse, loc.get("code_postal") or "", loc.get("commune") or ""
    voie = voie.rstrip(" ,")
    cp_commune = " ".join(x for x in (loc.get("code_postal") or cp or "", commune.upper()) if x)
    return voie, cp_commune


def categorie_erp(projet: dict) -> int | None:
    c = (projet.get("erp") or {}).get("categorie")
    try:
        c = int(c)
    except (TypeError, ValueError):
        return None
    return c if c in CATEGORIES_ERP else None


def libelle_classement(projet: dict) -> str | None:
    """« type M – 5e catégorie », ou None si la catégorie n'est pas cochée."""
    c = categorie_erp(projet)
    if c is None:
        return None
    rang = "1re" if c == 1 else f"{c}e"
    return f"type {TYPE_ERP} – {rang} catégorie"


def type_m_seul(projet: dict) -> bool:
    """Case « Type M » cochée à l'étape 5 (exclusive avec M1 à M5)."""
    return bool((projet.get("erp") or {}).get("type_m_seul"))


def classement(projet: dict) -> str | None:
    """Classement écrit dans les deux notices : « Type M » si la case Type M
    est cochée, sinon « type M – 5e catégorie » d'après M1 à M5."""
    return f"Type {TYPE_ERP}" if type_m_seul(projet) else libelle_classement(projet)


def fiche_onduleur(modele: str | None) -> Path | None:
    cle = re.sub(r"[\s\-_]", "", (modele or "").upper())
    for motif, chemin in FICHES_ONDULEUR:
        if motif.search(cle):
            return chemin
    return None


# --- manipulation du document ------------------------------------------------

def _w(tag: str) -> str:
    return f"{{{W}}}{tag}"


def _texte(p) -> str:
    """Texte des runs DIRECTS du paragraphe (pas celui des zones de texte)."""
    return "".join(p.xpath("./w:r/w:t/text()|./w:hyperlink/w:r/w:t/text()", namespaces=NS))


def _paragraphes(racine, debut: str) -> list:
    return [p for p in racine.iter(_w("p")) if _texte(p).strip().startswith(debut)]


def _paragraphe(racine, debut: str, rang: int = 0):
    """`rang` -1 = la DERNIÈRE occurrence : les titres d'annexe figurent aussi
    dans le sommaire, en tête de document, où il ne faut rien insérer."""
    trouves = _paragraphes(racine, debut)
    if not trouves or len(trouves) <= rang:
        raise ValueError(f"Gabarit de notice inattendu : paragraphe « {debut}… » introuvable.")
    return trouves[rang]


def _run(rpr, texte: str, surligne: bool = False):
    r = etree.Element(_w("r"))
    if rpr is not None:
        r.append(etree.fromstring(etree.tostring(rpr)))
    rp = r.find(_w("rPr"))
    if rp is not None:
        for h in rp.findall(_w("highlight")):
            rp.remove(h)
    if surligne:
        if rp is None:
            rp = etree.SubElement(r, _w("rPr"))
        h = etree.SubElement(rp, _w("highlight"))
        h.set(_w("val"), "yellow")
    t = etree.SubElement(r, _w("t"))
    t.text = texte
    t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    return r


def _remplacer(p, morceaux, lignes: bool = False) -> None:
    """Remplace le contenu du paragraphe en gardant sa mise en forme.

    `morceaux` : liste de (texte, surligné). Avec `lignes=True`, chaque
    morceau est une ligne (saut de ligne entre deux) : sert aux adresses, dont
    les paragraphes du gabarit ont des retraits qui coupaient l'adresse au
    hasard de sa longueur.
    """
    premier = p.find(".//" + _w("r"))
    rpr = premier.find(_w("rPr")) if premier is not None else None
    for enfant in list(p):
        if enfant.tag != _w("pPr"):
            p.remove(enfant)
    for i, (texte, surligne) in enumerate(morceaux):
        if lignes and i:
            br = etree.SubElement(p, _w("r"))
            if rpr is not None:
                br.append(etree.fromstring(etree.tostring(rpr)))
            etree.SubElement(br, _w("br"))
        p.append(_run(rpr, texte, surligne))


def _desurligner(p) -> None:
    for h in p.iter(_w("highlight")):
        h.getparent().remove(h)


def _morceau(valeur: str | None, manques: list[str], libelle: str):
    if valeur:
        return (valeur, False)
    manques.append(libelle)
    return (A_COMPLETER, True)


def _indice(racine, aujourd_hui: str) -> None:
    p = _paragraphe(racine, "Indice")
    m = re.match(r"\s*(Indice\s*\S+)", _texte(p))
    _remplacer(p, [(f"{m.group(1) if m else 'Indice 1'} - {aujourd_hui}", False)])


class _Paquet:
    """Un .docx ouvert : document.xml, ses relations, et les médias ajoutés."""

    def __init__(self, gabarit: Path):
        self.gabarit = gabarit
        with zipfile.ZipFile(gabarit) as z:
            self.fichiers = {n: z.read(n) for n in z.namelist()}
        self.doc = etree.fromstring(self.fichiers["word/document.xml"])
        self.rels = etree.fromstring(self.fichiers["word/_rels/document.xml.rels"])
        self.types = etree.fromstring(self.fichiers["[Content_Types].xml"])
        self._n = 0

    def ajouter_image(self, png: bytes, ext: str) -> str:
        self._n += 1
        nom = f"annexe_erp_{self._n}.{ext}"
        self.fichiers[f"word/media/{nom}"] = png
        rid = f"rIdNoticeErp{self._n}"
        rel = etree.SubElement(self.rels, f"{{{REL}}}Relationship")
        rel.set("Id", rid)
        rel.set("Type", TYPE_IMAGE)
        rel.set("Target", f"media/{nom}")
        if not any(d.get("Extension") == ext for d in self.types.findall(f"{{{CT}}}Default")):
            d = etree.Element(f"{{{CT}}}Default")
            d.set("Extension", ext)
            d.set("ContentType", "image/png" if ext == "png" else "image/jpeg")
            self.types.insert(0, d)
        return rid

    def paragraphe_image(self, image: Image.Image, titre: str):
        """Paragraphe centré portant l'image, à la taille maximale permise."""
        tampon = io.BytesIO()
        if image.mode not in ("RGB", "L"):
            image = image.convert("RGB")
        image.save(tampon, format="JPEG", quality=88)
        rid = self.ajouter_image(tampon.getvalue(), "jpeg")
        larg, haut = image.size
        echelle = min(LARGEUR_MAX_CM / larg, HAUTEUR_MAX_CM / haut)
        cx, cy = int(larg * echelle * EMU_CM), int(haut * echelle * EMU_CM)
        ident = 9000 + self._n
        xml = f"""<w:p xmlns:w="{W}" xmlns:r="{R}" xmlns:wp="{WP}" xmlns:a="{A}" xmlns:pic="{PIC}">
<w:pPr><w:spacing w:before="120" w:after="0"/><w:jc w:val="center"/></w:pPr>
<w:r><w:drawing><wp:inline distT="0" distB="0" distL="0" distR="0">
<wp:extent cx="{cx}" cy="{cy}"/><wp:docPr id="{ident}" name="{titre}"/>
<wp:cNvGraphicFramePr><a:graphicFrameLocks noChangeAspect="1"/></wp:cNvGraphicFramePr>
<a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">
<pic:pic><pic:nvPicPr><pic:cNvPr id="{ident}" name="{titre}"/><pic:cNvPicPr/></pic:nvPicPr>
<pic:blipFill><a:blip r:embed="{rid}"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill>
<pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm>
<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr></pic:pic>
</a:graphicData></a:graphic></wp:inline></w:drawing></w:r></w:p>"""
        return etree.fromstring(xml)

    def numeroter_pieds_de_page(self) -> None:
        """Rend leurs champs PAGE / NUMPAGES aux pieds de page figés.

        Dans le gabarit de la notice sécurité, seul le premier pied de page
        porte de vrais champs : ceux des sections « Annexes » sont du texte
        en dur (« 11 / 14 », « 12 / 14 »), restes d'une conversion. Avec les
        pages de fiches techniques insérées, toutes les annexes affichaient
        « 12 / 14 » (signalé le 28/10/2026). On remplace le premier nombre par
        PAGE et le dernier par NUMPAGES, en gardant la mise en forme du run.
        """
        for nom in [n for n in self.fichiers if re.fullmatch(r"word/footer\d*\.xml", n)]:
            racine = etree.fromstring(self.fichiers[nom])
            modifie = False
            for p in racine.iter(_w("p")):
                if p.find(".//" + _w("fldChar")) is not None or p.find(".//" + _w("fldSimple")) is not None:
                    continue
                runs = [r for r in p.findall(_w("r")) if r.find(_w("t")) is not None]
                texte = "".join(r.find(_w("t")).text or "" for r in runs)
                if not re.fullmatch(r"\s*\d+\s*/\s*\d+\s*", texte):
                    continue
                nombres = [r for r in runs if (r.find(_w("t")).text or "").strip().isdigit()]
                if len(nombres) != 2:
                    continue
                for run, champ in zip(nombres, ("PAGE", "NUMPAGES")):
                    rpr = run.find(_w("rPr"))
                    valeur = run.find(_w("t")).text
                    nouveaux = []
                    for genre in ("begin", "instr", "separate", "valeur", "end"):
                        r = etree.Element(_w("r"))
                        if rpr is not None:
                            r.append(etree.fromstring(etree.tostring(rpr)))
                        if genre == "instr":
                            el = etree.SubElement(r, _w("instrText"))
                            el.text = f" {champ} "
                            el.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
                        elif genre == "valeur":
                            etree.SubElement(r, _w("t")).text = valeur
                        else:
                            etree.SubElement(r, _w("fldChar")).set(_w("fldCharType"), genre)
                        nouveaux.append(r)
                    for r in nouveaux:
                        run.addprevious(r)
                    p.remove(run)
                modifie = True
            if modifie:
                self.fichiers[nom] = etree.tostring(racine, xml_declaration=True,
                                                    encoding="UTF-8", standalone=True)

    def enregistrer(self, chemin: Path) -> None:
        self.fichiers["word/document.xml"] = etree.tostring(
            self.doc, xml_declaration=True, encoding="UTF-8", standalone=True)
        self.fichiers["word/_rels/document.xml.rels"] = etree.tostring(
            self.rels, xml_declaration=True, encoding="UTF-8", standalone=True)
        self.fichiers["[Content_Types].xml"] = etree.tostring(
            self.types, xml_declaration=True, encoding="UTF-8", standalone=True)
        tmp = chemin.with_suffix(".tmp.docx")
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
            # [Content_Types].xml en tête d'archive, comme Word l'écrit
            for nom in sorted(self.fichiers, key=lambda n: n != "[Content_Types].xml"):
                z.writestr(nom, self.fichiers[nom])
        tmp.replace(chemin)


def _pages_pdf(chemin: Path, dpi: int = 150) -> list[Image.Image]:
    import pypdfium2 as pdfium
    with config.PDFIUM_LOCK:
        pdf = pdfium.PdfDocument(str(chemin))
        try:
            return [pdf[i].render(scale=dpi / 72).to_pil() for i in range(len(pdf))]
        finally:
            pdf.close()


def _images_plan_masse(projet: dict) -> list[Image.Image]:
    """La pièce DP2 du projet (1re page si PDF), tournée en portrait si besoin
    pour occuper la page A4 de l'annexe plutôt qu'un bandeau illisible."""
    doc = (projet.get("documents") or {}).get("dp2")
    if not doc:
        return []
    # confiné à l'espace : `fichier` vient du navigateur (config.chemin_dans_espace)
    chemin = config.chemin_dans_espace(doc.get("fichier"))
    if chemin is None or not chemin.exists():
        return []
    if chemin.suffix.lower() == ".pdf":
        image = _pages_pdf(chemin, dpi=200)[0]
    else:
        with Image.open(chemin) as im:
            image = im.copy()
    if image.width > image.height:
        image = image.rotate(90, expand=True)
    # un plan A0 rendu à 200 dpi pèse des dizaines de Mo : 3500 px suffisent
    image.thumbnail((3500, 3500))
    return [image]


def _inserer_apres(p, nouveaux: list) -> None:
    for n in reversed(nouveaux):
        p.addnext(n)


def _saut_de_page_avant(p) -> None:
    ppr = p.find(_w("pPr"))
    if ppr is None:
        ppr = etree.Element(_w("pPr"))
        p.insert(0, ppr)
    if ppr.find(_w("pageBreakBefore")) is None:
        # pPr est une séquence ordonnée : pageBreakBefore suit pStyle/keepNext/keepLines
        apres = [e for e in ppr if e.tag in (_w("pStyle"), _w("keepNext"), _w("keepLines"))]
        el = etree.Element(_w("pageBreakBefore"))
        if apres:
            apres[-1].addnext(el)
        else:
            ppr.insert(0, el)


# --- les deux notices ----------------------------------------------------------

def generer_accessibilite(projet: dict, sortie: Path) -> list[str]:
    paquet = _Paquet(GABARIT_ACCESSIBILITE)
    paquet.numeroter_pieds_de_page()
    doc = paquet.doc
    manques: list[str] = []
    voie, cp_commune = lignes_adresse(projet)
    aujourd_hui = date.today().strftime("%d/%m/%Y")

    # page de garde : deux lignes d'adresse sous « CARREFOUR MARKET »
    _remplacer(_paragraphe(doc, "Avenue Jean"), [_morceau(voie, manques, "adresse du site (étape 1)")])
    _remplacer(_paragraphe(doc, "18500"), [_morceau(cp_commune, [], "")])
    _indice(doc, aujourd_hui)
    # §1.1 : nom, maître d'ouvrage, exploitant et nature restent ceux du gabarit
    _remplacer(_paragraphe(doc, "Adresse :"),
               [("Adresse : ", False), _morceau(" ".join(x for x in (voie, cp_commune) if x), [], "")])
    # case « Type M » cochée : « Type M » à la place du type et de la catégorie
    _remplacer(_paragraphe(doc, "Type et catégorie d'ERP"),
               [("Type et catégorie d'ERP : ", False),
                _morceau(classement(projet), manques, "catégorie d'ERP (cases M1 à M5 ou Type M, étape 5)")])
    paquet.enregistrer(sortie)
    return [f"À compléter : {m}." for m in manques]


def generer_securite(projet: dict, sortie: Path) -> list[str]:
    paquet = _Paquet(GABARIT_SECURITE)
    paquet.numeroter_pieds_de_page()
    doc = paquet.doc
    manques: list[str] = []
    voie, cp_commune = lignes_adresse(projet)
    aujourd_hui = date.today().strftime("%d/%m/%Y")
    omb = projet.get("ombriere") or {}
    # Le gabarit redémarre à 1 après la garde et le sommaire, alors que
    # NUMPAGES compte toutes les pages : la dernière page affichait « 16 / 18 ».
    # Numérotation physique : la dernière page porte « N / N ».
    for num in doc.iter(_w("pgNumType")):
        num.attrib.pop(_w("start"), None)

    # page de garde et §1.2 « Situation de l'ouvrage » : même paragraphe d'adresse
    lignes = [_morceau(voie, manques, "adresse du site (étape 1)"), _morceau(cp_commune, [], "")]
    for p in _paragraphes(doc, "Avenue Jean"):
        _remplacer(p, lignes, lignes=True)
    _indice(doc, aujourd_hui)
    # §1.4 et §1.5 (contrôleur technique, SPS) : laissés tels quels

    # §2 Classement
    _remplacer(_paragraphe(doc, "Le CARREFOUR Market est un magasin de vente"), [
        ("Le CARREFOUR Market est un magasin de vente de produits alimentaires et non "
         "alimentaires. Son classement ERP est : ", False),
        _morceau(classement(projet), manques, "catégorie d'ERP (cases M1 à M5 ou Type M, étape 5)"),
        (".", False)])

    # §4.1 Description du projet
    nb_mod = omb.get("nb_modules_total")
    nb_omb = omb.get("nb_ombrieres")
    nb_ond = omb.get("nb_onduleurs")
    _remplacer(_paragraphe(doc, "245 panneaux"), [
        _morceau(_nombre_fr(nb_mod), manques, "nombre total de modules (étape 3)"),
        (" panneaux photovoltaïques de puissance unitaire ", False),
        _morceau(_nombre_fr(omb.get("module_puissance_wc")), manques, "puissance d'un module (étape 3)"),
        (" Wc (puissance totale ", False),
        _morceau(_nombre_fr(omb.get("puissance_kwc")), manques, "puissance crête (étape 3)"),
        (" kWc) sur ", False),
        _morceau(_nombre_fr(nb_omb), manques, "nombre d'ombrières (étape 3 ou 5)"),
        (" ombrières de parking ;" if (nb_omb or 0) > 1 else " ombrière de parking ;", False)])
    pluriel = (nb_ond or 0) != 1
    _remplacer(_paragraphe(doc, "2 onduleurs photovoltaïques"), [
        _morceau(_nombre_fr(nb_ond), manques, "nombre d'onduleurs (étape 3)"),
        ((" onduleurs photovoltaïques positionnés en haut des poteaux d’ombrières. Ces équipements"
          if pluriel else
          " onduleur photovoltaïque positionné en haut des poteaux d’ombrières. Cet équipement")
         + " et les câbles qui y sont raccordés sont situés à plus de 2,50 m du sol "
           "(inaccessibles par une personne se situant en dessous et levant le bras)", False)])

    # §7 Annexes : plan de masse, fiche des modules, fiche de l'onduleur
    p72 = _paragraphe(doc, "Avec localisation des dispositifs de coupure")
    for p in (_paragraphe(doc, "ANNEXE 2", -1), p72):
        _desurligner(p)
    plan = _images_plan_masse(projet)
    if plan:
        _inserer_apres(p72, [paquet.paragraphe_image(im, "Plan de masse") for im in plan])
    else:
        manques.append("plan de masse en annexe 7.2 (pièce DP2, étape 4)")

    fiche_ond = fiche_onduleur(omb.get("modele_onduleur"))
    for titre, fiche, libelle in (
            ("ANNEXE 3", FICHE_MODULE, None),
            ("ANNEXE 4", fiche_ond, "modèle d'onduleur (étape 3) : fiche de l'annexe 7.4 non choisie")):
        p_titre = _paragraphe(doc, titre, -1)
        p_fiche = p_titre.getnext()
        while p_fiche is not None and _texte(p_fiche).strip() != "Fiche technique":
            p_fiche = p_fiche.getnext()
        _saut_de_page_avant(p_titre)
        _desurligner(p_titre)
        if fiche is None:
            manques.append(libelle)
            continue
        if p_fiche is not None:
            _desurligner(p_fiche)
        _inserer_apres(p_fiche if p_fiche is not None else p_titre,
                       [paquet.paragraphe_image(im, f"Fiche technique {fiche.stem}")
                        for im in _pages_pdf(fiche)])

    modele = (omb.get("type_module") or "").upper()
    if modele and "AIKO" not in modele:
        manques.append(f"fiche des modules : le projet indique « {omb['type_module']} », "
                       "l'annexe 7.3 porte la fiche AIKO Neostar 3P+54 — à remplacer si besoin")

    paquet.enregistrer(sortie)
    return [f"À compléter : {m}." for m in manques]


def generer(projet: dict) -> tuple[dict[str, Path], list[str]]:
    """Génère les deux notices dans les assets du projet.

    Renvoie ({"accessibilite": chemin, "securite": chemin}, avertissements).
    """
    for g in (GABARIT_ACCESSIBILITE, GABARIT_SECURITE):
        if not g.exists():
            raise FileNotFoundError(f"Gabarit de notice ERP absent ({g.relative_to(config.REPO_ROOT)}).")
    assets = config.assets_dir(projet["id"])
    chemins = {"accessibilite": assets / "notice_accessibilite.docx",
               "securite": assets / "notice_securite.docx"}
    avert = [f"Accessibilité — {a}" for a in generer_accessibilite(projet, chemins["accessibilite"])]
    avert += [f"Sécurité — {a}" for a in generer_securite(projet, chemins["securite"])]
    return chemins, avert
