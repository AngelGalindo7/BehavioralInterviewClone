# BehavioralDummy

Real-time AI behavioral interview simulator. A browser-captured speech transcript is processed end-to-end — LLM generation, speech synthesis, avatar lip-sync — within a ≤450 ms time-to-first-audio budget.

## Overview

BehavioralDummy is a self-hosted, single-user interview training tool. The candidate speaks into Chrome; the WebSpeech API transcribes each answer and sends it over a WebSocket. The backend streams a response through OpenAI Chat Completions, synthesizes PCM audio via ElevenLabs sentence-by-sentence, and forwards it to a HeyGen LiveAvatar LITE session over a persistent server WebSocket. The avatar renders synchronized video and audio in the browser via a LiveKit room.

Deployment target: EC2 t4g.small (2 vCPU ARM Graviton2, 2 GiB RAM) with RDS db.t3.micro for session and turn storage.

> **Browser requirement:** Chrome only (WebSpeech API — `window.webkitSpeechRecognition` is undefined in Firefox and Safari).

## Architecture

```
Browser (Chrome)
├── WebSpeech API                  → transcript text
├── /ws/interview ←────────────────────────── binary PCM (1-byte prefix)
│    └── FastAPI / Uvicorn (uvloop + httptools, 1 worker)
│         ├── OpenAI Chat Completions (gpt-4o-mini, streaming)
│         │    └── token deltas → sentence-boundary buffer
│         ├── ElevenLabs eleven_turbo_v2_5
│         │    └── PCM16 @ 24 kHz, convert_as_stream per sentence flush
│         │         └── chunker → /ws/interview binary send
│         └── HeyGen LiveAvatar LITE  (server WebSocket, base64 PCM)
│              └── LiveKit room
└── livekit-client                 ← avatar video + audio
```

## Latency Model

Target: ≤450 ms from first transcript byte received to first avatar audio frame.

| Stage | Typical | Notes |
|---|---|---|
| OpenAI first token | ~300 ms | Retried up to 3× on stall (5 s cap per attempt) |
| ElevenLabs first PCM chunk | ~150 ms | 40-char eager first flush; subsequent flushes at sentence boundaries |
| HeyGen LiveAvatar ingest | ~0 ms (paced) | Server WS, paced at playback rate + 0.5 s lead buffer |
| **Total TTFB** | **~450 ms** | LLM start → first avatar audio |

Pre-pipeline STT latency (Chrome end-of-speech detector, ~700–1200 ms) sits outside this budget. A 600 ms post-interim silence fallback timer and a "Send now" affordance on the Stop button reduce perceived end-of-utterance delay.

## Stack

| Layer | Technology |
|---|---|
| Frontend | React 18 · TypeScript · Vite · livekit-client 2.7 · WebSpeech API |
| Backend | FastAPI 0.115 · SQLAlchemy 2 async · asyncpg 0.30 · Uvicorn (uvloop + httptools) |
| LLM | OpenAI Chat Completions · gpt-4o-mini · first-token retry + circuit breaker |
| TTS | ElevenLabs eleven_turbo_v2_5 · PCM16 @ 24 kHz · sentence-boundary flushing |
| Avatar | HeyGen LiveAvatar LITE · `audio_pcm_server` mode · LiveKit room (active) · Simli WebRTC opt-in, not maintained |
| Database | PostgreSQL 16 + pgvector (IVFFlat index, dormant) · RDS db.t3.micro |
| Infra | EC2 t4g.small (ARM Graviton2) · Nginx · Docker multi-stage · jemalloc |
| Observability | Loki + Promtail + Grafana (Docker Compose, ARM) |
| CI/CD | GitHub Actions → AWS OIDC → SSM Run Command (no static credentials) |

## Project Layout

