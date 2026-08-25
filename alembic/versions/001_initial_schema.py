"""Initial schema

Revision ID: 001
Revises: 
Create Date: 2025-08-25 16:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '001'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Organisations table
    op.create_table(
        'organisations',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('github_org_id', sa.String(), nullable=True),
        sa.Column('billing_email', sa.String(), nullable=True),
        sa.Column('plan', sa.Enum('free', 'starter', 'team', name='organisationplan'), nullable=True),
        sa.Column('stripe_customer_id', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_organisations_id', 'organisations', ['id'], unique=False)
    op.create_index('ix_organisations_name', 'organisations', ['name'], unique=False)

    # Repositories table
    op.create_table(
        'repositories',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('organisation_id', sa.String(), nullable=False),
        sa.Column('github_repo_id', sa.String(), nullable=True),
        sa.Column('full_name', sa.String(), nullable=False),
        sa.Column('default_branch', sa.String(), nullable=True),
        sa.Column('webhook_secret', sa.String(), nullable=True),
        sa.Column('tier', sa.Enum('free', 'starter', 'team', name='repositorytier'), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['organisation_id'], ['organisations.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_repositories_id', 'repositories', ['id'], unique=False)
    op.create_index('ix_repositories_full_name', 'repositories', ['full_name'], unique=False)

    # Pipeline runs table
    op.create_table(
        'pipeline_runs',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('repository_id', sa.String(), nullable=False),
        sa.Column('trigger_type', sa.String(), nullable=True),
        sa.Column('github_pr_number', sa.Integer(), nullable=True),
        sa.Column('commit_sha', sa.String(), nullable=True),
        sa.Column('diff_url', sa.String(), nullable=True),
        sa.Column('linked_issue_id', sa.String(), nullable=True),
        sa.Column('status', sa.Enum('queued', 'extracting', 'generating', 'awaiting_approval', 'running', 'completed', 'failed', name='pipelinestatus'), nullable=True),
        sa.Column('risk_level', sa.Enum('low', 'medium', 'high', 'critical', name='risklevel'), nullable=True),
        sa.Column('started_at', sa.DateTime(), nullable=True),
        sa.Column('completed_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['repository_id'], ['repositories.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_pipeline_runs_id', 'pipeline_runs', ['id'], unique=False)
    op.create_index('ix_pipeline_runs_repo_status', 'pipeline_runs', ['repository_id', 'status'], unique=False)
    op.create_index('ix_pipeline_runs_commit_sha', 'pipeline_runs', ['commit_sha'], unique=False)
    op.create_index('ix_pipeline_runs_created_at', 'pipeline_runs', ['created_at'], unique=False)
    op.create_index('ix_pipeline_runs_github_pr_number', 'pipeline_runs', ['github_pr_number'], unique=False)
    op.create_index('ix_pipeline_runs_risk_level', 'pipeline_runs', ['risk_level'], unique=False)
    op.create_index('ix_pipeline_runs_status', 'pipeline_runs', ['status'], unique=False)

    # Test cases table
    op.create_table(
        'test_cases',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('pipeline_run_id', sa.String(), nullable=False),
        sa.Column('title', sa.String(), nullable=False),
        sa.Column('test_type', sa.Enum('functional', 'regression', 'edge_case', 'integration', name='testtype'), nullable=True),
        sa.Column('priority', sa.Enum('p0_critical', 'p1_high', 'p2_medium', 'p3_low', name='testpriority'), nullable=True),
        sa.Column('steps', sa.Text(), nullable=False),
        sa.Column('expected_result', sa.Text(), nullable=True),
        sa.Column('risk_level', sa.Enum('low', 'medium', 'high', 'critical', name='risklevel'), nullable=True),
        sa.Column('risk_rationale', sa.Text(), nullable=True),
        sa.Column('generated_by', sa.String(), nullable=True),
        sa.Column('status', sa.Enum('pending', 'approved', 'rejected', 'running', 'passed', 'failed', 'healed', name='testcasestatus'), nullable=True),
        sa.Column('approval_status', sa.Enum('pending', 'approved', 'rejected', name='approvalstatus'), nullable=True),
        sa.Column('approved_by', sa.String(), nullable=True),
        sa.Column('approved_at', sa.DateTime(), nullable=True),
        sa.Column('uipath_test_id', sa.String(), nullable=True),
        sa.Column('outcome', sa.Enum('passed', 'failed', 'skipped', 'timed_out', name='testoutcome'), nullable=True),
        sa.Column('failure_step', sa.String(), nullable=True),
        sa.Column('failure_message', sa.Text(), nullable=True),
        sa.Column('failure_type', sa.Enum('selector_broken', 'api_contract', 'assertion_stale', 'assertion_failed', 'timeout', 'network', 'unknown', name='failuretype'), nullable=True),
        sa.Column('screenshot_url', sa.String(), nullable=True),
        sa.Column('duration_ms', sa.Integer(), nullable=True),
        sa.Column('robot_id', sa.String(), nullable=True),
        sa.Column('executed_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['pipeline_run_id'], ['pipeline_runs.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_test_cases_id', 'test_cases', ['id'], unique=False)
    op.create_index('ix_test_cases_pipeline_run_id', 'test_cases', ['pipeline_run_id'], unique=False)
    op.create_index('ix_test_cases_run_priority', 'test_cases', ['pipeline_run_id', 'priority'], unique=False)
    op.create_index('ix_test_cases_status', 'test_cases', ['status'], unique=False)
    op.create_index('ix_test_cases_approval_status', 'test_cases', ['approval_status'], unique=False)
    op.create_index('ix_test_cases_outcome', 'test_cases', ['outcome'], unique=False)

    # Test results table
    op.create_table(
        'test_results',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('test_case_id', sa.String(), nullable=False),
        sa.Column('outcome', sa.Enum('passed', 'failed', 'skipped', 'timed_out', name='testoutcome'), nullable=False),
        sa.Column('failure_step', sa.String(), nullable=True),
        sa.Column('failure_message', sa.Text(), nullable=True),
        sa.Column('failure_type', sa.Enum('selector_broken', 'api_contract', 'assertion_stale', 'assertion_failed', 'timeout', 'network', 'unknown', name='failuretype'), nullable=True),
        sa.Column('screenshot_url', sa.String(), nullable=True),
        sa.Column('duration_ms', sa.Integer(), nullable=True),
        sa.Column('robot_id', sa.String(), nullable=True),
        sa.Column('heal_attempt_id', sa.String(), nullable=True),
        sa.Column('executed_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['heal_attempt_id'], ['heal_attempts.id'], ),
        sa.ForeignKeyConstraint(['test_case_id'], ['test_cases.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_test_results_id', 'test_results', ['id'], unique=False)
    op.create_index('ix_test_results_test_case_id', 'test_results', ['test_case_id'], unique=False)
    op.create_index('ix_test_results_test_outcome', 'test_results', ['test_case_id', 'outcome'], unique=False)
    op.create_index('ix_test_results_heal_attempt_id', 'test_results', ['heal_attempt_id'], unique=False)
    op.create_index('ix_test_results_outcome', 'test_results', ['outcome'], unique=False)
    op.create_index('ix_test_results_executed_at', 'test_results', ['executed_at'], unique=False)

    # Heal attempts table
    op.create_table(
        'heal_attempts',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('test_case_id', sa.String(), nullable=False),
        sa.Column('failure_type', sa.Enum('selector_broken', 'api_contract', 'assertion_stale', 'assertion_failed', 'timeout', 'network', 'unknown', name='failuretype'), nullable=False),
        sa.Column('original_steps', sa.Text(), nullable=False),
        sa.Column('proposed_steps', sa.Text(), nullable=False),
        sa.Column('llm_rationale', sa.Text(), nullable=True),
        sa.Column('status', sa.Enum('proposed', 'accepted', 'rejected', 'verified', name='healstatus'), nullable=True),
        sa.Column('proposed_at', sa.DateTime(), nullable=True),
        sa.Column('verified_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['test_case_id'], ['test_cases.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_heal_attempts_id', 'heal_attempts', ['id'], unique=False)
    op.create_index('ix_heal_attempts_test_case_id', 'heal_attempts', ['test_case_id'], unique=False)
    op.create_index('ix_heal_attempts_test_status', 'heal_attempts', ['test_case_id', 'status'], unique=False)
    op.create_index('ix_heal_attempts_status', 'heal_attempts', ['status'], unique=False)


def downgrade() -> None:
    op.drop_table('heal_attempts')
    op.drop_table('test_results')
    op.drop_table('test_cases')
    op.drop_table('pipeline_runs')
    op.drop_table('repositories')
    op.drop_table('organisations')
