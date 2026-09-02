"""h1 initial schema

Revision ID: 0001
Revises:
Create Date: 2026-09-01 23:53:40.915529
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "channels",
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("language", sa.String(length=16), nullable=False),
        sa.Column("country", sa.String(length=2), nullable=False),
        sa.Column("niche", sa.String(length=200), nullable=False),
        sa.Column("audience", sa.Text(), nullable=False),
        sa.Column("tone", sa.Text(), nullable=False),
        sa.Column("target_duration_s", sa.Integer(), nullable=False),
        sa.Column("voice", sa.String(length=200), nullable=False),
        sa.Column("visual_style", sa.Text(), nullable=False),
        sa.Column("allowed_sources", sa.JSON(), nullable=False),
        sa.Column("banned_sources", sa.JSON(), nullable=False),
        sa.Column("max_budget_eur", sa.Float(), nullable=False),
        sa.Column("quality_threshold", sa.Float(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug"),
    )
    op.create_table(
        "video_projects",
        sa.Column("channel_id", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("topic", sa.Text(), nullable=False),
        sa.Column("format", sa.String(length=16), nullable=False),
        sa.Column("language", sa.String(length=16), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("failed_from_state", sa.String(length=32), nullable=True),
        sa.Column("state_updated_at", sa.DateTime(), nullable=False),
        sa.Column("parent_project_id", sa.String(length=32), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["channel_id"],
            ["channels.id"],
        ),
        sa.ForeignKeyConstraint(
            ["parent_project_id"],
            ["video_projects.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("video_projects", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_video_projects_channel_id"), ["channel_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_video_projects_state"), ["state"], unique=False
        )

    op.create_table(
        "cost_entries",
        sa.Column("project_id", sa.String(length=32), nullable=True),
        sa.Column("channel_id", sa.String(length=32), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("stage", sa.String(length=32), nullable=False),
        sa.Column("units", sa.Float(), nullable=False),
        sa.Column("unit_type", sa.String(length=16), nullable=False),
        sa.Column("est_cost_eur", sa.Float(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
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
    with op.batch_alter_table("cost_entries", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_cost_entries_channel_id"), ["channel_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_cost_entries_project_id"), ["project_id"], unique=False
        )

    op.create_table(
        "human_approvals",
        sa.Column("project_id", sa.String(length=32), nullable=False),
        sa.Column("stage", sa.String(length=16), nullable=False),
        sa.Column("decision", sa.String(length=32), nullable=False),
        sa.Column("reviewer", sa.String(length=200), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("resulting_state", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["video_projects.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("human_approvals", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_human_approvals_project_id"), ["project_id"], unique=False
        )

    op.create_table(
        "pipeline_jobs",
        sa.Column("project_id", sa.String(length=32), nullable=False),
        sa.Column("stage", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("idempotency_key", sa.String(length=200), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("next_run_at", sa.DateTime(), nullable=False),
        sa.Column("locked_by", sa.String(length=200), nullable=True),
        sa.Column("locked_at", sa.DateTime(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("error_module", sa.String(length=200), nullable=True),
        sa.Column("error_at", sa.DateTime(), nullable=True),
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["video_projects.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key", name="uq_job_idempotency_key"),
    )
    with op.batch_alter_table("pipeline_jobs", schema=None) as batch_op:
        batch_op.create_index(
            "ix_jobs_status_next_run", ["status", "next_run_at"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_pipeline_jobs_project_id"), ["project_id"], unique=False
        )

    op.create_table(
        "project_state_transitions",
        sa.Column("project_id", sa.String(length=32), nullable=False),
        sa.Column("from_state", sa.String(length=32), nullable=False),
        sa.Column("to_state", sa.String(length=32), nullable=False),
        sa.Column("actor", sa.String(length=200), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["video_projects.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("project_state_transitions", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_project_state_transitions_project_id"),
            ["project_id"],
            unique=False,
        )

    op.create_table(
        "quality_checks",
        sa.Column("project_id", sa.String(length=32), nullable=False),
        sa.Column("checks", sa.JSON(), nullable=False),
        sa.Column("total_score", sa.Float(), nullable=False),
        sa.Column("passed", sa.Boolean(), nullable=False),
        sa.Column("sent_back_to_state", sa.String(length=32), nullable=True),
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["video_projects.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("quality_checks", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_quality_checks_project_id"), ["project_id"], unique=False
        )

    op.create_table(
        "research_claims",
        sa.Column("project_id", sa.String(length=32), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("verified", sa.Boolean(), nullable=False),
        sa.Column("verification_note", sa.Text(), nullable=True),
        sa.Column("contradicts_claim_id", sa.String(length=32), nullable=True),
        sa.Column("entities", sa.JSON(), nullable=False),
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["contradicts_claim_id"],
            ["research_claims.id"],
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["video_projects.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("research_claims", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_research_claims_project_id"), ["project_id"], unique=False
        )

    op.create_table(
        "research_sources",
        sa.Column("project_id", sa.String(length=32), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("author", sa.String(length=300), nullable=True),
        sa.Column("publisher", sa.String(length=300), nullable=True),
        sa.Column("published_at", sa.DateTime(), nullable=True),
        sa.Column("retrieved_at", sa.DateTime(), nullable=False),
        sa.Column("license", sa.String(length=64), nullable=True),
        sa.Column("reliability", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=True),
        sa.Column("excerpt_path", sa.Text(), nullable=True),
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["video_projects.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("research_sources", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_research_sources_project_id"), ["project_id"], unique=False
        )

    op.create_table(
        "scripts",
        sa.Column("project_id", sa.String(length=32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("language", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("est_duration_s", sa.Float(), nullable=False),
        sa.Column("word_count", sa.Integer(), nullable=False),
        sa.Column("reviewer_notes", sa.Text(), nullable=True),
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["video_projects.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "version", name="uq_script_version"),
    )
    with op.batch_alter_table("scripts", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_scripts_project_id"), ["project_id"], unique=False
        )

    op.create_table(
        "research_claim_sources",
        sa.Column("claim_id", sa.String(length=32), nullable=False),
        sa.Column("source_id", sa.String(length=32), nullable=False),
        sa.ForeignKeyConstraint(
            ["claim_id"],
            ["research_claims.id"],
        ),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["research_sources.id"],
        ),
        sa.PrimaryKeyConstraint("claim_id", "source_id"),
    )
    op.create_table(
        "script_sections",
        sa.Column("script_id", sa.String(length=32), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("claim_ids", sa.JSON(), nullable=False),
        sa.Column("needs_verification", sa.Boolean(), nullable=False),
        sa.Column("est_duration_s", sa.Float(), nullable=False),
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.ForeignKeyConstraint(
            ["script_id"],
            ["scripts.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("script_id", "position", name="uq_section_position"),
    )
    with op.batch_alter_table("script_sections", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_script_sections_script_id"), ["script_id"], unique=False
        )

    op.create_table(
        "storyboards",
        sa.Column("project_id", sa.String(length=32), nullable=False),
        sa.Column("script_id", sa.String(length=32), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("aspect", sa.String(length=8), nullable=False),
        sa.Column("total_est_duration_s", sa.Float(), nullable=False),
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["video_projects.id"],
        ),
        sa.ForeignKeyConstraint(
            ["script_id"],
            ["scripts.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "version", name="uq_storyboard_version"),
    )
    with op.batch_alter_table("storyboards", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_storyboards_project_id"), ["project_id"], unique=False
        )

    op.create_table(
        "scenes",
        sa.Column("storyboard_id", sa.String(length=32), nullable=False),
        sa.Column("section_id", sa.String(length=32), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("narration", sa.Text(), nullable=False),
        sa.Column("est_duration_s", sa.Float(), nullable=False),
        sa.Column("narrative_goal", sa.Text(), nullable=True),
        sa.Column("visual_description", sa.Text(), nullable=False),
        sa.Column("asset_type", sa.String(length=32), nullable=False),
        sa.Column("search_terms", sa.JSON(), nullable=False),
        sa.Column("on_screen_text", sa.Text(), nullable=True),
        sa.Column("animation", sa.String(length=64), nullable=True),
        sa.Column("transition", sa.String(length=64), nullable=True),
        sa.Column("framing", sa.String(length=64), nullable=True),
        sa.Column("motion", sa.String(length=64), nullable=True),
        sa.Column("license_requirement", sa.String(length=64), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("fallback_visual", sa.Text(), nullable=True),
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.ForeignKeyConstraint(
            ["section_id"],
            ["script_sections.id"],
        ),
        sa.ForeignKeyConstraint(
            ["storyboard_id"],
            ["storyboards.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("storyboard_id", "position", name="uq_scene_position"),
    )
    with op.batch_alter_table("scenes", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_scenes_storyboard_id"), ["storyboard_id"], unique=False
        )

    op.create_table(
        "assets",
        sa.Column("project_id", sa.String(length=32), nullable=False),
        sa.Column("scene_id", sa.String(length=32), nullable=True),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("provider_asset_id", sa.String(length=200), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("author", sa.String(length=300), nullable=True),
        sa.Column("license", sa.String(length=32), nullable=False),
        sa.Column("attribution_text", sa.Text(), nullable=True),
        sa.Column("local_path", sa.Text(), nullable=True),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("duration_s", sa.Float(), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=True),
        sa.Column("relevance_score", sa.Float(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["video_projects.id"],
        ),
        sa.ForeignKeyConstraint(
            ["scene_id"],
            ["scenes.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("assets", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_assets_content_hash"), ["content_hash"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_assets_project_id"), ["project_id"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("assets", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_assets_project_id"))
        batch_op.drop_index(batch_op.f("ix_assets_content_hash"))

    op.drop_table("assets")
    with op.batch_alter_table("scenes", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_scenes_storyboard_id"))

    op.drop_table("scenes")
    with op.batch_alter_table("storyboards", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_storyboards_project_id"))

    op.drop_table("storyboards")
    with op.batch_alter_table("script_sections", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_script_sections_script_id"))

    op.drop_table("script_sections")
    op.drop_table("research_claim_sources")
    with op.batch_alter_table("scripts", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_scripts_project_id"))

    op.drop_table("scripts")
    with op.batch_alter_table("research_sources", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_research_sources_project_id"))

    op.drop_table("research_sources")
    with op.batch_alter_table("research_claims", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_research_claims_project_id"))

    op.drop_table("research_claims")
    with op.batch_alter_table("quality_checks", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_quality_checks_project_id"))

    op.drop_table("quality_checks")
    with op.batch_alter_table("project_state_transitions", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_project_state_transitions_project_id"))

    op.drop_table("project_state_transitions")
    with op.batch_alter_table("pipeline_jobs", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_pipeline_jobs_project_id"))
        batch_op.drop_index("ix_jobs_status_next_run")

    op.drop_table("pipeline_jobs")
    with op.batch_alter_table("human_approvals", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_human_approvals_project_id"))

    op.drop_table("human_approvals")
    with op.batch_alter_table("cost_entries", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_cost_entries_project_id"))
        batch_op.drop_index(batch_op.f("ix_cost_entries_channel_id"))

    op.drop_table("cost_entries")
    with op.batch_alter_table("video_projects", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_video_projects_state"))
        batch_op.drop_index(batch_op.f("ix_video_projects_channel_id"))

    op.drop_table("video_projects")
    op.drop_table("channels")
