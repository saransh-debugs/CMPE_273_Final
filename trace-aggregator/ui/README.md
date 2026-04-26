# Trace Aggregator UI

React + Vite dashboard for the Distributed Trace Aggregator.

## Run

Requires Node 18+.

```bash
cd ui
npm install
npm run dev
```

Open http://localhost:5173. The dev server proxies `/api/*` to the FastAPI server on `:8000`, so make sure the API is running first (`uvicorn api.main:app --reload --port 8000` from the repo root).

## Build

```bash
npm run build       # outputs to dist/
npm run preview     # serves the build locally
```

## Stack

- **React 18** + **TypeScript** + **Vite**
- **Tailwind CSS** with a custom palette + fonts (no @apply soup)
- **framer-motion** for staggered reveals
- **react-router-dom** for navigation

## Pages

- `/` — Trace list with filters (all / errors / clean)
- `/traces/:id` — Trace detail: stats, **TimelineWaterfall**, **DAGView**, **BlamePanel**
- `/blame` — Cross-trace agent leaderboard

## Design notes

- Palette: parchment-on-charcoal with a single cherry-red accent for blame and errors.
- Fonts: **Fraunces** (display, italics for headlines), **JetBrains Mono** (data/IDs), **Manrope** (UI chrome).
- All tokens live in `tailwind.config.js` — change them once and the whole app shifts.
- The Timeline is a custom SVG (no charting library) so it scales to thousands of spans without breaking a sweat.
