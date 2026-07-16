#!/usr/bin/env python3
"""Seed script (spec §9, Phase 0): create a group with N users and print their
magic-links. Usage:

    python seed.py --group "Freitagsrunde" --members Alex,Bea,Chris,Dana --port 8000
"""
from __future__ import annotations

import argparse
import os

from app.service import bootstrap_group


def magic_link(token: str, port: int) -> str:
    """Build the external magic-link. In a XaresAICoder workspace we derive it
    from VSCODE_PROXY_URI ({{port}} placeholder); otherwise fall back to localhost."""
    template = os.environ.get("VSCODE_PROXY_URI")
    if template:
        base = template.replace("{{port}}", str(port)).rstrip("/")
    else:
        base = f"http://localhost:{port}"
    return f"{base}/?t={token}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--group", default="Testgruppe")
    ap.add_argument("--members", default="Alex,Bea,Chris,Dana",
                    help="comma-separated display names")
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args()

    names = [n.strip() for n in args.members.split(",") if n.strip()]
    group_id, members = bootstrap_group(args.group, names)

    print(f"\n✅ Gruppe '{args.group}' (id={group_id}) mit {len(members)} Mitgliedern angelegt.\n")
    print("Magic-Links (jeweils einer Person geben):")
    for m in members:
        print(f"  • {m['name']:<10} {magic_link(m['token'], args.port)}")
    print()


if __name__ == "__main__":
    main()
