#!/usr/bin/env python3
"""Apply retention governance to existing ClickHouse tables."""
from __future__ import annotations

from db.init_db import get_client, governance_migration_statements


def main() -> None:
    client = get_client()
    for stmt in governance_migration_statements():
        print(f"→ {stmt}")
        client.command(stmt)
    print("✓ Retention governance applied.")


if __name__ == "__main__":
    main()
