#!/usr/bin/env bash
#
# mud.sh — control the local-LLM stack: LM Studio server + models + Evennia.
#
#   ./mud.sh start     bring everything up (server, models @ correct context, game)
#   ./mud.sh stop      tear everything down (game, unload models, stop server)
#   ./mud.sh restart   stop then start
#   ./mud.sh status     show what's running
#   ./mud.sh code       just the coding stack (server + coder model, no game)
#
# Context length is the thing that bites: opencode's system prompt is bigger
# than LM Studio's default 4096, so the coder MUST be loaded with a big context
# or opencode gets "tokens to keep ... greater than the context length".
#
set -uo pipefail

ROOT="$HOME/llm-local/mud-wizard"
GAME="$ROOT/wizardmud"
VENV="$ROOT/.venv"
LMS="$HOME/.lmstudio/bin/lms"
EVENNIA="$VENV/bin/evennia"

CODER="qwen/qwen3.6-27b"               # opencode
WIZARD="eva-qwen2.5-32b-v0.2-mlx"      # live in-game game master + puzzle judge (RP-tuned)
# NPC chatter (google/gemma-4-e4b) JIT-loads on first `talk`; prompts are tiny.
# Offline worldbible generation needs Hermes 70B + the Qwen 27B critic instead —
# see world/worldbible.py (not loaded by `start`; load them when authoring content).

CODER_CTX="${CODER_CTX:-32768}"     # override: CODER_CTX=65536 ./mud.sh start
WIZARD_CTX="${WIZARD_CTX:-16384}"

server_up () { "$LMS" server start; }

load_coder () {
  echo "▶ Loading coder $CODER @ ctx $CODER_CTX (for opencode)…"
  "$LMS" load "$CODER" -c "$CODER_CTX" --gpu max -y
}

load_wizard () {
  echo "▶ Loading wizard $WIZARD @ ctx $WIZARD_CTX (for the game)…"
  "$LMS" load "$WIZARD" -c "$WIZARD_CTX" --gpu max -y
}

start () {
  echo "▶ LM Studio server…"; server_up
  load_coder
  load_wizard
  echo "▶ Evennia (first run will prompt you to create a superuser)…"
  ( cd "$GAME" && "$EVENNIA" start )
  echo
  echo "✔ Up."
  echo "    code:  cd $ROOT && opencode"
  echo "    game:  http://localhost:4001   (or telnet localhost:4000)"
  echo
  status
}

code () {
  echo "▶ LM Studio server…"; server_up
  load_coder
  echo "✔ Coding stack up. Run:  cd $ROOT && opencode"
}

stop () {
  echo "▶ Stopping Evennia…"; ( cd "$GAME" && "$EVENNIA" stop ) 2>/dev/null || true
  echo "▶ Unloading models…"; "$LMS" unload --all 2>/dev/null || true
  echo "▶ Stopping LM Studio server…"; "$LMS" server stop 2>/dev/null || true
  echo "✔ Down."
}

status () {
  echo "── LM Studio server ──"; "$LMS" server status 2>&1 | sed 's/^/  /'
  echo "── Loaded models (watch the CONTEXT column) ──"; "$LMS" ps 2>&1 | sed 's/^/  /'
  echo "── Evennia ──"; ( cd "$GAME" && "$EVENNIA" status ) 2>&1 | tail -4 | sed 's/^/  /'
}

case "${1:-}" in
  start)   start ;;
  code)    code ;;
  stop)    stop ;;
  restart) stop; start ;;
  status)  status ;;
  *) echo "usage: $0 {start|code|stop|restart|status}"; exit 1 ;;
esac
