#!/usr/bin/env python3
"""Seed script (spec §9, Phase 0): create a group with N users and print their
magic-links. Usage:

    python seed.py --group "Freitagsrunde" --members Alex,Bea,Chris,Dana --port 8000
"""
from __future__ import annotations

import argparse
import os

from app.service import bootstrap_group


def magic_link(token: str, port: int, base_url: str | None = None) -> str:
    """Build the external magic-link.

    ``base_url`` (e.g. a mapped subdomain like https://table.example.org) wins if
    given. Otherwise derive it from VSCODE_PROXY_URI ({{port}} placeholder), else
    fall back to localhost."""
    if base_url:
        base = base_url.rstrip("/")
    else:
        template = os.environ.get("VSCODE_PROXY_URI")
        base = template.replace("{{port}}", str(port)).rstrip("/") if template else f"http://localhost:{port}"
    return f"{base}/?t={token}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--group", default="Testgruppe")
    ap.add_argument("--members", default="Alex,Bea,Chris,Dana",
                    help="comma-separated display names")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--base-url", default=None,
                    help="explicit public base URL, e.g. https://table.example.org")
    args = ap.parse_args()

    names = [n.strip() for n in args.members.split(",") if n.strip()]
    group_id, members = bootstrap_group(args.group, names)

    print(f"\n✅ Gruppe '{args.group}' (id={group_id}) mit {len(members)} Mitgliedern angelegt.\n")
    print("Magic-Links (jeweils einer Person geben):")
    for m in members:
        print(f"  • {m['name']:<10} {magic_link(m['token'], args.port, args.base_url)}")
    print()


if __name__ == "__main__":
    main()
