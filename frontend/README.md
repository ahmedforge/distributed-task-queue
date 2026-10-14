# DistribuQueue dashboard

React + TypeScript + Vite dashboard for the PDF task queue. It includes upload, paginated/filterable job lists, live progress with reconnect/polling fallback, failed-job retry, ZIP downloads, terminal-job deletion, charts, and theme preferences.

## Run the full app

From the repository root:

```bash
docker compose -f docker-compose.yml -f docker-compose.monitoring.yml up --build -d
```

Open http://localhost:3000. The built frontend runs in an unprivileged Nginx container; `/api/` REST and WebSocket traffic proxies to the API container. Set `FRONTEND_PORT` in the root `.env` if port 3000 is occupied.

## Develop with live reload

Requires Node.js 22 and npm. Start the backend from the repository root, then run:

```bash
cd frontend
npm ci
npm run dev
```

The development proxy targets http://localhost:8001. Stop the Docker frontend before using Vite on port 3000. `npm run lint` runs Oxlint; `npm run build` typechecks and creates the production bundle. See the root README for backend setup, metrics, and limitations.