```
app/                     FastAPI application
  api/
    auth.py              POST /auth/login, /auth/logout, GET /auth/check
    avatar.py            GET /avatar/providers, POST /avatar/session
    health.py            GET /health (liveness), GET /ready (DB readiness)
    session.py           POST /session/, DELETE /session/{id}
    admin.py             GET/PUT /admin/stories (corpus management)
    ws_interview.py      WebSocket /ws/interview — LLM → TTS → avatar pipeline
  audio/
    tts.py               ElevenLabs HTTP TTS (convert_as_stream + history deletion)
    tts_ws.py            ElevenLabs stream-input WebSocket (experimental, disabled)
    chunker.py           PCM frame batching
  avatar/
    base.py              AvatarSessionProvider ABC (audio_pcm / text / audio_pcm_server)
    providers/
      heygen.py          HeyGen LiveAvatar LITE (audio_pcm_server, production)
      simli.py           Simli WebRTC (audio_pcm, decommissioned — code preserved)
  core/
    auth.py              AccessPasscodeMiddleware + itsdangerous signed cookies
    circuit_breaker.py   3-state circuit breaker (CLOSED → OPEN → HALF_OPEN)
    exceptions.py        BehavioralDummyError hierarchy
    lifespan.py          Startup: DB check, story load, client pre-warm; orphan reaper task
  db/
    models.py            AppSetting, Anecdote, InterviewSession, Turn (SQLAlchemy ORM)
    engine.py            Async engine (asyncpg / aiosqlite fallback for Windows CI)
    migrations/          Alembic revisions (0001 initial, 0002 backfill, 0003 app_settings)
  llm/
    responder.py         OpenAI Chat Completions with first-token retry + circuit breaker
  rag/
    retriever.py         IVFFlat cosine search (dormant)
    embedder.py          OpenAI text-embedding-3-small (dormant)
    prompt_builder.py    System prompt template + in-memory stories cache

frontend/                React + Vite SPA (static, served by Nginx)
  src/
    lib/
      wsClient.ts        InterviewWebSocket (JSON + binary PCM protocol)
      heygenAvatar.ts    HeyGen LiveAvatar provider (livekit-client)
      simliAvatar.ts     Simli WebRTC provider (decommissioned — code preserved)
      speechRecognition.ts  WebSpeech API wrapper with silence-fallback timer
      timing.ts          TTFB waterfall instrumentation (turn_stage_timing log event)
      activeAvatar.ts    Runtime avatar selection
      avatarProvider.ts  AvatarProvider interface
    components/
      InterviewPage.tsx  Main interview UI (avatar, record, session lifecycle)
      AvatarView.tsx     <video>/<audio> element + state overlay
      RecordButton.tsx   Record / stop / skip controls
      StatusBar.tsx      WS status and error display
      AdminPage.tsx      Story editor (GET/PUT /admin/stories)

ingestion/               CLI: chunk → embed → bulk-insert anecdotes (dormant at query time)
data/
  stories.md             Interview corpus loaded at startup (provide at deploy time)
infra/
  nginx/                 Reverse proxy config (SSL, WS upgrade, static SPA)
  iam/                   GitHub OIDC trust policy + EC2 instance IAM policy
  scripts/
    deploy.sh            SSM-triggered deploy script
    bootstrap_ec2.sh     First-time EC2 instance setup
    setup_oidc.sh        GitHub OIDC trust policy bootstrap
    setup_swap.sh        Swap file setup (t4g.small memory pressure)
observability/
  docker-compose.yml     Loki + Promtail + Grafana stack
tests/
  unit/                  Circuit breaker, responder, TTS HTTP/WS, chunker, prompt, pacing, log contract
  integration/           Full WebSocket interview flow (mocked OpenAI/ElevenLabs)
.github/workflows/
  ci.yml                 Lint · type-check · unit tests · integration tests · frontend build
  deploy.yml             Gate on unit tests → OIDC auth → SSM send-command → deploy.sh
```

## WebSocket Protocol

All real-time interview communication runs over `GET /ws/interview?session_id=<uuid>`. The connection requires an authenticated session cookie.

### Client → Server (JSON text frames, max 4 096 bytes)

| Type | Payload | Behavior |
|---|---|---|
| `greeting` | — | Triggers static opener via ElevenLabs (no LLM call) |
| `transcript` | `"text": "<answer>"` | Starts full LLM → TTS → avatar pipeline |
| `skip` | — | Interrupts current avatar utterance server-side |

### Server → Client (binary frames)

Every binary frame carries a 1-byte prefix followed by raw PCM16 mono audio:

| Prefix | Hex | Meaning |
|---|---|---|
| `PLAY_IMMEDIATE` | `0x01` | First chunk of each utterance — bypass avatar jitter buffer |
| `PLAY_BUFFERED` | `0x00` | Subsequent chunks — standard buffered ingest |

