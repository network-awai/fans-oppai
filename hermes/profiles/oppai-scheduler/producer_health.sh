#!/bin/sh
set -eu
REPO_ROOT="${OPPAI_REPO_ROOT:-${HOME}/github/.oppai-producer-runtime/orgs/network-awai/fans-oppai}"
cd "$REPO_ROOT"
exec /opt/homebrew/bin/kbb --backend sci --classpath scripts:src scripts/producer_health.cljk --run
