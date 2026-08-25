from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Application
    APP_NAME: str = "Ghost QA"
    APP_ENV: str = "development"
    APP_DEBUG: bool = True
    APP_PORT: int = 8000
    APP_HOST: str = "0.0.0.0"
    DEMO_MODE: bool = False
    AUTO_APPROVE: bool = True

    # Database
    DATABASE_URL: str = "sqlite:///./ghost_qa.db"
    DATABASE_URL_SQLITE: str = "sqlite:///./ghost_qa.db"

    # GitHub
    GITHUB_TOKEN: Optional[str] = None
    GITHUB_WEBHOOK_SECRET: Optional[str] = None

    # Anthropic Claude
    ANTHROPIC_API_KEY: Optional[str] = None

    # XAI (Grok) — alternative AI provider
    XAI_API_KEY: Optional[str] = None
    GROK_MODEL: str = "grok-4-1-fast-reasoning"

    # UiPath
    UIPATH_CLIENT_ID: Optional[str] = None
    UIPATH_CLIENT_SECRET: Optional[str] = None
    UIPATH_TENANT_NAME: Optional[str] = None
    UIPATH_ORG_ID: Optional[str] = None
    UIPATH_ENVIRONMENT_ID: Optional[str] = None
    UIPATH_TEST_FOLDER: str = "GhostQA"
    UIPATH_EXECUTION_TIMEOUT_SECONDS: int = 300
    UIPATH_TEST_MANAGER_BASE: str = "https://cloud.uipath.com"

    # Slack (optional)
    SLACK_BOT_TOKEN: Optional[str] = None
    SLACK_CHANNEL: str = "ghost-qa-alerts"

    # Security
    SECRET_KEY: str = "change-me-in-production"

    # Auth & JWT
    JWT_EXPIRY_MINUTES: int = Field(default=60, ge=15, le=1440)
    AUTH_USERS: str = '{"admin@ghost.qa": {"password_hash": "changeme", "role": "approver"}}'

    # Rate limiting
    RATE_LIMIT_ENABLED: bool = True

    # Approval SLA
    APPROVAL_SLA_WARN_HOURS: int = 4
    APPROVAL_SLA_REJECT_HOURS: int = 24

    # UiPath Action Center
    UIPATH_ACTION_CENTER_BASE: str = "https://cloud.uipath.com"


settings = Settings()
