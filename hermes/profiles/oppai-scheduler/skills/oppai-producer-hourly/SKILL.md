---
name: oppai-producer-hourly
description: Read canonical Producer health for oppai-scheduler without submitting or publishing.
---

# Hourly Producer health observation

This scheduler profile observes the Producer. The Producer profile owns the
story generation and publication procedure; do not use an old balanced-generation
recipe or temporary checkout from this observer.

The active cron definition invokes the existing host-local `producer_health.sh`
with `no_agent: true`. The wrapper enters
`~/github/.oppai-producer-runtime/orgs/network-awai/fans-oppai` and invokes
`scripts/producer_health.cljk --run` with the `scripts:src` classpath.
Shell wrappers are excluded from profile exports, so verify its presence on a
new host before enabling the job.

The reader inspects `~/.local/state/oppai-producer/<YYYYMMDDTHHZ>/receipt.json`
and the Producer reviewer job status. Read its JSON result and exit status:
missing or stale receipts, incomplete verification, failed or unverified review,
and manual-review requirements remain actionable observations.

Report the observed status, latest hour/state, age, receipt count and one proposed
next action. If the reader cannot run or its input is missing, report that failure;
do not manufacture a successful receipt or infer publication from generation.

Do not submit, generate, call publication tools, deploy, edit receipts, change cron
definitions, or copy a legacy studio ledger as acceptance evidence. Those operations
require a separate authorized Producer task.
