# MasterTheBehavioralInterview

Real-time AI behavioral clone. Interviewer speaks, browser transcribes, backend retrieves anecdotes from a vector store, OpenAI generates the response, ElevenLabs synthesises speech, and Simli renders a talking-head avatar — all within a ~630ms TTFB budget.

## Stack

- **Frontend** — React 18 · TypeScript · Vite · simli-client v3 (WebRTC) · WebSpeech API (Chrome only)
- **Backend** — FastAPI · SQLAlchemy async · asyncpg · Uvicorn (uvloop + httptools)
- **AI** — OpenAI Responses API · `text-embedding-3-small` · ElevenLabs `eleven_turbo_v2_5` · Simli `fasttalk`
- **Database** — PostgreSQL 16 + pgvector with IVFFlat index
- **Infra** — EC2 t4g.small (ARM Graviton2) · RDS db.t3.micro · Nginx · Docker · jemalloc
- **Observability** — Loki + Promtail + Grafana (Docker Compose, ARM)
- **CI/CD** — GitHub Actions → OIDC → SSM Run Command (no SSH, no static AWS keys)

## Layout

```
app/             FastAPI application (api, audio, avatar, core, db, llm, rag)
frontend/        React + Vite SPA
ingestion/       Local CLI for chunk → embed → bulk-insert anecdotes
infra/           Dockerfile assets, Nginx, systemd, IAM, bootstrap scripts
observability/   Loki + Promtail + Grafana compose stack
tests/           Unit + integration suites
.github/         CI and OIDC deploy workflows
```

## Running locally

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env                                  # then fill in API keys
pytest tests/unit/
```

`asyncpg` requires a C compiler on Windows. For local tests on Windows set `DATABASE_URL=sqlite+aiosqlite:///:memory:` and install `aiosqlite`.

## Deploying

Pushes to `main` trigger `.github/workflows/deploy.yml`, which authenticates to AWS via OIDC and runs `infra/scripts/deploy.sh` on the EC2 instance via SSM. No SSH key or AWS credential is stored in GitHub.

First-time AWS setup is in `infra/scripts/setup_oidc.sh`.

## Configuration

All runtime config is environment-driven. See `.env.example` for the full list of variables; the production secrets file lives at `infra/systemd/behavioral-dummy-env` (gitignored — copy from `behavioral-dummy-env.example`).