Closing the WebSocket mid-turn propagates a `CancelledError` that aborts the in-flight OpenAI and ElevenLabs streams, stopping billing immediately.

## Authentication

A single shared passcode gates the entire application. On `POST /auth/login`, the backend validates the passcode and issues an `itsdangerous` signed cookie. The `AccessPasscodeMiddleware` checks the cookie on every request except `/health`, `/ready`, and `/auth/*`.

No multi-user access control. The cookie is valid for 30 days (`AUTH_COOKIE_MAX_AGE_SECONDS`).

## Configuration

All runtime configuration is environment-variable-driven. Copy `.env.example` to `.env` for local development. The production secrets file lives at `infra/systemd/behavioral-dummy-env` (gitignored — copy from `behavioral-dummy-env.example`).

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | required | PostgreSQL DSN (`asyncpg` driver; use `sqlite+aiosqlite:///` for local tests) |
| `DB_POOL_SIZE` | `5` | SQLAlchemy async connection pool size per worker |
| `DB_MAX_OVERFLOW` | `3` | Overflow connections above pool size |
| `OPENAI_API_KEY` | required | Chat Completions key |
| `OPENAI_RESPONSE_MODEL` | `gpt-4o-mini` | LLM model |
| `OPENAI_TEMPERATURE` | `0.2` | LLM sampling temperature |
| `OPENAI_MAX_OUTPUT_TOKENS` | `1024` | Per-turn output ceiling (~750 spoken words) |
| `OPENAI_FIRST_TOKEN_TIMEOUT_S` | `5.0` | Seconds before a stalled first token triggers retry |
| `OPENAI_FIRST_TOKEN_MAX_ATTEMPTS` | `3` | Max retry attempts for stalled first-token |
| `ELEVENLABS_API_KEY` | required | TTS API key |
| `ELEVENLABS_VOICE_ID` | required | Cloned voice ID |
| `ELEVENLABS_MODEL_ID` | `eleven_turbo_v2_5` | TTS model |
| `ELEVENLABS_OUTPUT_FORMAT` | `pcm_16000` | PCM format (`pcm_24000` required for LiveAvatar) |
| `ELEVENLABS_FIRST_CHUNK_TIMEOUT_S` | `6.0` | Hang-prevention timeout per sentence flush |
| `ELEVENLABS_STABILITY` | `0.5` | Voice stability (lower = wider prosody range) |
| `ELEVENLABS_STYLE` | `0.30` | Style amplification |
| `TTS_USE_WEBSOCKET` | `false` | Enable stream-input WS path (experimental — see Dormant Subsystems) |
| `HEYGEN_API_KEY` | required | LiveAvatar API key |
| `HEYGEN_AVATAR_ID` | required | LiveAvatar avatar ID |
| `HEYGEN_QUALITY` | `low` | Render quality (`low` / `medium` / `high`) |
| `LIVEAVATAR_PACING_LEAD_S` | `0.5` | Seconds of audio lead ahead of real-time for the avatar feed |
| `CANDIDATE_NAME` | `Angel` | Injected into the system prompt |
| `ACCESS_PASSCODE` | required | Single shared passcode for the UI |
| `SESSION_SECRET` | required | Cookie signing key (minimum 32 characters) |
| `AVATAR_PROVIDER` | `heygen` | Avatar provider name |
| `MAX_TURNS_PER_SESSION` | `50` | Hard cap on transcripts per WebSocket session |
| `SESSION_MAX_AGE_SECONDS` | `1800` | Session lifetime (30 min); enforced by WS watchdog + orphan reaper |
| `PCM_CHUNK_BYTES` | `6000` | PCM frame size per WebSocket binary send |
| `CB_FAILURE_THRESHOLD` | `5` | Consecutive failures before a circuit breaker opens |
| `CB_RECOVERY_TIMEOUT` | `30.0` | Seconds in OPEN state before HALF_OPEN probe |
| `FIRST_FLUSH_MIN_CHARS` | `40` | Minimum chars before the first TTS flush of a turn fires |
| `LOG_LEVEL` | `INFO` | Logging verbosity |
| `ENVIRONMENT` | `production` | Affects `Secure` cookie flag and CORS behavior |

