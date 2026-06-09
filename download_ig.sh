#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -lt 1 ]; then
  echo "Usage: $0 <instagram-url> [output-prefix]" >&2
  exit 2
fi

url="$1"
prefix="${2:-instagram_%(id)s}"

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
yt_dlp="$script_dir/.venv/bin/python -m yt_dlp"

if [ ! -x "$script_dir/.venv/bin/python" ]; then
  echo "Missing local Python venv: $script_dir/.venv" >&2
  exit 1
fi

cd "$script_dir"
exec $yt_dlp --no-playlist --restrict-filenames -o "$prefix.%(ext)s" "$url"
