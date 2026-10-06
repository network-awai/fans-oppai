#!/bin/sh
set -eu
unset NBB_CLJK_ROOTS
cd /Users/junkawasaki/github/.oppai-producer-runtime/orgs/network-awai/fans-oppai
exec /opt/homebrew/bin/kbb --backend sci --classpath scripts:src scripts/producer_run.cljk
