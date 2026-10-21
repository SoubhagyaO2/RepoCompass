# Deploying RepoCompass

RepoCompass is two programs: the **API** (Python, `backend/`) and the **web UI** (Node, `frontend/`).
The browser only talks to the UI server; the UI server forwards `/api/*` to the API. So the one
setting that makes a deployment work is the address of the API on the **UI server**:

| Where | Variable | Value |
|---|---|---|
| UI server (runtime) | `REPOCOMPASS_API_URL` | the API's address as the UI server sees it, e.g. `https://repocompass-api.example.com` or `http://backend:8000` |
| API | `GEMINI_API_KEY` | your key (optional: without it the app is evidence-only) |
| API | `REPOCOMPASS_DATA_DIR` | a **persistent** directory (clones and indexes live here) |

The UI reads `REPOCOMPASS_API_URL` when each request arrives, so it can be set or changed in the
host's environment settings without rebuilding. (Earlier versions froze it at build time, and a
deployment that built the UI without the variable answered every request with 502.)

## "Cannot reach the API (The server answered 502)"

That message means the UI loaded but its server could not reach the API. Check in this order:

1. **Is `REPOCOMPASS_API_URL` set on the UI server?** If not, it tries `http://127.0.0.1:8000`, which
   on a host is the UI's own machine. The 502 text now says so ("`REPOCOMPASS_API_URL` is not set").
2. **Is the API up?** Open `<API address>/health`: you should see `{"status":"ok", ...}`. Many free
   tiers stop an idle service; the first request after a sleep can take a minute.
3. **Does the address include the scheme?** `https://host`, not `host`. No trailing path.
4. **Is the API out of memory?** It loads an embedding model and needs roughly 1 GB of RAM or more.
   If the host kills it (look for "out of memory" in its logs) the UI sees 502.
5. **Can the UI server reach the API's network?** Private addresses only work if both run on the
   same private network (for example two services in one `docker compose`).

## What the API needs from its host

- **About 1 GB of RAM or more**, and several GB of disk for the image (it contains torch and the
  embedding model).
- **One instance only.** The local vector store allows one process at a time, so do not run two
  copies against the same data directory or scale the API horizontally.
- **A persistent disk** mounted at `REPOCOMPASS_DATA_DIR` (`/data` in the Docker image). Without it,
  connected repositories disappear when the service restarts.
- **Outbound access to github.com** (to clone public repositories) and, for answers, to the model
  provider.
- **Switch off connecting repositories on anything public:** set
  `REPOCOMPASS_ENABLE_REPO_INDEXING=false`. There are no accounts or per-client rate limits, so
  anyone who can reach the API could otherwise start index jobs (the limits in the README still
  apply).

## Docker Compose (the tested path)

```bash
docker compose up -d        # API on :8000, UI on :3000; the UI reaches the API at http://backend:8000
```

`docker-compose.yml` sets `REPOCOMPASS_API_URL: http://backend:8000` on the UI service. See the
README for indexing a repository first or connecting one from the UI.

## Separate hosts (for example UI on one service, API on another)

1. Deploy the API image (`backend/Dockerfile`) with a persistent disk and the variables above.
2. Check `https://<api host>/health`.
3. Deploy the UI (`frontend/`, `npm run build` then `npm run start`, or its Dockerfile) and set
   `REPOCOMPASS_API_URL=https://<api host>` in its **runtime** environment.
4. Open the UI; the status card should show "Index ready" once a repository is connected.

I have only run the Docker Compose path. The separate-host steps follow from how the UI and API
talk to each other, but I have not run them on any hosting provider.
