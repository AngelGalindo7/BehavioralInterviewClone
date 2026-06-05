from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Database — point directly to RDS instance endpoint (no proxy for single-user)
    database_url: str
    db_pool_size: int = 5
    db_max_overflow: int = 3
    db_pool_timeout: int = 30

    # OpenAI
    openai_api_key: str
    openai_embedding_model: str = "text-embedding-3-small"
    openai_embedding_dimensions: int = 1536
    openai_response_model: str = "gpt-4o-mini"
    openai_temperature: float = 0.2
    rag_top_k: int = 4
    # OpenAI intermittently parks a request several seconds before its first
    # token even while we're far under rate limits. Cap time-to-first-token and
    # re-fire on a fresh request — the stall is intermittent, so a retry almost
    # always lands fast. Only the FIRST token is bounded; streaming is unbounded
    # once tokens flow. Tune the timeout from prod logs (openai_first_token_*).
    openai_first_token_timeout_s: float = 5.0
    openai_first_token_max_attempts: int = 3

    # ElevenLabs
    elevenlabs_api_key: str
    elevenlabs_voice_id: str
    elevenlabs_model_id: str = "eleven_turbo_v2_5"
    elevenlabs_output_format: str = "pcm_16000"
    # Bound time-to-first-chunk per sentence flush. A parked ElevenLabs request
    # would otherwise freeze the avatar until the session watchdog (~30 min);
    # exceeding this aborts the flush so the turn fails fast and trips the EL
    # circuit breaker. Hang-prevention, not a latency target — keep it well
    # above the normal first-chunk time (turbo is usually < 1 s).
    elevenlabs_first_chunk_timeout_s: float = 6.0
    # When true, synthesise each turn as ONE continuous ElevenLabs generation
    # over the stream-input WebSocket (app/audio/tts_ws.py) instead of one HTTP
    # request per sentence — eliminates inter-sentence prosodic seams. Off by
    # default; the HTTP convert_as_stream path remains the fallback. The pinned
    # SDK (1.9.0) has no WS method, so the protocol is hand-rolled — validate
    # against the live API before enabling in prod.
    tts_use_websocket: bool = False
    elevenlabs_ws_base_url: str = "wss://api.elevenlabs.io"
    # Voice settings — taste knobs, tune by ear in a real session.
    # stability < 0.5 widens prosody range (less monotone) at the cost of more
    # take-to-take variability. style amplifies the voice's natural delivery;
    # leave low if cloned from a flat reference.
    elevenlabs_stability: float = 0.5
    elevenlabs_similarity_boost: float = 0.75
    elevenlabs_style: float = 0.30
    elevenlabs_use_speaker_boost: bool = True
    # Greeting-only overrides for an upbeat opener. Lower stability widens the
    # prosody range; higher style amplifies the cheerful delivery. Applied only
    # by _speak_greeting so interview answers keep the calmer profile above.
    elevenlabs_greeting_stability: float = 0.20
    elevenlabs_greeting_style: float = 0.55

    # Simli — v3 SDK uses /compose/token with these fields
    simli_api_key: str
    simli_face_id: str
    simli_model: str = "fasttalk"
    simli_max_session_length: int = 1800
    simli_max_idle_time: int = 60

    # HeyGen — optional. Only required when avatar_provider="heygen" or the
    # runtime ?provider=heygen toggle is exercised. Voice (incl. 3rd-party
    # ElevenLabs) is bound to the avatar server-side in HeyGen's dashboard,
    # so no voice_id env var is needed here.
    heygen_api_key: str | None = None
    heygen_avatar_id: str | None = None
    heygen_quality: str = "low"

    # Circuit breakers
    cb_failure_threshold: int = 5
    cb_recovery_timeout: float = 30.0

    # App
    candidate_name: str = "Angel"
    pcm_chunk_bytes: int = 6000
    log_level: str = "INFO"
    environment: str = "production"
    avatar_provider: str = "simli"

    # Security
    frontend_origin: str = "http://localhost:5173"
    # Set to "none" (and ensure environment="production") when the frontend is
    # hosted cross-origin (e.g. Vercel). SameSite=None requires Secure=True.
    cookie_samesite: str = "strict"
    max_ws_text_frame_bytes: int = 4096

    # Access gate — single shared passcode + signed-cookie session.
    # Both required (no defaults) so a misconfigured deploy fails loud at startup.
    access_passcode: str
    session_secret: str
    auth_cookie_name: str = "bd_auth"
    auth_cookie_max_age_seconds: int = 60 * 60 * 24 * 30  # 30 days

    # Memory profiling
    tracemalloc_interval_seconds: int = 300

    # LLM → TTS pipelining
    sentence_boundary_max_chars: int = 200
    # First flush of a turn fires on the earlier of (clause boundary, this many
    # chars). Short threshold trades a little prosody on the opener for ~200–400 ms
    # less audible TTFB. Subsequent flushes still wait for sentence boundaries.
    first_flush_min_chars: int = 40

    # Session lifecycle / cost caps.
    # session_max_age_seconds bounds both the server-side WS watchdog and the
    # orphan reaper. Matches simli_max_session_length so an abandoned tab is
    # cleaned up no later than Simli's own session timeout.
    session_max_age_seconds: int = 1800
    session_reaper_interval_seconds: int = 300
    # Hard cap on transcripts per WS session — every accepted transcript bills
    # OpenAI + ElevenLabs even behind the passcode gate. 50 turns ≈ a full 30-min
    # interview at one question per ~35s, with plenty of headroom.
    max_turns_per_session: int = 50
    # Per-turn ceiling on Responses-API output tokens. Bounds blast radius of a
    # runaway generation (looping model, pathological prompt) before the WS-level
    # caps kick in. 1024 ≈ ~750 spoken words — well past any natural answer.
    openai_max_output_tokens: int = 1024

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )


settings = Settings()  # type: ignore[call-arg]
