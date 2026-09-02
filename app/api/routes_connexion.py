"""Page de connexion, déconnexion, et identité de la session.

Ajouté le 02/09/2026 pour l'hébergement partagé. Jusque-là, l'authentification
ne passait que par HTTP Basic : correct pour un script, pénible pour une
équipe (fenêtre grise du navigateur, aucun moyen de se déconnecter, et
identifiants renvoyés à chaque requête).

HTTP Basic reste accepté en parallèle : les sondes et les appels en ligne de
commande continuent de fonctionner sans session.
"""
from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from .. import securite

router = APIRouter(tags=["connexion"])


def _page(message: str = "", champ: str = "") -> str:
    """Formulaire de connexion, aux couleurs Greenvolt, sans dépendance."""
    alerte = (f'<p class="err">{message}</p>' if message else "")
    return f"""<!doctype html>
<html lang="fr"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>GV_DP · Connexion</title>
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='%2305DB79' d='M13 2 4 14h6l-1 8 9-12h-6z'/%3E%3C/svg%3E">
<style>
  :root {{ --navy:#002455; --vert:#05DB79; --line:#DFDFDD; --muted:#667C99; --red:#B4232A; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; min-height:100vh; display:flex; align-items:center; justify-content:center;
         background:#F5F7FA; font-family:system-ui,-apple-system,"Segoe UI",sans-serif; color:var(--navy); }}
  .carte {{ background:#fff; border:1px solid var(--line); border-radius:14px;
            padding:34px 32px; width:min(92vw,380px); box-shadow:0 1px 3px rgba(0,0,0,.05); }}
  h1 {{ font-size:20px; margin:0 0 4px; }}
  .sous {{ color:var(--muted); font-size:13.5px; margin:0 0 22px; line-height:1.5; }}
  label {{ display:block; font-size:13px; font-weight:600; margin:14px 0 5px; }}
  input {{ width:100%; height:42px; padding:0 12px; border:1px solid var(--line);
           border-radius:9px; font-size:14px; font-family:inherit; }}
  input:focus {{ outline:none; border-color:var(--vert); box-shadow:0 0 0 3px #05DB7922; }}
  button {{ width:100%; height:44px; margin-top:22px; border:0; border-radius:9px;
            background:var(--navy); color:#fff; font-size:14.5px; font-weight:600;
            font-family:inherit; cursor:pointer; }}
  button:hover {{ background:#00306f; }}
  .err {{ background:#FCE8E9; color:var(--red); border-radius:8px; padding:9px 12px;
          font-size:13px; margin:0 0 14px; }}
</style></head>
<body>
  <form class="carte" method="post" action="/connexion">
    <h1>GV_DP</h1>
    <p class="sous">Générateur de déclaration préalable, ombrières photovoltaïques de parking.</p>
    {alerte}
    <label for="utilisateur">Identifiant</label>
    <input id="utilisateur" name="utilisateur" autocomplete="username" autofocus
           value="{champ}" required>
    <label for="motdepasse">Mot de passe</label>
    <input id="motdepasse" name="motdepasse" type="password"
           autocomplete="current-password" required>
    <button type="submit">Se connecter</button>
  </form>
</body></html>"""


@router.get("/connexion", response_class=HTMLResponse)
def formulaire(request: Request):
    """Formulaire. Déjà connecté, ou instance non protégée : on renvoie à l'outil."""
    if not securite.comptes():
        return RedirectResponse("/", status_code=303)
    if securite.verifier_session(request.cookies.get(securite.COOKIE_SESSION)):
        return RedirectResponse("/", status_code=303)
    return HTMLResponse(_page())


@router.post("/connexion")
def connecter(request: Request, utilisateur: str = Form(""), motdepasse: str = Form("")):
    definis = securite.comptes()
    attendu = definis.get(utilisateur.strip()) or securite.LEURRE
    # On vérifie toujours, même pour un identifiant inconnu : sortir tôt
    # révélerait par la durée quels comptes existent.
    valide = securite.verifier_mot_de_passe(motdepasse, attendu)
    if not (valide and utilisateur.strip() in definis):
        return HTMLResponse(
            _page("Identifiant ou mot de passe incorrect.", utilisateur.strip()[:80]),
            status_code=401)

    reponse = RedirectResponse("/", status_code=303)
    reponse.set_cookie(
        securite.COOKIE_SESSION, securite.creer_session(utilisateur.strip()),
        max_age=securite.DUREE_SESSION_H * 3600,
        httponly=True,          # inaccessible au JavaScript : pas de vol par XSS
        samesite="lax",         # pas envoyé depuis un site tiers
        secure=securite.cookie_securise(request),
        path="/",
    )
    return reponse


@router.post("/deconnexion")
def deconnecter():
    reponse = RedirectResponse("/connexion", status_code=303)
    reponse.delete_cookie(securite.COOKIE_SESSION, path="/")
    return reponse


@router.get("/api/moi")
def moi(request: Request):
    """Qui est connecté, pour que l'interface affiche le bouton de déconnexion."""
    return JSONResponse({
        "utilisateur": request.headers.get("x-utilisateur"),
        "mode": securite.mode(),
    })
