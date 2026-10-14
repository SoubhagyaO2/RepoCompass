# frontend

Next.js + TypeScript UI. It calls `/api/*`, which Next forwards to the FastAPI backend
(`REPOCOMPASS_API_URL`, default `http://127.0.0.1:8000`; read when the app is built or started in
dev), so the backend needs no CORS configuration.

```bash
npm install
npm run dev                      # http://127.0.0.1:3000
npm run typecheck && npm run build
```

The page reads `GET /api/health`: with an index and a model key it answers; with an index only it
shows the retrieved files and lines; without an index it says what to configure.
