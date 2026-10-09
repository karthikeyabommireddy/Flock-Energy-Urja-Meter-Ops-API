"""Write the FastAPI-generated contract to <repo>/openapi.json. Re-run after changing a route or a model.

    python scripts/export_openapi.py

Importing the app needs the credential settings to exist, but generating the schema never talks to the
portal, so harmless placeholders are enough here.
"""

import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
os.environ.setdefault("URJA_PORTAL_EMAIL", "openapi-export@example.invalid")
os.environ.setdefault("URJA_PORTAL_PASSWORD", "not-a-real-password")

from app.main import app  # noqa: E402

OUT = REPO / "openapi.json"

if __name__ == "__main__":
    OUT.write_text(json.dumps(app.openapi(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {OUT}")
