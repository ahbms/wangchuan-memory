#!/usr/bin/env bash
set -euo pipefail

# 5-minute WangChuan CLI demo.
# Optional: set WANGCHUAN_HOME to isolate demo data.
: "${WANGCHUAN_HOME:=$(mktemp -d)}"
export WANGCHUAN_HOME

python3 -m wangchuan paths --json
python3 -m wangchuan remember "User prefers concise CLI output." --importance 0.9 --tag preference --tag style --json
python3 -m wangchuan remember "WangChuan stores data in SQLite." --importance 0.75 --tag fact --tag storage --json
python3 -m wangchuan recall "concise output and storage" --limit 5 --json
python3 -m wangchuan status --json