## Local Development

### Prerequisites

- Python 3.12+
- Node.js 20+ (frontend build only — not needed at runtime)
- PostgreSQL 16 with `pgvector` extension (or use the SQLite fallback below)
- A C compiler for `asyncpg` (Windows: Visual C++ Build Tools, or use SQLite fallback)

### Setup

```bash
# Backend
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env             # fill in API keys

# Start backend
uvicorn app.main:app --reload --port 8000
```

```bash
# Frontend (separate terminal)
cd frontend
npm install
npm run dev                      # Vite dev server on http://localhost:5173
```

The Vite dev server proxies `/api`, `/ws`, `/auth`, `/avatar`, `/session`, `/admin`, `/health`, and `/ready` to `localhost:8000`.

### Windows / SQLite fallback

`asyncpg` requires a C compiler on Windows. For local development and unit tests without a PostgreSQL instance:

```bash
pip install aiosqlite
# .env:
DATABASE_URL=sqlite+aiosqlite:///:memory:
```

The SQLite backend supports all tests except pgvector queries (RAG integration tests), which are automatically skipped when the driver is not `asyncpg`.

## Running Tests

```bash
# Unit tests (no live DB required with SQLite fallback)
pytest tests/unit/ -v

# Integration tests (requires a live PostgreSQL + pgvector instance)
pytest tests/integration/ -v

# Full suite with coverage
pytest --cov=app --cov-report=term-missing

# Type checking
mypy app

# Lint
ruff check app tests
```

### Test Coverage

| File | Covers |
|---|---|
| `test_circuit_breaker.py` | CLOSED → OPEN → HALF_OPEN state machine; probe guard against stampede |
| `test_responder.py` | First-token retry loop, timeout, stream consumption without re-entry |
| `test_tts.py` | ElevenLabs history deletion queue, LATEST sentinel fallback |
| `test_tts_ws.py` | Hand-rolled stream-input WS protocol (BOS / text / EOS / PCM chunks) |
| `test_ws_pacing.py` | LiveAvatar pacing delay calculation (`_pacing_delay_s`) |
| `test_prompt_builder.py` | System prompt formatting with candidate name and stories corpus |
| `test_chunker.py` | PCM frame batching and re-assembly at `pcm_chunk_bytes` boundaries |
| `test_avatar_api.py` | Provider selection endpoint, 400 on unknown/unregistered provider |
| `test_session_api.py` | Session create / end / idempotent delete |
| `test_health_api.py` | Liveness probe (always 200), readiness probe (DB connectivity) |
| `test_log_contract.py` | Structured JSON log schema compliance |
| `test_ws_interview.py` | Full transcript → binary PCM flow; skip handling; mid-turn cancellation |

## Deployment

### Automated (push to `main`)

`deploy.yml` runs on every push to `main`:

1. Unit tests gate the deploy — no tests, no ship.
2. Authenticates to AWS via GitHub OIDC (short-lived credentials, no secrets stored in GitHub).
3. Issues `ssm:SendCommand` on the EC2 instance ID stored in GitHub environment variables.
4. `deploy.sh` runs as the `ubuntu` user on the instance:
   - `git fetch && git reset --hard origin/main`
   - `npm ci && npm run build` — outputs `frontend/dist/`
   - `rsync` dist to the Nginx web root
   - `docker build -t behavioral-dummy:latest .` — multi-stage build
   - `docker compose -f docker-compose.app.yml up -d` — rolling replace
   - `alembic upgrade head` — applies any pending migrations
   - Health-check poll (12 × 5 s against `GET /health`)
   - `nginx -s reload`
5. Deploy status is polled via `ssm:GetCommandInvocation`.

### First-Time Setup

AWS OIDC trust policy and IAM role: `infra/scripts/setup_oidc.sh` and `infra/iam/`.

EC2 instance bootstrap: `infra/scripts/bootstrap_ec2.sh`.

### Manual / Local Docker

```bash
docker build -t behavioral-dummy:latest .
docker compose -f docker-compose.app.yml up
```

The image uses `LD_PRELOAD` for jemalloc (`/usr/lib/aarch64-linux-gnu/libjemalloc.so.2` on ARM64 Debian) to prevent allocator fragmentation during sustained PCM streaming.

