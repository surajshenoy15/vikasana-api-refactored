# app/main.py — Feature-based FastAPI application

from dotenv import load_dotenv
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(BASE_DIR, ".env"))

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.status import HTTP_422_UNPROCESSABLE_ENTITY
from contextlib import asynccontextmanager

from app.core.config import settings
from app.core.redis import close_redis


# ── Lifespan (startup/shutdown) ──

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    yield
    # Shutdown
    await close_redis()


# ── App Init ──

app = FastAPI(
    title="Vikasana Foundation API",
    description="Backend API for the Vikasana Admin Panel",
    version="2.0.0",
    docs_url="/docs" if settings.DEBUG else None,
    redoc_url="/redoc" if settings.DEBUG else None,
    lifespan=lifespan,
)


# ── Validation Error Handler ──

def _sanitize(obj):
    if isinstance(obj, (bytes, bytearray)):
        return f"<bytes:{len(obj)}>"
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize(v) for v in obj]
    return obj


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    safe_errors = _sanitize(exc.errors())
    return JSONResponse(
        status_code=HTTP_422_UNPROCESSABLE_ENTITY,
        content={"detail": safe_errors},
    )


# ── CORS ──

default_origins = [
    "http://localhost:3000",
    "http://localhost:5173",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:5173",
    "http://31.97.230.171",
    "http://31.97.230.171:3000",
    "http://31.97.230.171:5173",
    "https://31.97.230.171",
    "https://31.97.230.171:3000",
    "https://31.97.230.171:5173",
    "https://vikasana-admin.vercel.app",
]

origins = set(default_origins)
if settings.origins_list:
    origins.update([o.strip() for o in settings.origins_list if o and o.strip()])

app.add_middleware(
    CORSMiddleware,
    allow_origins=list(origins),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Debug Origin Logger ──

@app.middleware("http")
async def log_origin(request: Request, call_next):
    origin = request.headers.get("origin")
    if origin:
        print(f"\U0001f30d ORIGIN: {origin} | PATH: {request.url.path}")
    response = await call_next(request)
    return response


# ═══════════════════════════════════════════════════════════════
# FEATURE-BASED ROUTE REGISTRATION
# Each feature module registers its own routes with appropriate prefixes.
# To extract a module into a microservice, simply move the feature
# folder and re-register its routes in a standalone FastAPI app.
# ═══════════════════════════════════════════════════════════════

# ── Auth ──
from app.features.auth.routes import router as auth_router
from app.features.auth.student_routes import router as student_auth_router
app.include_router(auth_router, prefix="/api")
app.include_router(student_auth_router, prefix="/api")

# ── Faculty ──
from app.features.faculty.routes import router as faculty_router
app.include_router(faculty_router, prefix="/api")

# ── Students ──
from app.features.students.routes import (
    faculty_router as faculty_students_router,
    admin_router as admin_students_router,
    student_router as student_profile_router,
    activity_points_admin_router,
)
app.include_router(faculty_students_router, prefix="/api")
app.include_router(admin_students_router, prefix="/api")
app.include_router(activity_points_admin_router, prefix="/api")
app.include_router(student_profile_router, prefix="/api")

# ── Activities ──
from app.features.activities.routes import (
    router as student_activity_router,
    admin_router as admin_activity_router,
    legacy_router as student_legacy_router,
)
from app.features.activities.summary_routes import router as activity_summary_router
from app.features.activities.types_routes import router as activity_types_router
app.include_router(student_activity_router, prefix="/api")
app.include_router(admin_activity_router, prefix="/api")
app.include_router(activity_summary_router, prefix="/api")
app.include_router(activity_types_router, prefix="/api")

# ── Events (must come before legacy routes) ──
from app.features.events.routes import router as events_router
app.include_router(events_router, prefix="/api")
app.include_router(student_legacy_router, prefix="/api")

# ── Sessions (Admin) ──
from app.features.sessions.routes import router as admin_sessions_router
app.include_router(admin_sessions_router, prefix="/api")

# ── Certificates ──
from app.features.certificates.public_routes import router as public_verify_router
from app.features.certificates.student_routes import router as student_certificates_router
from app.features.certificates.admin_routes import router as admin_certificates_router
app.include_router(public_verify_router, prefix="/api")
app.include_router(student_certificates_router, prefix="/api")
app.include_router(admin_certificates_router, prefix="/api")

# ── Face Recognition ──
from app.features.face.routes import router as face_router
app.include_router(face_router, prefix="/api")

# ── Dashboard ──
from app.features.dashboard.routes import router as admin_dashboard_router
app.include_router(admin_dashboard_router, prefix="/api")

# ── Storage (MinIO Proxy) ──
from app.features.storage.routes import router as public_minio_router
app.include_router(public_minio_router, prefix="/api")


# ── Health Checks ──

@app.get("/", tags=["Health"])
async def root():
    return {
        "status": "ok",
        "app": "Vikasana Foundation API",
        "version": "2.0.0",
        "architecture": "feature-based",
        "env": settings.APP_ENV,
    }


@app.get("/health", tags=["Health"])
async def health():
    return {"status": "healthy"}
