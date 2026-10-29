"""Read-only report of database records whose referenced assets are missing."""
from __future__ import annotations

from studio.database import Database
from studio.model_manager import ModelManager
from studio.service import StudioService


def main() -> int:
    service = StudioService(Database(), ModelManager())
    issues = service.validate_data_integrity()
    if issues:
        print(f"INTEGRITY_WARNINGS={len(issues)}")
        for item in issues:
            print(item)
        return 1
    print("INTEGRITY_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
