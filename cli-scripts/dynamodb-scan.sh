#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "Usage: ./dynamodb-scan.sh <table-name> [limit]"
  exit 1
fi

TABLE_NAME="$1"
LIMIT="${2:-20}"

aws dynamodb scan \
  --table-name "$TABLE_NAME" \
  --max-items "$LIMIT" \
  --output json
