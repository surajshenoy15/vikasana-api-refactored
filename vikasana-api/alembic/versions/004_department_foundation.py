"""Add department and batch architecture foundation.

Revision ID: 004_department_foundation
Revises: a00fa6471be0
"""

from alembic import op
import sqlalchemy as sa


revision = "004_department_foundation"
down_revision = "a00fa6471be0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "college_organization_settings",
        sa.Column(
            "id",
            sa.Integer(),
            primary_key=True,
            autoincrement=True,
        ),
        sa.Column(
            "college",
            sa.String(length=200),
            nullable=False,
        ),
        sa.Column(
            "department_architecture_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "academic_year_start_month",
            sa.SmallInteger(),
            nullable=False,
            server_default=sa.text("7"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "academic_year_start_month BETWEEN 1 AND 12",
            name="ck_college_org_academic_year_start_month",
        ),
    )

    op.create_index(
        "ix_college_organization_settings_college",
        "college_organization_settings",
        ["college"],
    )

    op.execute(
        """
        CREATE UNIQUE INDEX
            uq_college_organization_settings_college_ci
        ON college_organization_settings (
            LOWER(TRIM(college))
        )
        """
    )

    # Departments managed dynamically inside each college
    op.create_table(
        "departments",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("college", sa.String(length=200), nullable=False),
        sa.Column("name", sa.String(length=150), nullable=False),
        sa.Column("code", sa.String(length=30), nullable=False),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "created_by_faculty_id",
            sa.Integer(),
            sa.ForeignKey("faculty.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_by_admin_id",
            sa.Integer(),
            sa.ForeignKey("admins.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    op.create_index(
        "ix_departments_college",
        "departments",
        ["college"],
    )

    op.create_index(
        "ix_departments_college_active",
        "departments",
        ["college", "is_active"],
    )

    op.execute(
        """
        CREATE UNIQUE INDEX uq_departments_college_name_ci
        ON departments (
            LOWER(TRIM(college)),
            LOWER(TRIM(name))
        )
        """
    )

    op.execute(
        """
        CREATE UNIQUE INDEX uq_departments_college_code_ci
        ON departments (
            LOWER(TRIM(college)),
            LOWER(TRIM(code))
        )
        """
    )

    # College-wide academic batches
    op.create_table(
        "academic_batches",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("college", sa.String(length=200), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("admitted_year", sa.Integer(), nullable=False),
        sa.Column("passout_year", sa.Integer(), nullable=False),
        sa.Column(
            "course_duration_years",
            sa.SmallInteger(),
            nullable=False,
            server_default=sa.text("4"),
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "created_by_faculty_id",
            sa.Integer(),
            sa.ForeignKey("faculty.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_by_admin_id",
            sa.Integer(),
            sa.ForeignKey("admins.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "passout_year > admitted_year",
            name="ck_academic_batches_year_order",
        ),
        sa.CheckConstraint(
            "course_duration_years BETWEEN 1 AND 8",
            name="ck_academic_batches_duration",
        ),
    )

    op.create_index(
        "ix_academic_batches_college",
        "academic_batches",
        ["college"],
    )

    op.create_index(
        "ix_academic_batches_college_active",
        "academic_batches",
        ["college", "is_active"],
    )

    op.execute(
        """
        CREATE UNIQUE INDEX uq_academic_batches_identity_ci
        ON academic_batches (
            LOWER(TRIM(college)),
            admitted_year,
            passout_year,
            course_duration_years
        )
        """
    )

    # Faculty organization scope
    op.add_column(
        "faculty",
        sa.Column("department_id", sa.Integer(), nullable=True),
    )

    op.add_column(
        "faculty",
        sa.Column(
            "legacy_college_scope",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )

    op.create_foreign_key(
        "fk_faculty_department_id",
        "faculty",
        "departments",
        ["department_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # Existing faculty stay true; future faculty default to false
    op.alter_column(
        "faculty",
        "legacy_college_scope",
        existing_type=sa.Boolean(),
        nullable=False,
        server_default=sa.text("false"),
    )

    op.create_index(
        "ix_faculty_college_department_role",
        "faculty",
        ["college", "department_id", "role"],
    )

    # Current student organization state
    op.add_column(
        "students",
        sa.Column("department_id", sa.Integer(), nullable=True),
    )

    op.add_column(
        "students",
        sa.Column("batch_id", sa.Integer(), nullable=True),
    )

    op.add_column(
        "students",
        sa.Column("current_year", sa.SmallInteger(), nullable=True),
    )

    op.add_column(
        "students",
        sa.Column("assigned_faculty_id", sa.Integer(), nullable=True),
    )

    op.create_foreign_key(
        "fk_students_department_id",
        "students",
        "departments",
        ["department_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_foreign_key(
        "fk_students_batch_id",
        "students",
        "academic_batches",
        ["batch_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_foreign_key(
        "fk_students_assigned_faculty_id",
        "students",
        "faculty",
        ["assigned_faculty_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_check_constraint(
        "ck_students_current_year",
        "students",
        "current_year IS NULL OR current_year BETWEEN 1 AND 8",
    )

    op.create_index(
        "ix_students_department_id",
        "students",
        ["department_id"],
    )

    op.create_index(
        "ix_students_batch_id",
        "students",
        ["batch_id"],
    )

    op.create_index(
        "ix_students_assigned_faculty_id",
        "students",
        ["assigned_faculty_id"],
    )

    op.create_index(
        "ix_students_college_department_batch",
        "students",
        ["college", "department_id", "batch_id"],
    )

    # Promotion, demotion and academic audit history
    op.create_table(
        "student_academic_history",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "student_id",
            sa.Integer(),
            sa.ForeignKey("students.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("action", sa.String(length=30), nullable=False),
        sa.Column(
            "from_department_id",
            sa.Integer(),
            sa.ForeignKey("departments.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "to_department_id",
            sa.Integer(),
            sa.ForeignKey("departments.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "from_batch_id",
            sa.Integer(),
            sa.ForeignKey("academic_batches.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "to_batch_id",
            sa.Integer(),
            sa.ForeignKey("academic_batches.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("from_year", sa.SmallInteger(), nullable=True),
        sa.Column("to_year", sa.SmallInteger(), nullable=True),
        sa.Column(
            "academic_session",
            sa.String(length=20),
            nullable=True,
        ),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "changed_by_faculty_id",
            sa.Integer(),
            sa.ForeignKey("faculty.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "changed_by_admin_id",
            sa.Integer(),
            sa.ForeignKey("admins.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "from_year IS NULL OR from_year BETWEEN 1 AND 8",
            name="ck_student_history_from_year",
        ),
        sa.CheckConstraint(
            "to_year IS NULL OR to_year BETWEEN 1 AND 8",
            name="ck_student_history_to_year",
        ),
    )

    op.create_index(
        "ix_student_academic_history_student_created",
        "student_academic_history",
        ["student_id", "created_at"],
    )

    op.create_index(
        "ix_student_academic_history_changed_by_faculty",
        "student_academic_history",
        ["changed_by_faculty_id"],
    )

    # Student-to-faculty assignment history
    op.create_table(
        "student_faculty_assignments",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "student_id",
            sa.Integer(),
            sa.ForeignKey("students.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "previous_faculty_id",
            sa.Integer(),
            sa.ForeignKey("faculty.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "assigned_faculty_id",
            sa.Integer(),
            sa.ForeignKey("faculty.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("action", sa.String(length=30), nullable=False),
        sa.Column(
            "assigned_by_faculty_id",
            sa.Integer(),
            sa.ForeignKey("faculty.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "assigned_by_admin_id",
            sa.Integer(),
            sa.ForeignKey("admins.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    op.create_index(
        "ix_student_faculty_assignments_student_created",
        "student_faculty_assignments",
        ["student_id", "created_at"],
    )

    op.create_index(
        "ix_student_faculty_assignments_assigned_faculty",
        "student_faculty_assignments",
        ["assigned_faculty_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_student_faculty_assignments_assigned_faculty",
        table_name="student_faculty_assignments",
    )
    op.drop_index(
        "ix_student_faculty_assignments_student_created",
        table_name="student_faculty_assignments",
    )
    op.drop_table("student_faculty_assignments")

    op.drop_index(
        "ix_student_academic_history_changed_by_faculty",
        table_name="student_academic_history",
    )
    op.drop_index(
        "ix_student_academic_history_student_created",
        table_name="student_academic_history",
    )
    op.drop_table("student_academic_history")

    op.drop_index(
        "ix_students_college_department_batch",
        table_name="students",
    )
    op.drop_index(
        "ix_students_assigned_faculty_id",
        table_name="students",
    )
    op.drop_index(
        "ix_students_batch_id",
        table_name="students",
    )
    op.drop_index(
        "ix_students_department_id",
        table_name="students",
    )

    op.drop_constraint(
        "ck_students_current_year",
        "students",
        type_="check",
    )
    op.drop_constraint(
        "fk_students_assigned_faculty_id",
        "students",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_students_batch_id",
        "students",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_students_department_id",
        "students",
        type_="foreignkey",
    )

    op.drop_column("students", "assigned_faculty_id")
    op.drop_column("students", "current_year")
    op.drop_column("students", "batch_id")
    op.drop_column("students", "department_id")

    op.drop_index(
        "ix_faculty_college_department_role",
        table_name="faculty",
    )
    op.drop_constraint(
        "fk_faculty_department_id",
        "faculty",
        type_="foreignkey",
    )
    op.drop_column("faculty", "legacy_college_scope")
    op.drop_column("faculty", "department_id")

    op.execute(
        "DROP INDEX IF EXISTS uq_academic_batches_identity_ci"
    )
    op.drop_index(
        "ix_academic_batches_college_active",
        table_name="academic_batches",
    )
    op.drop_index(
        "ix_academic_batches_college",
        table_name="academic_batches",
    )
    op.drop_table("academic_batches")

    op.execute(
        "DROP INDEX IF EXISTS uq_departments_college_code_ci"
    )
    op.execute(
        "DROP INDEX IF EXISTS uq_departments_college_name_ci"
    )
    op.drop_index(
        "ix_departments_college_active",
        table_name="departments",
    )
    op.drop_index(
        "ix_departments_college",
        table_name="departments",
    )
    op.drop_table("departments")

    op.execute(
        "DROP INDEX IF EXISTS uq_college_organization_settings_college_ci"
    )
    op.drop_index(
        "ix_college_organization_settings_college",
        table_name="college_organization_settings",
    )
    op.drop_table("college_organization_settings")
