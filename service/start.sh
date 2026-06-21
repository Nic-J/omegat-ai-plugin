#!/usr/bin/env zsh
cd "$(dirname "$0")"
uv run uvicorn main:app --host 127.0.0.1 --port 8000
