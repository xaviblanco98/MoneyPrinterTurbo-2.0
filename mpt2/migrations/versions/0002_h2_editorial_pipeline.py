"""h2 editorial pipeline

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-02 09:36:27.604526
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "budget_limits",
        sa.Column("key", sa.String(length=32), nullable=False),
        sa.Column("value_eur", sa.Float(), nullable=False),
        sa.Column("updated_by", sa.String(length=200), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )
    op.create_table(
        "llm_cache",
        sa.Column("cache_key", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=100), nullable=False),
        sa.Column("task", sa.String(length=64), nullable=False),
        sa.Column("response", sa.JSON(), nullable=False),
        sa.Column("tokens_in", sa.Integer(), nullable=False),
        sa.Column("tokens_out", sa.Integer(), nullable=False),
        sa.Column("hits", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_hit_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("cache_key"),
    )
    op.create_table(
        "dossiers",
        sa.Column("project_id", sa.String(length=32), nullable=False),
        sa.Column("run", sa.Integer(), nullable=False),
        sa.Column("content", sa.JSON(), nullable=False),
        sa.Column("researched_at", sa.DateTime(), nullable=False),
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["video_projects.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "run", name="uq_dossier_run"),
    )
    with op.batch_alter_table("dossiers", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_dossiers_project_id"), ["project_id"], unique=False
        )

    op.create_table(
        "editorial_options",
        sa.Column("project_id", sa.String(length=32), nullable=False),
        sa.Column("run", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("scores", sa.JSON(), nullable=False),
        sa.Column("total_score", sa.Float(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("selected", sa.Boolean(), nullable=False),
        sa.Column("selected_by", sa.String(length=200), nullable=True),
        sa.Column("selection_reason", sa.Text(), nullable=True),
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["video_projects.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("editorial_options", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_editorial_options_project_id"), ["project_id"], unique=False
        )

    op.create_table(
        "llm_calls",
        sa.Column("project_id", sa.String(length=32), nullable=True),
        sa.Column("channel_id", sa.String(length=32), nullable=True),
        sa.Column("task", sa.String(length=64), nullable=False),
        sa.Column("stage", sa.String(length=32), nullable=True),
        sa.Column("model", sa.String(length=100), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("idempotency_key", sa.String(length=200), nullable=False),
        sa.Column("cache_key", sa.String(length=64), nullable=False),
        sa.Column("cache_hit", sa.Boolean(), nullable=False),
        sa.Column("prompt_sha256", sa.String(length=64), nullable=False),
        sa.Column("prompt_chars", sa.Integer(), nullable=False),
        sa.Column("tokens_in", sa.Integer(), nullable=False),
        sa.Column("tokens_out", sa.Integer(), nullable=False),
        sa.Column("cache_read_tokens", sa.Integer(), nullable=False),
        sa.Column("cache_write_tokens", sa.Integer(), nullable=False),
        sa.Column("web_search_requests", sa.Integer(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("cost_usd", sa.Float(), nullable=False),
        sa.Column("cost_eur", sa.Float(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("request_id", sa.String(length=100), nullable=True),
        sa.Column("stop_reason", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.ForeignKeyConstraint(
            ["channel_id"],
            ["channels.id"],
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["video_projects.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("llm_calls", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_llm_calls_created_at"), ["created_at"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_llm_calls_idempotency_key"),
            ["idempotency_key"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_llm_calls_project_id"), ["project_id"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_llm_calls_task"), ["task"], unique=False)

    op.create_table(
        "research_plans",
        sa.Column("project_id", sa.String(length=32), nullable=False),
        sa.Column("run", sa.Integer(), nullable=False),
        sa.Column("content", sa.JSON(), nullable=False),
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["video_projects.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "run", name="uq_research_plan_run"),
    )
    with op.batch_alter_table("research_plans", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_research_plans_project_id"), ["project_id"], unique=False
        )

    op.create_table(
        "review_events",
        sa.Column("project_id", sa.String(length=32), nullable=False),
        sa.Column("entity_type", sa.String(length=32), nullable=False),
        sa.Column("entity_id", sa.String(length=64), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("actor", sa.String(length=200), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("changes", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["video_projects.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("review_events", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_review_events_project_id"), ["project_id"], unique=False
        )

    op.create_table(
        "search_queries",
        sa.Column("project_id", sa.String(length=32), nullable=False),
        sa.Column("run", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=True),
        sa.Column("language", sa.String(length=16), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("result_count", sa.Integer(), nullable=False),
        sa.Column("results", sa.JSON(), nullable=False),
        sa.Column("executed_queries", sa.JSON(), nullable=False),
        sa.Column("cost_eur", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["video_projects.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("search_queries", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_search_queries_project_id"), ["project_id"], unique=False
        )

    with op.batch_alter_table("quality_checks", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "kind", sa.String(length=32), nullable=False, server_default="technical"
            )
        )
        batch_op.add_column(
            sa.Column("run", sa.Integer(), nullable=False, server_default="1")
        )

    with op.batch_alter_table("research_claims", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("run", sa.Integer(), nullable=False, server_default="1")
        )
        batch_op.add_column(
            sa.Column(
                "importance",
                sa.String(length=16),
                nullable=False,
                server_default="medium",
            )
        )
        batch_op.add_column(
            sa.Column("geographic_scope", sa.String(length=120), nullable=True)
        )
        batch_op.add_column(
            sa.Column("time_period", sa.String(length=120), nullable=True)
        )
        batch_op.add_column(
            sa.Column("evidence", sa.JSON(), nullable=False, server_default="[]")
        )
        batch_op.add_column(
            sa.Column(
                "contradicting_source_ids",
                sa.JSON(),
                nullable=False,
                server_default="[]",
            )
        )
        batch_op.add_column(
            sa.Column(
                "verification_status",
                sa.String(length=16),
                nullable=False,
                server_default="unverified",
            )
        )
        batch_op.add_column(
            sa.Column(
                "used_in_script", sa.Boolean(), nullable=False, server_default="0"
            )
        )
        batch_op.add_column(
            sa.Column("human_edited", sa.Boolean(), nullable=False, server_default="0")
        )
        batch_op.add_column(
            sa.Column("discarded", sa.Boolean(), nullable=False, server_default="0")
        )
        batch_op.add_column(sa.Column("notes", sa.Text(), nullable=True))

    with op.batch_alter_table("research_sources", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("run", sa.Integer(), nullable=False, server_default="1")
        )
        batch_op.add_column(sa.Column("canonical_url", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("domain", sa.String(length=200), nullable=True))
        batch_op.add_column(sa.Column("accessed_at", sa.DateTime(), nullable=True))
        batch_op.add_column(
            sa.Column("snippets", sa.JSON(), nullable=False, server_default="[]")
        )
        batch_op.add_column(
            sa.Column("is_primary", sa.Boolean(), nullable=False, server_default="0")
        )
        batch_op.add_column(
            sa.Column("source_type", sa.String(length=64), nullable=True)
        )
        batch_op.add_column(
            sa.Column(
                "status",
                sa.String(length=16),
                nullable=False,
                server_default="candidate",
            )
        )
        batch_op.add_column(sa.Column("rejection_reason", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("query_id", sa.String(length=32), nullable=True))
        batch_op.create_index(
            batch_op.f("ix_research_sources_canonical_url"),
            ["canonical_url"],
            unique=False,
        )
        batch_op.create_foreign_key(
            "fk_research_sources_query_id_search_queries",
            "search_queries",
            ["query_id"],
            ["id"],
        )

    with op.batch_alter_table("scenes", schema=None) as batch_op:
        batch_op.add_column(sa.Column("chart_data", sa.JSON(), nullable=True))
        batch_op.add_column(
            sa.Column("claim_ids", sa.JSON(), nullable=False, server_default="[]")
        )
        batch_op.add_column(
            sa.Column("source_ids", sa.JSON(), nullable=False, server_default="[]")
        )
        batch_op.add_column(
            sa.Column(
                "copyright_risk",
                sa.String(length=16),
                nullable=False,
                server_default="low",
            )
        )
        batch_op.add_column(
            sa.Column(
                "priority",
                sa.String(length=16),
                nullable=False,
                server_default="normal",
            )
        )
        batch_op.add_column(
            sa.Column("human_edited", sa.Boolean(), nullable=False, server_default="0")
        )

    with op.batch_alter_table("script_sections", schema=None) as batch_op:
        batch_op.add_column(sa.Column("title", sa.String(length=200), nullable=True))
        batch_op.add_column(
            sa.Column("word_count", sa.Integer(), nullable=False, server_default="0")
        )
        batch_op.add_column(
            sa.Column("human_edited", sa.Boolean(), nullable=False, server_default="0")
        )
        batch_op.add_column(sa.Column("notes", sa.Text(), nullable=True))

    with op.batch_alter_table("scripts", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("run", sa.Integer(), nullable=False, server_default="1")
        )
        batch_op.add_column(sa.Column("title", sa.String(length=300), nullable=True))
        batch_op.add_column(sa.Column("angle_id", sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column("hook_id", sa.String(length=32), nullable=True))
        batch_op.add_column(
            sa.Column("structure_id", sa.String(length=32), nullable=True)
        )
        batch_op.add_column(
            sa.Column("human_edited", sa.Boolean(), nullable=False, server_default="0")
        )
        batch_op.create_foreign_key(
            "fk_scripts_hook_id_editorial_options",
            "editorial_options",
            ["hook_id"],
            ["id"],
        )
        batch_op.create_foreign_key(
            "fk_scripts_structure_id_editorial_options",
            "editorial_options",
            ["structure_id"],
            ["id"],
        )
        batch_op.create_foreign_key(
            "fk_scripts_angle_id_editorial_options",
            "editorial_options",
            ["angle_id"],
            ["id"],
        )

    with op.batch_alter_table("storyboards", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("run", sa.Integer(), nullable=False, server_default="1")
        )

    with op.batch_alter_table("video_projects", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("pipeline_run", sa.Integer(), nullable=False, server_default="1")
        )


def downgrade() -> None:
    with op.batch_alter_table("video_projects", schema=None) as batch_op:
        batch_op.drop_column("pipeline_run")

    with op.batch_alter_table("storyboards", schema=None) as batch_op:
        batch_op.drop_column("run")

    with op.batch_alter_table("scripts", schema=None) as batch_op:
        batch_op.drop_constraint(
            "fk_scripts_angle_id_editorial_options", type_="foreignkey"
        )
        batch_op.drop_constraint(
            "fk_scripts_structure_id_editorial_options", type_="foreignkey"
        )
        batch_op.drop_constraint(
            "fk_scripts_hook_id_editorial_options", type_="foreignkey"
        )
        batch_op.drop_column("human_edited")
        batch_op.drop_column("structure_id")
        batch_op.drop_column("hook_id")
        batch_op.drop_column("angle_id")
        batch_op.drop_column("title")
        batch_op.drop_column("run")

    with op.batch_alter_table("script_sections", schema=None) as batch_op:
        batch_op.drop_column("notes")
        batch_op.drop_column("human_edited")
        batch_op.drop_column("word_count")
        batch_op.drop_column("title")

    with op.batch_alter_table("scenes", schema=None) as batch_op:
        batch_op.drop_column("human_edited")
        batch_op.drop_column("priority")
        batch_op.drop_column("copyright_risk")
        batch_op.drop_column("source_ids")
        batch_op.drop_column("claim_ids")
        batch_op.drop_column("chart_data")

    with op.batch_alter_table("research_sources", schema=None) as batch_op:
        batch_op.drop_constraint(
            "fk_research_sources_query_id_search_queries", type_="foreignkey"
        )
        batch_op.drop_index(batch_op.f("ix_research_sources_canonical_url"))
        batch_op.drop_column("query_id")
        batch_op.drop_column("rejection_reason")
        batch_op.drop_column("status")
        batch_op.drop_column("source_type")
        batch_op.drop_column("is_primary")
        batch_op.drop_column("snippets")
        batch_op.drop_column("accessed_at")
        batch_op.drop_column("domain")
        batch_op.drop_column("canonical_url")
        batch_op.drop_column("run")

    with op.batch_alter_table("research_claims", schema=None) as batch_op:
        batch_op.drop_column("notes")
        batch_op.drop_column("discarded")
        batch_op.drop_column("human_edited")
        batch_op.drop_column("used_in_script")
        batch_op.drop_column("verification_status")
        batch_op.drop_column("contradicting_source_ids")
        batch_op.drop_column("evidence")
        batch_op.drop_column("time_period")
        batch_op.drop_column("geographic_scope")
        batch_op.drop_column("importance")
        batch_op.drop_column("run")

    with op.batch_alter_table("quality_checks", schema=None) as batch_op:
        batch_op.drop_column("run")
        batch_op.drop_column("kind")

    with op.batch_alter_table("search_queries", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_search_queries_project_id"))

    op.drop_table("search_queries")
    with op.batch_alter_table("review_events", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_review_events_project_id"))

    op.drop_table("review_events")
    with op.batch_alter_table("research_plans", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_research_plans_project_id"))

    op.drop_table("research_plans")
    with op.batch_alter_table("llm_calls", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_llm_calls_task"))
        batch_op.drop_index(batch_op.f("ix_llm_calls_project_id"))
        batch_op.drop_index(batch_op.f("ix_llm_calls_idempotency_key"))
        batch_op.drop_index(batch_op.f("ix_llm_calls_created_at"))

    op.drop_table("llm_calls")
    with op.batch_alter_table("editorial_options", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_editorial_options_project_id"))

    op.drop_table("editorial_options")
    with op.batch_alter_table("dossiers", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_dossiers_project_id"))

    op.drop_table("dossiers")
    op.drop_table("llm_cache")
    op.drop_table("budget_limits")
