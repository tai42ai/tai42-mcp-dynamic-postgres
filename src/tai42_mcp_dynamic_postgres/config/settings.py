"""PostgreSQL connection and pool settings read from ``PG_*`` environment variables."""

from pathlib import Path

from dotenv import load_dotenv
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings

load_dotenv()


class PostgresSettings(BaseSettings):
    """Connection, pool, and generated-tools-directory settings from the ``PG_*`` env vars."""

    model_config = {
        "env_prefix": "PG_",
    }
    tools_dir: str = Field(
        default_factory=lambda: str(Path.home() / ".cache" / "tai42-mcp-dynamic-postgres" / "tools"),
        description="Directory for generated tool files",
        validation_alias="TOOLS_DIR",
    )
    host: str = Field("localhost", description="PostgreSQL host")
    port: int = Field(5432, description="PostgreSQL port")
    # No defaults: db, user, and password must be supplied; a missing value raises
    # a ValidationError at startup rather than connecting with a default.
    db: str = Field(description="Database name")
    user: str = Field(description="Database user (use a least-privilege role)")
    password: SecretStr = Field(description="Database password")

    # Statement timeout (milliseconds) applied to every pooled connection. Caps a
    # runaway query so it cannot hold a pool slot indefinitely. 0 disables it.
    statement_timeout: int = Field(30000, description="Per-connection statement_timeout in milliseconds (0 disables)")

    # Pool configuration
    pool_min_size: int = Field(1, description="Minimum number of pooled connections")
    pool_max_size: int = Field(10, description="Maximum number of pooled connections")
    pool_timeout: int = Field(10, description="Pool acquire timeout in seconds")
    pool_max_lifetime: int = Field(300, description="Max lifetime of connection in seconds")


# Populated from PG_*/TOOLS_DIR env vars. db/user/password have no defaults, so
# construction fails loudly if they are missing.
pg_settings = PostgresSettings()  # pyright: ignore[reportCallIssue]
