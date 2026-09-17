# Fear & Greed Dashboard

Ce projet analyse des articles financiers et économiques pour construire un indice de sentiment Fear & Greed.

## Prérequis

- Python 3.10 ou plus
- pip
- un accès internet pour télécharger les dépendances et le modèle local si nécessaire

## Installation

Depuis le dossier du projet :

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

## Lancer le projet localement

L’analyse utilise par défaut le modèle financier local FinBERT (`ProsusAI/finbert`).
Pour utiliser Gemini volontairement, définir `SENTIMENT_PROVIDER=gemini` avant la collecte.

### Option 1 : app web

```bash
python3 app.py
```

Puis ouvrez dans le navigateur :

```text
http://127.0.0.1:5001
```

### Option 2 : dashboard statique

```bash
python3 open_dashboard.py --days 30 --no-open
```

Le fichier HTML généré est :

```text
fear_greed_chart.html
```

## Structure du projet

- `app.py` : application web Flask
- `fear_greed_index.py` : calcul de l’indice Fear & Greed
- `veille_presse.py` : collecte des articles et gestion des données
- `sentiment.py` : analyse de sentiment
- `sentiments.db` : base SQLite locale
- `requirements.txt` : dépendances Python

## Remarques

- Le projet fonctionne en local sans exposition publique.
- Il est conçu pour un usage privé ou une démonstration ciblée.
- Les modèles de sentiment peuvent télécharger des dépendances automatiquement lors du premier lancement.

## Utilisation rapide

Pour générer le dashboard sans le lancer dans le navigateur :

```bash
python3 open_dashboard.py --days 30 --no-open
```

Pour démarrer l’interface web locale :

```bash
python3 app.py
```

## Dépannage

Si l’installation échoue :

```bash
python -m pip install --upgrade pip setuptools wheel
pip install -r requirements.txt
```

Si un modèle est absent, relancer le projet et laisser le téléchargement se faire.
