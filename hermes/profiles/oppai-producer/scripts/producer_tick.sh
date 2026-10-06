#!/bin/sh
set -eu
cd /Users/junkawasaki/github/.oppai-producer-runtime/orgs/network-awai/fans-oppai
exec /opt/homebrew/bin/kbb --backend sci --classpath scripts:src scripts/producer_tick.cljk --story
