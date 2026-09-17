# Instructions pour les agents

## Mode de réponse

- Répondre en français par défaut lorsque la demande est en français.
- Employer un langage direct, précis et dépourvu d'émojis, de remplissage, d'exagération et de formulations destinées à prolonger l'échange.
- Ne pas refléter l'humeur ou le style superficiel de l'utilisateur.
- Ne pas ajouter de motivation, de conclusion douce, d'offre d'aide ou d'appel à l'action après avoir livré l'information demandée.
- Ne poser aucune question lorsque le dépôt et la demande permettent d'agir; expliciter les hypothèses nécessaires et continuer.
- Terminer la réponse immédiatement après le résultat, les limites vérifiées et les validations effectuées.

## Contexte du projet

- Prototype Python qui interroge Gemini pour produire un score de sentiment financier en français compris entre `-1` et `1`.
- `indice_sentiment.py` mesure la latence et exige un score à deux décimales.
- `lien API.py` et `test_gemini.py` sont des scripts de connectivité; `test_gemini.py` n'est pas une suite de tests automatisés.
- Le code est procédural et exécuté au niveau module. Préserver cette simplicité sauf nécessité démontrée.
- Les commentaires et prompts existants sont en français.

## Exécution et dépendances

- Dépendance principale: `google-genai`.
- Installation: `python3 -m pip install google-genai`.
- Exécution: `python3 indice_sentiment.py`, `python3 "lien API.py"` ou `python3 test_gemini.py`.
- Les scripts nécessitent un accès réseau, un interpréteur Python 3 et un modèle Gemini accessible.
- Il n'existe actuellement ni `requirements.txt`, ni `pyproject.toml`, ni suite de tests configurée. Valider au minimum la syntaxe Python et l'exécution ciblée lorsqu'une clé valide est disponible.

## Secrets et intégration Gemini

- Ne jamais ajouter, recopier ou afficher une clé API dans une instruction, un exemple, un log ou une réponse.
- Toute clé codée en dur rencontrée dans le code doit être considérée comme compromise. Pour les modifications futures, lire `GEMINI_API_KEY` depuis l'environnement et signaler la rotation de la clé sans exposer sa valeur.
- Conserver les appels via `genai.Client()` et `client.models.generate_content(...)` sauf changement explicitement requis.
- Ne pas dépendre davantage d'attributs privés du SDK comme `Models._logged_afc_warning`.
- Une réponse Gemini doit être nettoyée et validée avant conversion en `float`; vérifier aussi qu'elle appartient à `[-1, 1]`.

## Validation

- Après chaque modification Python, exécuter au minimum `python3 -m py_compile <fichier>`.
- Ne pas appeler l'API distante dans une prétendue validation unitaire; les scripts actuels effectuent des appels réels et peuvent consommer des quotas.
- Signaler séparément les erreurs dues au réseau, aux identifiants ou au modèle, et les erreurs introduites par le code.