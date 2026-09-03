#!/usr/bin/env bash
# Daily archive of settled KXBTCD hours. Run this from cron; it is the only thing
# standing between this repo and permanent data loss.
#
# ADR 022: Kalshi keeps roughly 66 days of settled markets. /events still lists 8000
# events back to 2025-08, but 6443 of them have empty `markets` -- no result, no
# expiration_value, no candlesticks. The boundary is hard. So history CANNOT be
# back-filled: whatever is not archived before it ages out is gone, and the gap is
# permanent. The archive is the only irreplaceable thing here; the code can be rewritten.
#
# ADR 016's reopen condition for cushion_hold (>=130 days) can only be earned forwards,
# by this job running every day. --check says when it has been.
#
#   crontab -e
#   17 * * * * /path/to/BTCHOUR/ops/archive-hourly.sh >> /var/log/btchour-archive.log 2>&1
#
# Hourly rather than daily is deliberate: the pull is incremental and skips what it has,
# so a missed run costs nothing, while a machine that is off at the one daily slot for
# three days costs three days that cannot be recovered.

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"

PYTHON="${PYTHON:-python3}"
DAYS="${ARCHIVE_DAYS:-70}"
WORKERS="${ARCHIVE_WORKERS:-8}"
MAX_LAG_HOURS="${ARCHIVE_MAX_LAG_HOURS:-6}"

echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) archive-hourly ==="

# The pull is incremental: stored events are skipped, so re-running is cheap and safe.
"$PYTHON" research/pull_hourly.py --days "$DAYS" --workers "$WORKERS"

# A puller that successfully pulls nothing exits 0. That is the failure cron will never
# report, so ask separately whether the archive is actually current.
if ! "$PYTHON" research/pull_hourly.py --check --max-lag-hours "$MAX_LAG_HOURS"; then
    echo "archive-hourly: FAILED freshness check -- investigate now, not tomorrow" >&2
    "$PYTHON" research/pull_hourly.py --coverage >&2 || true
    exit 1
fi
