"""
SQLAlchemy model registry.

Celery workers do not load the FastAPI route tree, so all ORM model
modules must be imported explicitly before database-backed tasks execute.

Importing these modules registers their mapped classes with SQLAlchemy.
"""

import app.features.activities.models  # noqa: F401
import app.features.audit.models  # noqa: F401
import app.features.auth.models  # noqa: F401
import app.features.certificates.models  # noqa: F401
import app.features.college_access.models  # noqa: F401
import app.features.events.models  # noqa: F401
import app.features.face.models  # noqa: F401
import app.features.faculty.models  # noqa: F401
import app.features.organization.models  # noqa: F401
import app.features.students.models  # noqa: F401
