#!/usr/bin/env sh
set -eu

if [ "${MODE:-platform}" = "agent" ]; then
  exec python /app/agent.py
fi

exec python /app/server.py
