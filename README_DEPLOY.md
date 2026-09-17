# Déploiement Cloud Run

## 1) Pré-requis

- Installer gcloud CLI
- Créer ou sélectionner un projet Google Cloud
- Activer les APIs : Cloud Run, Cloud Build, Container Registry

```bash
gcloud auth login
gcloud config set project YOUR_PROJECT_ID

gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com
```

## 2) Construire l’image

```bash
docker build -t gcr.io/YOUR_PROJECT_ID/fear-greed-app .
```

## 3) Publier sur Cloud Run

```bash
gcloud run deploy fear-greed-app \
  --image gcr.io/YOUR_PROJECT_ID/fear-greed-app \
  --platform managed \
  --region europe-west1 \
  --allow-unauthenticated \
  --memory 1Gi \
  --cpu 1 \
  --timeout 600
```

## 4) Rendre le site public

Cloud Run peut être public avec `--allow-unauthenticated`.
L’URL publique est alors fournie par Google, par exemple :

```text
https://fear-greed-app-xxxxx-ew.a.run.app
```

## 5) Configurer un domaine personnalisé

Dans la console Cloud Run > Sécurité > Domaines personnalisés.

## 6) SEO / indexation Google

- Créer un domaine avec HTTPS
- Ajouter le site dans Google Search Console
- Créer `robots.txt`
- Créer `sitemap.xml`
- Utiliser des balises `title`, `meta description`, `canonical`, `Open Graph`

Exemple de `robots.txt` :

```text
User-agent: *
Allow: /

Sitemap: https://votre-domaine.com/sitemap.xml
```

Exemple de `sitemap.xml` :

```xml
<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>https://votre-domaine.com/</loc>
  </url>
</urlset>
```

## 7) Bonnes pratiques SEO

- page unique très claire sur le thème
- contenu pertinent et mis à jour régulièrement
- liens internes
- time-to-live du contenu stable
- ajouter `meta` description
- éviter la page “index.html” vide sans contenu texte
