from functools import lru_cache
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


APP_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """
    All config comes from .env file.
    """

    # ── Database ──
    DATABASE_URL: str
    DATABASE_SYNC_URL: str
    DB_POOL_SIZE: int = 20
    DB_MAX_OVERFLOW: int = 40

    # ── Redis ──
    REDIS_URL: str = "redis://127.0.0.1:6379/0"

    # ── JWT ──
    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60

    # ── CORS ──
    ALLOWED_ORIGINS: str = (
        "http://localhost:5173,"
        "http://127.0.0.1:5173,"
        "http://31.97.230.171:5173,"
        "http://31.97.230.171,"
        "https://31.97.230.171,"
        "https://31.97.230.171:5173"
    )

    # ── App ──
    APP_ENV: str = "production"
    DEBUG: bool = False

    # ── Certificate QR Verification ──
    CERT_SIGNING_SECRET: str
    PUBLIC_BASE_URL: str

    CERT_TEMPLATE_PDF_PATH: str = str(
        APP_DIR / "assets" / "certificate_template.pdf"
    )

    # ── MinIO / S3 ──
    MINIO_ENDPOINT: str = "127.0.0.1:9000"
    MINIO_ACCESS_KEY: str = ""
    MINIO_SECRET_KEY: str = ""
    MINIO_BUCKET: str = "vikasana-faculty"
    MINIO_BUCKET_FACULTY: str = "vikasana-faculty"
    MINIO_BUCKET_ACTIVITIES: str = "activity-uploads"
    MINIO_BUCKET_EVENT_THUMBNAILS: str = "vikasana-event-thumbnails"
    MINIO_FACE_BUCKET: str = "face-verification"
    MINIO_BUCKET_CERTIFICATES: str = "vikasana-certificates"
    MINIO_SECURE: bool = False
    MINIO_PUBLIC_BASE: str = ""

    # ── Email (Brevo / Sendinblue) ──
    SENDINBLUE_API_KEY: str = ""
    EMAIL_FROM: str = "admin@vikasana.org"
    EMAIL_FROM_NAME: str = "Vikasana Foundation"

    # ── Faculty Activation ──
    FRONTEND_BASE_URL: str = "http://localhost:5173"
    ACTIVATION_TOKEN_SECRET: str = "secret"
    ACTIVATION_TOKEN_EXPIRE_HOURS: int = 48

    # ── Celery ──
    CELERY_BROKER_URL: str = "redis://127.0.0.1:6379/1"
    CELERY_RESULT_BACKEND: str = "redis://127.0.0.1:6379/2"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def origins_list(self) -> list[str]:
        return [o.strip() for o in self.ALLOWED_ORIGINS.split(",") if o.strip()]


@lru_cache()
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
