# MediaDown — Téléchargeur de vidéos et audio

Application web full-stack pour télécharger des médias depuis YouTube, Vimeo, SoundCloud, et 1000+ autres plateformes.

## Architecture

```
┌─────────────────┐        ┌──────────────────────────┐
│   React (Vite)  │ ──────▶│   FastAPI (Python)       │
│   Frontend      │  HTTP  │   Backend                │
│   Port 5173     │ ◀───── │   Port 8000              │
└─────────────────┘        │                          │
                           │  yt-dlp  ──▶  /tmp/      │
                           │  ffmpeg  (conversion)     │
                           └──────────────────────────┘
```

**Flux de données :**
1. `POST /api/analyze` → yt-dlp extrait les métadonnées + formats disponibles (sans télécharger)
2. `POST /api/download` → démarre un job en background thread, retourne un `job_id`
3. `GET /api/job/{id}` → polling côté client (toutes les 800ms) pour la progression
4. `GET /api/download/{id}` → `FileResponse` streame le fichier vers le navigateur

## Prérequis

- **Python 3.12+**
- **Node.js 20+**
- **ffmpeg** installé sur le système (pour la conversion audio et la fusion video+audio)

## Démarrage rapide (développement local)

### 1. Backend

```bash
cd backend
python -m venv .venv
source .venv/bin/activate  # Windows : .venv\Scripts\activate
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

### 2. Frontend

```bash
cd frontend
npm install
npm run dev
```

Ouvrez http://localhost:5173

### 3. Via Docker Compose (recommandé)

```bash
# Tout lancer en une commande
docker compose up --build

# Mode dev avec hot-reload frontend
docker compose --profile dev up
```

## Déploiement en production

### Variables d'environnement

| Variable | Défaut | Description |
|----------|--------|-------------|
| `VITE_API_URL` | `""` (même origin) | URL de l'API backend pour le frontend |

### Google Cloud Run

```bash
# Build et push l'image
docker build -t gcr.io/YOUR_PROJECT/mediadown .
docker push gcr.io/YOUR_PROJECT/mediadown

# Déployer
gcloud run deploy mediadown \
  --image gcr.io/YOUR_PROJECT/mediadown \
  --platform managed \
  --allow-unauthenticated \
  --port 8000 \
  --memory 2Gi \
  --timeout 300
```

### Heroku

```bash
heroku create your-app-name
heroku container:push web
heroku container:release web
```

### Nginx (reverse proxy)

```nginx
server {
    listen 80;
    server_name your-domain.com;

    # API backend
    location /api/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_read_timeout 300s;
    }

    # Frontend statique (après `npm run build`)
    location / {
        root /path/to/mediadown/frontend/dist;
        try_files $uri $uri/ /index.html;
    }
}
```

## Structure du projet

```
mediadown/
├── backend/
│   ├── main.py          # API FastAPI complète
│   └── requirements.txt
├── frontend/
│   ├── src/
│   │   ├── App.jsx      # Composant principal React
│   │   ├── App.css      # Styles spécifiques
│   │   ├── index.css    # Variables CSS et reset
│   │   └── main.jsx     # Point d'entrée
│   ├── index.html
│   ├── package.json
│   └── vite.config.js   # Proxy /api → backend
├── Dockerfile
├── docker-compose.yml
└── README.md
```

## Plateformes supportées (via yt-dlp)

YouTube, Vimeo, Dailymotion, SoundCloud, Bandcamp, Twitch, Twitter/X, TikTok, Instagram, Facebook, Reddit, et 1000+ autres.

## Considérations légales

Assurez-vous de respecter les CGU des plateformes et le droit d'auteur applicable dans votre pays. Cet outil est destiné à un usage personnel sur du contenu que vous avez le droit de télécharger.
