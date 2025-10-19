#!/usr/bin/env bash
set -euo pipefail

PYTHON_BIN="${PYTHON_BIN:-python3}"
VENV_DIR="${VENV_DIR:-.venv}"

if ! command -v "${PYTHON_BIN}" >/dev/null 2>&1; then
  echo "Python interpreter '${PYTHON_BIN}' not found. Set PYTHON_BIN to a valid executable." >&2
  exit 1
fi

if [ ! -d "${VENV_DIR}" ]; then
  echo "Creating virtual environment at '${VENV_DIR}' using '${PYTHON_BIN}'..."
  "${PYTHON_BIN}" -m venv "${VENV_DIR}"
else
  echo "Using existing virtual environment at '${VENV_DIR}'."
fi

# shellcheck disable=SC1090
source "${VENV_DIR}/bin/activate"

python -m pip install --upgrade pip
pip install -r requirements.txt

echo
echo "Virtual environment ready."
echo "Activate it with: source ${VENV_DIR}/bin/activate"
