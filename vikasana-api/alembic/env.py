import os
from logging.config import fileConfig
from sqlalchemy import engine_from_config, pool
from alembic import context
from dotenv import load_dotenv

load_dotenv()

# Import ALL models so Alembic can detect table changes
from app.core.database import Base

# Feature-based model imports
from app.features.auth.models import Admin  # noqa: F401
from app.features.students.models import Student  # noqa: F401
from app.features.activities.models import (  # noqa: F401
    ActivitySession, ActivityType, ActivityPhoto,
    ActivityFaceCheck, StudentActivityStats,
    StudentActivityProgress, StudentPointAdjustment,
)
from app.features.events.models import Event, EventSubmission, EventSubmissionPhoto, EventActivityType  # noqa: F401
from app.features.certificates.models import Certificate, CertificateCounter  # noqa: F401
from app.features.faculty.models import Faculty, FacultyActivationSession  # noqa: F401
from app.features.face.models import StudentFaceEmbedding  # noqa: F401
from app.features.auth.models import StudentOtpSession  # noqa: F401

config = context.config
config.set_main_option("sqlalchemy.url", os.environ["DATABASE_SYNC_URL"])

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
