<#
    Guetteur d'ouverture du navigateur.

    Extrait du .bat le 01/09/2026. Trois raisons :

    1. La commande inline du .bat enchaînait les continuations « ^ », les
       guillemets et les apostrophes. Un chemin OneDrive contenant une
       apostrophe, un crochet ou une esperluette cassait la commande en
       silence (chemin d'un collègue, dossier renommé...).
    2. Start-Process était DANS le try : quand l'ouverture du navigateur
       échouait (pas de gestionnaire http par défaut, stratégie de poste),
       l'exception partait dans le catch, `break` n'était jamais atteint et
       la boucle repartait. Le navigateur ne s'ouvrait jamais, sans une
       seule trace. C'est l'un des deux symptômes décrits par l'utilisateur.
    3. Rien n'était journalisé : impossible de diagnostiquer à distance.

    Le guetteur écrit désormais TOUT dans SON PROPRE journal. Il ne partage
    pas celui du serveur : le .bat y garde un handle d'ajout ouvert pendant
    toute la session, et les écritures concurrentes échouaient en silence
    (constaté au premier essai, 01/09/2026).
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Url,
    [Parameter(Mandatory = $true)][string]$Log,
    [int]$TimeoutSecondes = 180
)

function Journal([string]$message) {
    $ligne = "[{0}] guetteur : {1}" -f (Get-Date -Format 'dd/MM/yyyy HH:mm:ss'), $message
    try { Add-Content -LiteralPath $Log -Value $ligne -Encoding UTF8 } catch { }
}

# Journal dédié, à côté de celui du serveur (voir l'en-tête, point 3).
$Log = [System.IO.Path]::Combine(
    [System.IO.Path]::GetDirectoryName($Log), 'gvdp_navigateur.log')
try { Set-Content -LiteralPath $Log -Value '' -Encoding UTF8 } catch { }

Journal "démarré, attente de $Url (jusqu'à $TimeoutSecondes s)"

$limite = (Get-Date).AddSeconds($TimeoutSecondes)
$pret = $false

while ((Get-Date) -lt $limite) {
    try {
        $null = Invoke-WebRequest -UseBasicParsing -TimeoutSec 3 -Uri $Url
        $pret = $true
        break
    }
    catch {
        Start-Sleep -Seconds 2
    }
}

if (-not $pret) {
    Journal "le serveur n'a pas répondu dans le délai, navigateur non ouvert"
    exit 1
}

Journal "serveur prêt, ouverture du navigateur"

# HORS du try de l'attente : un échec d'ouverture doit être vu et journalisé,
# jamais avalé puis retenté en boucle.
try {
    Start-Process $Url -ErrorAction Stop
    Journal "navigateur ouvert"
    exit 0
}
catch {
    Journal "ECHEC d'ouverture du navigateur : $($_.Exception.Message)"
    Journal "ouvre manuellement cette adresse : $Url"
    exit 2
}
