# oppai-scheduler — Producer receipt health observer

This profile monitors the canonical Producer receipts once per hour. Its active
cron job is `oppai-producer-health`, runs `producer_health.sh` with `no_agent: true`,
and performs no generation, submission, review mutation, deployment or publication.
The legacy story-generation job stays disabled.

## Sources

- Runtime: `~/github/.oppai-producer-runtime/orgs/network-awai/fans-oppai`
- Health implementation: `scripts/producer_health.cljk` in that runtime
- Canonical receipts: `~/.local/state/oppai-producer/<YYYYMMDDTHHZ>/receipt.json`
- Reviewer status: `~/.hermes/profiles/oppai-producer/cron/jobs.json`

The host health wrapper enters the runtime before invoking the health reader.
It is a host-local compatibility wrapper; the profile export does not export
shell scripts. A new host needs that wrapper before enabling this job.

## Reporting

Read the health reader's JSON and exit status. Report missing, stale, failed or
uncertain state as observed; a failed or unavailable check is not healthy.
Use canonical receipts rather than legacy studio or scheduler ledgers.

Any manual follow-up is limited to one finding and a proposed next action.
This profile does not run the Producer generation or publication workflow,
change receipts, or restart jobs to fill missing evidence.
