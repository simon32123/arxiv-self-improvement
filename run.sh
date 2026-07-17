#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

# On this Mac, /usr/local/bin/python3 is an obsolete non-Apple-Silicon build
# that macOS terminates before the script starts. Prefer known-good runtimes.
if [ -x /opt/miniconda3/bin/python3 ]; then
  PYTHON=/opt/miniconda3/bin/python3
elif [ -x /usr/bin/python3 ]; then
  PYTHON=/usr/bin/python3
elif command -v python3 >/dev/null 2>&1; then
  PYTHON=$(command -v python3)
else
  echo "找不到可用的 Python 3。" >&2
  exit 127
fi

exec "$PYTHON" "$SCRIPT_DIR/fetch_arxiv.py" "$@"
