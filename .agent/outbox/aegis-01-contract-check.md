# Orchestrator in-flight AA contract observations

2026-09-09, implementation not yet finished. Official source re-opened: https://artificialanalysis.ai/data-api/docs, Free endpoint response example.

1. Confirmed mismatch (already delivered to Opus resume): default completion path must be `performance.median_end_to_end_response_time_seconds`, not root `median_end_to_end_seconds`. Fixture and negative tests must match.
2. Official Free endpoint query documents `page` only. Current live adapter additionally sends `page_size`, an undocumented parameter. Remove the extra query unless explicitly supported by verified official contract. Response pagination's `page_size` must still be validated/preserved; a response field is not automatically a supported query parameter. Cover live adapter wire request in a mocked HTTP contract test.
3. Root `intelligence_index_version` can be fractional (official example `4.1`); current Decimal handling appears appropriate. Ensure fractional-version regression coverage; do not truncate to int.
4. Official creator object example has id/name only, not creator slug. Model slug remains required. Current creator parser does not require slug; retain that compatibility.

These are focused observations, not an implementation approval. Recheck against the final diff before a packet verdict.
