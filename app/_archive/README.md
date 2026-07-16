# Archive — approche Gemini (étape 5) retirée le 2026-07-16

Ces fichiers sont l'ancienne implémentation de l'Insertion IA qui appelait
l'API **Gemini** côté serveur (génération d'image en direct, clé
`GEMINI_API_KEY`). Le plan directeur a été refondu le 2026-07-16 (§6 bis,
« flux figé ») : l'étape 5 est désormais un **générateur de prompt sans API**
(l'utilisateur génère l'image dans son propre ChatGPT).

Conservés en `.txt` pour référence (calage du prompt, garde-fous), non importés.
- `insertion_ia_gemini.py.txt` — module Gemini (generateContent + responseModalities).
- `routes_insertion_gemini.py.txt` — routes upload photo / zone / generer / retoucher.