## Observability

Loki, Promtail, and Grafana run as a separate Docker Compose stack on the same EC2 instance:

```bash
cd observability
docker compose up -d
```

Grafana is served at `/grafana/` via the Nginx reverse proxy. Promtail scrapes the Docker `journald` logging driver; all structured JSON log lines appear as Loki streams.

Key structured log events: `tts_stream_start`, `tts_first_chunk_timeout`, `openai_first_token_timeout`, `openai_first_token_retry_succeeded`, `circuit_opened`, `circuit_closed`, `circuit_failure`, `liveavatar_session_created`, `stories_loaded_from_db`.

## Dormant Subsystems

These components exist in the codebase but are not active in production.

| Subsystem | Location | Disabled | Re-enable conditions |
|---|---|---|---|
| RAG / pgvector retrieval | `app/rag/`, `ingestion/` | 05/05/2026 | Corpus exceeds ~400 entries; `lists=sqrt(N)` becomes meaningful. Uncomment embed/retrieve calls in `ws_interview.py` and warm the IVFFlat index at startup. |
| ElevenLabs stream-input TTS | `app/audio/tts_ws.py` | Experimental | One generation per turn eliminates inter-sentence prosodic seams. Hand-rolled against pinned SDK 1.9.0 — validate live API behavior before enabling (`TTS_USE_WEBSOCKET=true`). |
| Simli WebRTC avatar | `app/avatar/providers/simli.py`, `frontend/src/lib/simliAvatar.ts` | Not maintained (04/06/2026) | Opt-in: set `SIMLI_API_KEY` + `SIMLI_FACE_ID` + `AVATAR_PROVIDER=simli`. Code is complete but untested against the current WS pipeline — re-validate `_drain_and_pace` and PCM pacing before production use. |

The `anecdotes` table, IVFFlat index, and `pgvector` extension remain in place in the database schema (no migration was issued to drop them). Ingestion tooling (`ingestion/ingest.py`) is fully functional as a CLI for bulk-loading stories into the vector store.

## Architecture Decisions

Non-obvious decisions — including alternatives rejected and their tradeoffs — are in [`docs/DECISION_LOG.md`](docs/DECISION_LOG.md).

Highlights:

| Decision | Rationale |
|---|---|
| Chat Completions over Responses API | Measured +5 s TTFB from Responses API at this prompt size on the prod box |
| Sentence-boundary TTS flushing | Pipelines LLM tokens into TTS before the full response is ready; 40-char eager first flush shaves ~200–400 ms |
| `_drain_and_pace` queue | Decouples ElevenLabs drain rate from LiveAvatar send rate; upstream never throttled, pacing only at the avatar boundary |
| IVFFlat over HNSW | HNSW RAM cost causes disk thrashing on db.t3.micro |
| Static corpus over live RAG | Full stories corpus (~60 entries, ~12k tokens) fits in GPT-4o-mini's context window; removes 180 ms of per-turn embed+retrieve overhead |
| No CloudWatch | Verbose AI payloads exhaust the free-tier ingestion budget; Loki indexes labels only |
| No faster-whisper on EC2 | large-v3 int8 uses ~1.5 GiB RAM — impossible alongside Uvicorn on a 2 GiB instance |
| jemalloc via `LD_PRELOAD` | Prevents glibc allocator fragmentation from long-running PCM streaming on ARM |
| 1 Uvicorn worker | t4g.small RAM limit; two workers + jemalloc overhead would trigger swap thrashing |

## Hard Constraints

These constraints are enforced by project convention (see `CLAUDE.md`):

- **No HNSW index** — RAM cost on db.t3.micro causes disk thrashing. Always IVFFlat.
- **No AWS Lambda** — sessions are long-lived (30 min); Lambda billing is catastrophic.
- **No CloudWatch logging** — verbose AI payloads exhaust the free tier. All logs → Loki.
- **No backend audio transcoding** — never run ffmpeg/pydub on PCM. Always request `pcm_*` directly from ElevenLabs.
- **No faster-whisper on EC2** — STT runs in the browser (WebSpeech API). EC2 has no capacity.
- **No ZDR SDK flag on ElevenLabs Creator tier** — `enable_logging=False` is silently ignored. Use active history deletion via `history_delete_worker` instead.
