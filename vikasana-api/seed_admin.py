"""
seed_admin.py
─────────────
Creates the first admin account with a bcrypt-hashed password.
Run ONCE after the migration:

    python seed_admin.py

Reads from .env — change SEED_ADMIN_* values there, or edit defaults below.
"""
import asyncio
import os
from dotenv import load_dotenv

load_dotenv()

# ── Change these in .env or edit here ────────────────────────────────
ADMIN_NAME     = os.getenv("SEED_ADMIN_NAME",     "Super Admin")
ADMIN_EMAIL    = os.getenv("SEED_ADMIN_EMAIL",    "admin@vikasanafoundation.org")
ADMIN_PASSWORD = os.getenv("SEED_ADMIN_PASSWORD", "ChangeMe@2025")
# ─────────────────────────────────────────────────────────────────────


async def seed():
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
    from sqlalchemy import select
    from app.core.security import hash_password
    from app.features.auth.models import Admin

    engine  = create_async_engine(os.environ["DATABASE_URL"], echo=False)
    Session = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    async with Session() as db:
        # Check if already exists — idempotent
        existing = (await db.execute(
            select(Admin).where(Admin.email == ADMIN_EMAIL)
        )).scalar_one_or_none()

        if existing:
            print(f"⚠️  Admin already exists: {ADMIN_EMAIL}")
            print("   No changes made. To reset password, use the DB directly.")
            await engine.dispose()
            return

        admin = Admin(
            name=ADMIN_NAME,
            email=ADMIN_EMAIL,
            password_hash=hash_password(ADMIN_PASSWORD),
        )
        db.add(admin)
        await db.commit()
        await db.refresh(admin)

    await engine.dispose()

    print("\n✅  Admin created successfully!")
    print(f"    ID    : {admin.id}")
    print(f"    Name  : {admin.name}")
    print(f"    Email : {admin.email}")
    print(f"    Hash  : {admin.password_hash[:40]}...")
    print()
    print("🔑  Login endpoint : POST /api/auth/login")
    print(f'    Body           : {{"email": "{ADMIN_EMAIL}", "password": "{ADMIN_PASSWORD}"}}')
    print()
    print("⚠️   Change the password after first login!")


if __name__ == "__main__":
    asyncio.run(seed())
