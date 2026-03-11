#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 2 ]]; then
  echo "Usage: ./upload-to-s3.sh <bucket-name> <file-path> [s3-key]"
  exit 1
fi

BUCKET_NAME="$1"
FILE_PATH="$2"
OBJECT_KEY="${3:-$(basename "$FILE_PATH")}" 

aws s3 cp "$FILE_PATH" "s3://$BUCKET_NAME/$OBJECT_KEY"
echo "Uploaded $FILE_PATH to s3://$BUCKET_NAME/$OBJECT_KEY"
