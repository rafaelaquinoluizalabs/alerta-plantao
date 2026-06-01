#!/usr/bin/env bash
set -euo pipefail

# Garante execução a partir da raiz do repositório.
cd "$(dirname "$0")"

if [[ -f .venv/bin/activate ]]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi

python main.py
python tools/ajustar_plantonistas.py --ajustar
