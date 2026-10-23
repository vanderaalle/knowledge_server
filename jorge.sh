#!/usr/bin/env bash
# Start Jorge (if not already running) and open it in an app window.
DIR="$(cd "$(dirname "$0")" && pwd)"
PORT="${JORGE_PORT:-8765}"
URL="http://127.0.0.1:$PORT/"
if ! curl -sf -o /dev/null "$URL"; then
  nohup "$DIR/.venv/bin/python" "$DIR/jorge.py" --port "$PORT" --no-open \
    > "${XDG_CACHE_HOME:-$HOME/.cache}/jorge.log" 2>&1 &
  for _ in $(seq 40); do curl -sf -o /dev/null "$URL" && break; sleep 0.25; done
fi
if command -v google-chrome >/dev/null; then
  exec google-chrome --app="$URL"
else
  exec xdg-open "$URL"
fi
