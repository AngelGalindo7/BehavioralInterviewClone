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

    # Simli — v3 SDK uses /compose/token with these fields
    simli_api_key: str
    simli_face_id: str
    simli_model: str = "fasttalk"
    simli_max_session_length: int = 1800
    simli_max_idle_time: int = 60

    # Circuit breakers
    cb_failure_threshold: int = 5
    cb_recovery_timeout: float = 30.0

    # App
    candidate_name: str = "Angel"
    pcm_chunk_bytes: int = 6000
    log_level: str = "INFO"
    environment: str = "production"

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

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )


settings = Settings()  # type: ignore[call-arg]
