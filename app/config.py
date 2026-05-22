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
    rag_top_k: int = 4

    # ElevenLabs
    elevenlabs_api_key: str
    elevenlabs_voice_id: str
    elevenlabs_model_id: str = "eleven_turbo_v2_5"
    elevenlabs_output_format: str = "pcm_16000"
    # Voice settings — taste knobs, tune by ear in a real session.
    # stability < 0.5 widens prosody range (less monotone) at the cost of more
    # take-to-take variability. style amplifies the voice's natural delivery;
    # leave low if cloned from a flat reference.
    elevenlabs_stability: float = 0.35
    elevenlabs_similarity_boost: float = 0.75
    elevenlabs_style: float = 0.30
    elevenlabs_use_speaker_boost: bool = True

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

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )


settings = Settings()  # type: ignore[call-arg]
