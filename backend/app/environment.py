"""Load optional application settings before installed provider composition."""

import os
from pathlib import Path

from dotenv import load_dotenv


def load_application_environment() -> Path | None:
    """Load the explicit file or the source checkout's root .env, preserving process values."""
    configured = os.environ.get("AICS_ENV_FILE")
    if configured:
        path = Path(configured).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"AICS_ENV_FILE does not exist or is not a file: {path}")
    else:
        path = Path(__file__).resolve().parents[2] / ".env"
        if not path.is_file():
            return None
    load_dotenv(path, override=False)
    return path
