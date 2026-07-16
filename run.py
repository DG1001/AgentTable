#!/usr/bin/env python3
"""Dev entrypoint. Binds 0.0.0.0 (proxy requirement, see AGENTS.md).

    python run.py            # port 8000
    PORT=8000 python run.py
"""
from __future__ import annotations

import os

import uvicorn

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    # reload disabled: keeps in-memory WebSocket connections & task loops alive.
    uvicorn.run("app.main:application", host="0.0.0.0", port=port, reload=False)
