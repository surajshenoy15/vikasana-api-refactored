# Vikasana Foundation API — Feature-Based Architecture

## Project Structure

```
vikasana-api/
├── app/
│   ├── main.py                        # FastAPI app + route registration
│   ├── core/                          # Shared infrastructure
│   │   ├── config.py                  # Pydantic settings (from .env)
│   │   ├── database.py                # Async SQLAlchemy engine + session
│   │   ├── redis.py                   # Redis caching, rate limiting, session cache
│   │   ├── security.py                # Password hashing + JWT creation
│   │   ├── jwt.py                     # JWT token utilities
│   │   ├── dependencies.py            # FastAPI auth guards (get_current_admin, etc.)
│   │   ├── email_service.py           # Brevo email sending
│   │   ├── minio_client.py            # MinIO/S3 client
│   │   ├── file_storage.py            # Faculty image upload
│   │   ├── activity_storage.py        # Activity photo upload
│   │   ├── event_thumbnail_storage.py # Event thumbnail presigned URLs
│   │   ├── cert_pdf.py                # Certificate PDF generation
│   │   ├── cert_sign.py               # HMAC signing for QR verification
│   │   ├── cert_storage.py            # Certificate PDF storage in MinIO
│   │   ├── faculty_tokens.py          # Activation token utilities
│   │   └── geo.py                     # Geofence/haversine utilities
│   │
│   ├── features/                      # Feature modules (self-contained)
│   │   ├── auth/                      # Authentication (admin + student OTP)
│   │   │   ├── models.py             # Admin, StudentOtpSession
│   │   │   ├── schemas/              # LoginRequest, LoginResponse, etc.
│   │   │   ├── service.py            # Login logic, token creation
│   │   │   ├── student_auth_service.py
│   │   │   ├── routes.py             # /auth/login, /auth/me, /auth/logout
│   │   │   └── student_routes.py     # /auth/student/request-otp, verify-otp
│   │   │
│   │   ├── students/                  # Student management
│   │   │   ├── models.py             # Student model
│   │   │   ├── schemas/              # StudentCreate, StudentOut, etc.
│   │   │   ├── service.py            # CRUD, CSV import
│   │   │   ├── points_service.py     # Points award/adjustment logic
│   │   │   └── routes.py             # Faculty/Admin/Student profile routes
│   │   │
│   │   ├── activities/                # Activity tracking system
│   │   │   ├── models.py             # ActivitySession, ActivityType, ActivityPhoto, etc.
│   │   │   ├── schemas/              # Session/photo/type schemas
│   │   │   ├── service.py            # Session create/submit/geofence
│   │   │   ├── photos_service.py     # Photo upload with geofence enforcement
│   │   │   ├── summary_service.py    # Student activity summary
│   │   │   ├── routes.py             # Student/admin activity routes
│   │   │   ├── summary_routes.py     # /student/activity/summary
│   │   │   └── types_routes.py       # /activity-types CRUD
│   │   │
│   │   ├── events/                    # Event management
│   │   │   ├── models.py             # Event, EventSubmission, EventSubmissionPhoto
│   │   │   ├── schemas/              # EventCreateIn, EventOut, etc.
│   │   │   ├── service.py            # CRUD, approval, certificate issuance
│   │   │   └── routes.py             # Admin/student event routes
│   │   │
│   │   ├── certificates/             # Certificate system
│   │   │   ├── models.py             # Certificate, CertificateCounter
│   │   │   ├── schemas/              # CertificateVerifyOut, etc.
│   │   │   ├── service.py            # Certificate generation logic
│   │   │   ├── admin_routes.py       # /admin/certificates
│   │   │   ├── student_routes.py     # /student/certificates
│   │   │   └── public_routes.py      # /public/certificates/verify
│   │   │
│   │   ├── dashboard/                 # Admin dashboard
│   │   │   └── routes.py             # /admin/dashboard/stats, etc.
│   │   │
│   │   ├── faculty/                   # Faculty management
│   │   │   ├── models.py             # Faculty, FacultyActivationSession
│   │   │   ├── schemas/              # FacultyCreate, activation schemas
│   │   │   ├── service.py            # Faculty CRUD, activation flow
│   │   │   └── routes.py             # Faculty routes
│   │   │
│   │   ├── face/                      # Face recognition
│   │   │   ├── models.py             # StudentFaceEmbedding
│   │   │   ├── service.py            # OpenCV face detection/matching
│   │   │   ├── checks_service.py     # Face check upsert logic
│   │   │   └── routes.py             # /face/enroll, /face/verify-session
│   │   │
│   │   ├── sessions/                  # Admin session management
│   │   │   ├── schemas/              # AdminSessionListItemOut, etc.
│   │   │   ├── service.py            # List/approve/reject sessions
│   │   │   └── routes.py             # /admin/sessions
│   │   │
│   │   └── storage/                   # MinIO proxy
│   │       └── routes.py             # /public/minio/object
│   │
│   ├── workers/                       # Background workers (Celery)
│   │   ├── celery_app.py             # Celery configuration
│   │   └── tasks.py                  # Certificate PDF gen, email, image processing
│   │
│   └── assets/                        # Static assets (certificate template)
│
├── alembic/                           # Database migrations
│   ├── env.py                         # Alembic config (imports all models)
│   └── versions/                      # Migration files
│       └── 003_add_btree_indexes.py  # B-Tree indexes for performance
│
├── docker/
│   └── nginx.conf                     # Load balancer config
│
├── docker-compose.yml                 # Full deployment stack
├── Dockerfile                         # Container build
├── seed_admin.py                      # Initial admin seeder
├── requirements.txt                   # Python dependencies
└── .env.example                       # Environment template
```

## Key Architecture Decisions

### 1. Feature-Based Structure
Each feature module contains its own routes, services, models, and schemas.
This makes it trivial to extract any module into a standalone microservice.

### 2. Repository Pattern
Database operations are isolated in service layers. Routes call services,
services handle business logic and DB operations via SQLAlchemy async sessions.

### 3. Redis Caching
- Dashboard stats cached for 60s
- Event lists cached for 5 min
- Token validation cached to reduce DB lookups
- Rate limiting per IP/user

### 4. Connection Pooling (2000+ users)
- pool_size=20 (persistent connections)
- max_overflow=40 (burst connections)
- pool_pre_ping=True (stale connection detection)
- pool_recycle=1800 (30-min connection recycling)

### 5. Background Workers (Celery)
Heavy tasks offloaded from request cycle:
- Certificate PDF generation
- Email sending
- Image processing

### 6. B-Tree Indexes
Optimized queries for:
- email lookups (auth)
- student_id + status (activity queries)
- event_date + is_active (event listing)
- submitted_at (sorting)
- Composite indexes for common join patterns

### 7. Deployment
- Docker Compose with 3 API replicas
- Nginx load balancer (least_conn)
- Shared Redis, PostgreSQL, MinIO
- Celery workers for async tasks

## Microservice Extraction Guide

To extract e.g. `auth` as a standalone service:

1. Copy `app/features/auth/` to new project
2. Copy `app/core/` (shared infra)
3. Create standalone `main.py` registering auth routes
4. Deploy with its own database or shared via API gateway
5. Replace internal imports with HTTP/gRPC calls

Same pattern works for: activities, events, certificates, etc.
