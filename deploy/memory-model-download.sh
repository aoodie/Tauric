#!/bin/sh
set -eu
ollama serve >/tmp/model-download-server.log 2>&1 &
task_server_pid=$!
trap 'kill "$task_server_pid"' EXIT
task_attempt=0
until ollama list >/dev/null 2>&1; do
  task_attempt=$((task_attempt + 1))
  if [ "$task_attempt" -ge 30 ]; then exit 1; fi
  sleep 1
done
ollama pull qwen3:4b
ollama pull nomic-embed-text:v1.5
ollama create tauric-memory:4b -f /setup/Memory.Modelfile
ollama create tauric-heartbeat:4b -f /setup/Heartbeat.Modelfile
ollama list
