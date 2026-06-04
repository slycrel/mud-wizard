#!/usr/bin/env bash
#
# Download the 3-model lineup into LM Studio WHILE YOU STILL HAVE WIFI.
#
# The ids below are best-guess. Exact MLX repo names change — if a `lms get`
# can't resolve, run `lms get <partial-name>` interactively (it lists matches)
# or search the model catalog inside the LM Studio app. Always prefer the
# **MLX** build over GGUF on Apple Silicon.
#
set -uo pipefail

echo "Prereq: LM Studio installed and the CLI bootstrapped."
echo "  open -a 'LM Studio'        # run once"
echo "  ~/.lmstudio/bin/lms bootstrap   # if 'lms' isn't on PATH yet"
echo

get () {
  echo ">>> lms get $1"
  lms get "$1" || echo "!!! couldn't resolve '$1' — search for it in LM Studio instead"
  echo
}

# Coder (opencode)        — Qwen 3.6 27B dense, MLX
get qwen3.6-27b-instruct-mlx

# Wizard / game master    — Qwen 3.6 35B-A3B MoE, MLX
get qwen3.6-35b-a3b-mlx

# NPC chatter (default)   — Gemma 3 4B, MLX
get gemma-3-4b-it-mlx

# Optional NPC backups (uncomment to fetch):
# get qwen3.6-4b-instruct-mlx
# get llama-3.2-3b-instruct-mlx

echo "Downloaded models:"
lms ls
