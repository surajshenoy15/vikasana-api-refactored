"""college hierarchy foundation

Revision ID: 005_college_hierarchy_foundation
Revises: 004_department_foundation
Create Date: 2026-08-11

Additive foundation for:

Admin
  -> College
  -> College Coordinator
  -> HOD
  -> Faculty / Mentor
  -> Student

Important:
- Existing legacy records are preserved.
- Existing college string columns remain unchanged.
- Legacy spellings can be mapped through college_aliases.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "005_college_hierarchy_foundation"
down_revision: Union[str, None] = "004_department_foundation"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --------------------------------------------------
    # COLLEGE MASTER
    # --------------------------------------------------

    op.create_table(
        "colleges",
        sa.Column(
            "id",
            sa.Integer(),
            primary_key=True,
            autoincrement=True,
        ),
        sa.Column(
            "name",
            sa.String(length=200),
            nullable=False,
        ),
        sa.Column(
            "code",
            sa.String(length=50),
            nullable=True,
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "created_by_admin_id",
            sa.Integer(),
            sa.ForeignKey(
                "admins.id",
                ondelete="SET NULL",
            ),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    op.create_index(
        "ix_colleges_name",
        "colleges",
        ["name"],
    )

    # Case-insensitive canonical college name uniqueness.
    op.execute(
        """
        CREATE UNIQUE INDEX uq_colleges_name_ci
        ON colleges (LOWER(TRIM(name)))
        """
    )

    # Optional short code, unique when provided.
    op.execute(
        """
        CREATE UNIQUE INDEX uq_colleges_code_ci
        ON colleges (LOWER(TRIM(code)))
        WHERE code IS NOT NULL
          AND TRIM(code) <> ''
        """
    )

    # --------------------------------------------------
    # COLLEGE ALIASES
    # --------------------------------------------------
    #
    # This table allows legacy names/spellings to map to
    # one canonical College without changing old records.
    #
    # Example:
    #   GREEN CIRCUIT  -> College X
    #   green circuit  -> College X
    #   GREEN CIRCUT   -> College X
    # --------------------------------------------------

    op.create_table(
        "college_aliases",
        sa.Column(
            "id",
            sa.Integer(),
            primary_key=True,
            autoincrement=True,
        ),
        sa.Column(
            "college_id",
            sa.Integer(),
            sa.ForeignKey(
                "colleges.id",
                ondelete="RESTRICT",
            ),
            nullable=False,
        ),
        sa.Column(
            "alias",
            sa.String(length=200),
            nullable=False,
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "created_by_admin_id",
            sa.Integer(),
            sa.ForeignKey(
                "admins.id",
                ondelete="SET NULL",
            ),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    op.create_index(
        "ix_college_aliases_college_id",
        "college_aliases",
        ["college_id"],
    )

    op.execute(
        """
        CREATE UNIQUE INDEX uq_college_aliases_alias_ci
        ON college_aliases (LOWER(TRIM(alias)))
        """
    )


    # --------------------------------------------------
    # FACULTY HIERARCHY FOUNDATION
    # --------------------------------------------------
    #
    # Current hierarchy:
    #
    # Admin
    #   -> College Coordinator
    #       -> HOD
    #           -> Faculty / Mentor
    #
    # parent_faculty_id represents the current reporting/access tree.
    #
    # created_by_* preserves original account provenance.
    # Existing faculty rows remain valid because all fields are nullable.
    # --------------------------------------------------

    op.add_column(
        "faculty",
        sa.Column(
            "parent_faculty_id",
            sa.Integer(),
            nullable=True,
        ),
    )

    op.create_foreign_key(
        "fk_faculty_parent_faculty_id",
        "faculty",
        "faculty",
        ["parent_faculty_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.add_column(
        "faculty",
        sa.Column(
            "created_by_admin_id",
            sa.Integer(),
            nullable=True,
        ),
    )

    op.create_foreign_key(
        "fk_faculty_created_by_admin_id",
        "faculty",
        "admins",
        ["created_by_admin_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.add_column(
        "faculty",
        sa.Column(
            "created_by_faculty_id",
            sa.Integer(),
            nullable=True,
        ),
    )

    op.create_foreign_key(
        "fk_faculty_created_by_faculty_id",
        "faculty",
        "faculty",
        ["created_by_faculty_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_index(
        "ix_faculty_parent_faculty_id",
        "faculty",
        ["parent_faculty_id"],
    )

    op.create_index(
        "ix_faculty_created_by_admin_id",
        "faculty",
        ["created_by_admin_id"],
    )

    op.create_index(
        "ix_faculty_created_by_faculty_id",
        "faculty",
        ["created_by_faculty_id"],
    )

    # --------------------------------------------------
    # FACULTY ACCESS / ROLE HISTORY
    # --------------------------------------------------
    #
    # Immutable audit trail for:
    #
    # Admin -> College Coordinator
    # College Coordinator -> HOD
    # HOD -> Faculty / Mentor
    #
    # Rows are added when access is granted, changed,
    # reassigned, activated or deactivated.
    # --------------------------------------------------

    op.create_table(
        "faculty_access_history",

        sa.Column(
            "id",
            sa.Integer(),
            primary_key=True,
            autoincrement=True,
        ),

        sa.Column(
            "faculty_id",
            sa.Integer(),
            sa.ForeignKey(
                "faculty.id",
                ondelete="SET NULL",
            ),
            nullable=True,
        ),

        sa.Column(
            "action",
            sa.String(length=40),
            nullable=False,
        ),

        sa.Column(
            "previous_role",
            sa.String(length=50),
            nullable=True,
        ),

        sa.Column(
            "new_role",
            sa.String(length=50),
            nullable=True,
        ),

        sa.Column(
            "previous_parent_faculty_id",
            sa.Integer(),
            sa.ForeignKey(
                "faculty.id",
                ondelete="SET NULL",
            ),
            nullable=True,
        ),

        sa.Column(
            "new_parent_faculty_id",
            sa.Integer(),
            sa.ForeignKey(
                "faculty.id",
                ondelete="SET NULL",
            ),
            nullable=True,
        ),

        sa.Column(
            "college",
            sa.String(length=200),
            nullable=False,
        ),

        sa.Column(
            "department_id",
            sa.Integer(),
            sa.ForeignKey(
                "departments.id",
                ondelete="SET NULL",
            ),
            nullable=True,
        ),

        sa.Column(
            "changed_by_admin_id",
            sa.Integer(),
            sa.ForeignKey(
                "admins.id",
                ondelete="SET NULL",
            ),
            nullable=True,
        ),

        sa.Column(
            "changed_by_faculty_id",
            sa.Integer(),
            sa.ForeignKey(
                "faculty.id",
                ondelete="SET NULL",
            ),
            nullable=True,
        ),

        sa.Column(
            "note",
            sa.Text(),
            nullable=True,
        ),

        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    op.create_index(
        "ix_faculty_access_history_faculty_created",
        "faculty_access_history",
        ["faculty_id", "created_at"],
    )

    op.create_index(
        "ix_faculty_access_history_changed_by_faculty",
        "faculty_access_history",
        ["changed_by_faculty_id"],
    )

    op.create_index(
        "ix_faculty_access_history_changed_by_admin",
        "faculty_access_history",
        ["changed_by_admin_id"],
    )

    op.create_index(
        "ix_faculty_access_history_parent",
        "faculty_access_history",
        ["new_parent_faculty_id"],
    )


def downgrade() -> None:
    # Intentionally blocked because LoRaa Connect must preserve
    # historical college and hierarchy records.
    raise RuntimeError(
        "Downgrade of 005_college_hierarchy_foundation is intentionally "
        "disabled to protect historical records."
    )
