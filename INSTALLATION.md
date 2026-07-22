# GV_DP — Démarrer sur ton poste

Outil interne du bureau d'études : on saisit les informations d'un projet
d'ombrière photovoltaïque de parking, il produit le dossier de Déclaration
Préalable prêt à déposer (Cerfa, pièces DP1 à DP11, notice, assemblage PPTX/PDF).

**Il n'y a rien à installer.** Le moteur est inclus dans le dossier partagé.

---

## Démarrer (2 minutes)

1. Ouvre le dossier OneDrive **`Innovation\OUTILS\DP\GV_DP`**
2. La première fois : clic droit sur le dossier → **« Toujours conserver sur
   cet appareil »**, puis laisse OneDrive finir de tout télécharger (~450 Mo,
   pastille verte pleine ✓ sur le dossier). C'est le seul temps d'attente.
3. Double-clique sur **`Lancer GV_DP.bat`**

Une fenêtre noire s'ouvre, puis ton navigateur sur `http://localhost:8420`.
Astuce : clic droit sur `Lancer GV_DP.bat` → Envoyer vers → Bureau, pour avoir
un raccourci.

> En cas de souci avec ce mode (antivirus, lenteur), le plan B est une
> installation locale classique : installe « Python 3.13 » depuis le Microsoft
> Store, puis lance `Installer GV_DP.bat`. Tout le reste est identique.

---

## Utiliser l'outil au quotidien

Double-clique sur le raccourci **GV_DP** de ton Bureau.

Une fenêtre noire s'ouvre, puis ton navigateur sur `http://localhost:8420`.

- **Garde la fenêtre noire ouverte** pendant que tu travailles : c'est elle qui
  fait tourner l'outil.
- **Ferme-la** pour arrêter l'outil quand tu as fini.
- Si le navigateur affiche « site inaccessible », vérifie que cette fenêtre est
  toujours là.

---

## Travail en équipe : ce qu'il faut savoir

Les projets sont **communs à toute l'équipe** : ils vivent dans le dossier
OneDrive partagé, et chacun les voit depuis son poste. Il n'y a pas de serveur
central, chaque poste exécute sa propre copie de l'outil sur les mêmes dossiers.

Trois conséquences pratiques :

**Un projet à la fois, par une seule personne.** Quand tu ouvres un dossier,
l'outil signale ta présence aux autres, comme le fait Word sur SharePoint. Si un
collègue l'a déjà ouvert, tu vois « ouvert par … » et tu peux soit attendre,
soit prendre la main en connaissance de cause. **Ne travaillez jamais à deux sur
le même dossier en même temps** : OneDrive créerait une « copie de conflit » et
du travail serait perdu.

**La synchronisation prend quelques secondes.** Un projet créé par un collègue
n'apparaît pas instantanément chez toi. Patiente un instant et recharge la page.

**La génération d'images est facturée.** Toute l'équipe utilise la même clé, et
chaque insertion générée coûte environ 0,13 €. L'outil affiche le total de
l'équipe et enregistre qui a généré quoi. Vérifie le volume dans l'aperçu avant
de lancer une génération : le rendu ne sera pas meilleur que le volume que tu as
posé.

---

## Mise à jour

Il n'y a rien à faire : le code vit dans OneDrive, les améliorations arrivent
automatiquement. Ferme et rouvre l'outil pour en profiter.

Si une nouvelle version demande un composant supplémentaire, relance simplement
`Installer GV_DP.bat`.

---

## En cas de problème

**« Python n'est pas installé »** — reprends l'étape 1 puis relance
l'installateur.

**L'antivirus bloque quelque chose** — signale-le, une exception est peut-être
nécessaire. L'outil n'utilise aucun exécutable compilé, ce cas devrait rester
rare.

**« Site inaccessible »** — la fenêtre noire s'est fermée. Relance le raccourci.

**L'export PDF échoue** — l'outil s'appuie sur PowerPoint, installé avec le pack
Office. Ferme les fenêtres PowerPoint ouvertes et réessaie ; le fichier `.pptx`
reste de toute façon exportable à la main.

**Un projet semble corrompu ou daté** — préviens Florent avant d'intervenir :
OneDrive conserve l'historique des versions, une restauration est possible.
