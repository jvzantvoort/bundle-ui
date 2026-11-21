#!/usr/bin/env bash
#
# Keep openapi.yaml identical to the canonical copy in bundle-server.
#
#   scripts/sync-openapi.sh          replace openapi.yaml with the canonical copy
#   scripts/sync-openapi.sh --check  fail if openapi.yaml differs from it
#
# OPENAPI_SOURCE overrides where the canonical copy is fetched from, for example
# a bundle-server branch or a local checkout (file:///path/to/openapi.yaml).
set -euo pipefail

source_url="${OPENAPI_SOURCE:-https://raw.githubusercontent.com/jvzantvoort/bundle-server/main/openapi.yaml}"
target="$(cd "$(dirname "$0")/.." && pwd)/openapi.yaml"

canonical="$(mktemp)"
trap 'rm -f "$canonical"' EXIT
curl --fail --silent --show-error --location "$source_url" --output "$canonical"

case "${1:-}" in
--check)
	if diff -u --label "openapi.yaml" --label "$source_url" "$target" "$canonical"; then
		echo "openapi.yaml matches $source_url"
	else
		echo "openapi.yaml differs from $source_url; run scripts/sync-openapi.sh" >&2
		exit 1
	fi
	;;
"")
	if cmp -s "$target" "$canonical"; then
		echo "openapi.yaml is already up to date"
	else
		cp "$canonical" "$target"
		echo "openapi.yaml updated from $source_url"
	fi
	;;
*)
	echo "usage: $0 [--check]" >&2
	exit 2
	;;
esac
