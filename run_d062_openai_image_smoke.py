"""Explicit D062 paid smoke launcher with repository-local backend imports."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / "backend"))

from app.tooling.d062_openai_image_smoke import main


if __name__ == "__main__":
    main()
