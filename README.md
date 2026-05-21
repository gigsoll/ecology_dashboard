# Air Quality Dashboard

This project now includes an interactive frontend in [frontend](/home/tolik/Документи/Лаби/final/frontend) built with React, Vite, Apache ECharts, and Leaflet.

## Frontend

Development:

```bash
cd frontend
npm install
npm run dev
```

The Vite dev server proxies API requests to `http://127.0.0.1:8000`.

Production build:

```bash
cd frontend
npm install
npm run build
```

After the build, FastAPI serves the dashboard at `/dashboard`.
