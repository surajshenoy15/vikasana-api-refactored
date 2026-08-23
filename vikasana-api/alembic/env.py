import os
from logging.config import fileConfig

from alembic import context
from dotenv import load_dotenv
from sqlalchemy import engine_from_config, pool
from sqlalchemy.engine import make_url

load_dotenv()

# Import ALL models so Alembic can detect table changes.
from app.core.database import Base

# Feature-based model imports.
from app.features.auth.models import (  # noqa: F401
    Admin,
    StudentOtpSession,
)
from app.features.students.models import Student  # noqa: F401
from app.features.activities.models import (  # noqa: F401
    ActivityFaceCheck,
    ActivityPhoto,
    ActivitySession,
    ActivityType,
    StudentActivityProgress,
    StudentActivityStats,
    StudentPointAdjustment,
)
from app.features.events.models import (  # noqa: F401
    Event,
    EventActivityType,
    EventSubmission,
    EventSubmissionPhoto,
)
from app.features.certificates.models import (  # noqa: F401
    Certificate,
    CertificateCounter,
)
from app.features.faculty.models import (  # noqa: F401
    Faculty,
    FacultyActivationSession,
)
from app.features.organization.models import (  # noqa: F401
    AcademicBatch,
    CollegeOrganizationSetting,
    Department,
    StudentAcademicHistory,
    StudentFacultyAssignment,
)
from app.features.face.models import StudentFaceEmbedding  # noqa: F401
from app.features.audit.models import AuditLog  # noqa: F401


config = context.config

database_url = make_url(
    os.environ["DATABASE_URL"]
)

query = dict(database_url.query)

ssl_value = query.pop(
    "ssl",
    None,
)

if ssl_value:
    query["sslmode"] = ssl_value

sync_database_url = database_url.set(
    drivername="postgresql+psycopg2",
    query=query,
)

config.set_main_option(
    "sqlalchemy.url",
    sync_database_url
    .render_as_string(
        hide_password=False,
    )
    .replace("%", "%%"),
)

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
        config.get_section(
            config.config_ini_section,
            {},
        ),
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