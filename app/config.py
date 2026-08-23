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
    UIPATH_TEST_FOLDER: str = "Shared/Ghost-QA"
    UIPATH_TEST_PROCESS: Optional[str] = None
    UIPATH_PAT: Optional[str] = None

    # Slack (optional)
    SLACK_BOT_TOKEN: Optional[str] = None
    SLACK_CHANNEL: str = "ghost-qa-alerts"

    # Security
    SECRET_KEY: str = "change-me-in-production"


settings = Settings()
