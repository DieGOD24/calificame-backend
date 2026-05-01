import os

from loguru import logger
from pydantic import field_validator
from pydantic_settings import BaseSettings

# Stable default key for development — production MUST override via env
_DEV_SECRET_KEY = "dev-only-secret-key-override-in-production"

# Sentinel: any of these are known weak/default values that must never run in prod
_WEAK_SECRET_KEYS = frozenset(
    [
        _DEV_SECRET_KEY,
        "your-secret-key-change-in-production",
        "changeme",
        "secret",
        "secret-key",
        "default",
    ]
)


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    DATABASE_URL: str = "sqlite:///./calificame.db"
    SECRET_KEY: str = _DEV_SECRET_KEY
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60

    OPENAI_API_KEY: str = ""
    AI_MODEL: str = "gpt-4.1"

    STORAGE_TYPE: str = "local"
    STORAGE_LOCAL_PATH: str = "./uploads"

    MINIO_ENDPOINT: str = "localhost:9000"
    MINIO_ACCESS_KEY: str = "minioadmin"
    MINIO_SECRET_KEY: str = "minioadmin"
    MINIO_BUCKET: str = "calificame"
    MINIO_SECURE: bool = False

    CORS_ORIGINS: list[str] = ["http://localhost:3000"]
    CORS_METHODS: list[str] = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
    CORS_HEADERS: list[str] = ["Authorization", "Content-Type", "Accept"]

    MAX_FILE_SIZE_MB: int = 50
    ALLOWED_UPLOAD_EXTENSIONS: list[str] = [".pdf", ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tiff"]

    LOG_LEVEL: str = "INFO"

    # Rate limiting
    RATE_LIMIT_AUTH: str = "5/minute"
    RATE_LIMIT_UPLOAD: str = "10/minute"
    RATE_LIMIT_AI: str = "3/minute"
    RATE_LIMIT_DEFAULT: str = "60/minute"

    # Password policy
    PASSWORD_MIN_LENGTH: int = 8
    PASSWORD_REQUIRE_UPPERCASE: bool = True
    PASSWORD_REQUIRE_DIGIT: bool = True

    @field_validator("SECRET_KEY")
    @classmethod
    def secret_key_must_be_set(cls, v: str) -> str:
        if len(v) < 16:
            raise ValueError("SECRET_KEY must be at least 16 characters")
        # In production, reject any known weak/default value
        is_production = os.environ.get("ENV", "").lower() in ("prod", "production")
        if is_production and v in _WEAK_SECRET_KEYS:
            raise ValueError(
                "SECRET_KEY is a known default value — set a strong unique secret "
                "via the SECRET_KEY environment variable before running in production."
            )
        return v

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


settings = Settings()

# Warn if using dev key or missing OpenAI key
if settings.SECRET_KEY == _DEV_SECRET_KEY:
    logger.warning("Using development SECRET_KEY — set a secure key in production!")

if not settings.OPENAI_API_KEY:
    logger.warning("OPENAI_API_KEY not set — AI features (OCR, grading) will fail!")
