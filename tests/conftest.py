import pytest
import os
import sys
import uuid
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ["DEMO_MODE"] = "true"
os.environ["DATABASE_URL"] = "sqlite:///./test_ghost_qa.db"
os.environ["ANTHROPIC_API_KEY"] = ""
os.environ["GITHUB_TOKEN"] = "test-token"
os.environ["GITHUB_WEBHOOK_SECRET"] = ""

from app.database import Base
from app.models import *

TestEngine = create_engine("sqlite:///./test_ghost_qa.db", connect_args={"check_same_thread": False})
TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=TestEngine)


@pytest.fixture(scope="session", autouse=True)
def setup_test_db():
    Base.metadata.create_all(bind=TestEngine)
    yield
    TestEngine.dispose()
    import os
    import time
    if os.path.exists("./test_ghost_qa.db"):
        # On Windows, give a moment for file handles to close
        time.sleep(0.1)
        try:
            os.remove("./test_ghost_qa.db")
        except PermissionError:
            # If still locked, just pass - the next test run will overwrite it
            pass


@pytest.fixture
def test_db():
    """Provide a clean database session for each test."""
    from app.database import SessionLocal
    # Patch SessionLocal to use test engine
    import app.database as db_module
    original_session = db_module.SessionLocal
    db_module.SessionLocal = TestSessionLocal

    session = TestSessionLocal()
    # Clear tables
    for table in reversed(Base.metadata.sorted_tables):
        session.execute(table.delete())
    session.commit()
    
    yield session

    session.close()
    db_module.SessionLocal = original_session


@pytest.fixture
def clean_db():
    """Clean database tables for each test."""
    session = TestSessionLocal()
    for table in reversed(Base.metadata.sorted_tables):
        session.execute(table.delete())
    session.commit()
    
    from app.database import SessionLocal
    import app.database as db_module
    original_session = db_module.SessionLocal
    db_module.SessionLocal = TestSessionLocal
    
    yield session

    session.close()
    db_module.SessionLocal = original_session


@pytest.fixture
def org_repo_pipeline(test_db):
    """Create test org, repo, and pipeline run."""
    org = Organisation(
        id=str(uuid.uuid4()), name="test-org", github_org_id="12345"
    )
    test_db.add(org)
    test_db.commit()

    repo = Repository(
        id=str(uuid.uuid4()),
        organisation_id=org.id,
        github_repo_id="67890",
        full_name="test-org/test-repo",
        default_branch="main"
    )
    test_db.add(repo)
    test_db.commit()

    pipeline = PipelineRun(
        id=str(uuid.uuid4()),
        repository_id=repo.id,
        trigger_type="github_pr",
        github_pr_number=42,
        commit_sha="abc123",
        status=PipelineStatus.queued
    )
    test_db.add(pipeline)
    test_db.commit()

    return {"org": org, "repo": repo, "pipeline": pipeline, "db": test_db}
