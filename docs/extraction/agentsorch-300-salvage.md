# AgentsOrchNext #300 salvage

Tracking: #15

Source:

```text
oalangomes/AgentsOrchNext PR #300
head 8655d4757f8668b3bc544e896ce0a1df645cc620
```

## Moved

Generic product hardening only:

- embedding transport envelope:
  - `timeout_seconds`
  - `request_batch_size`
- bounded retry for known transient Ollama local transport failures;
- Solr/OpenSearch wiring for the embedding transport envelope;
- deterministic fallback when rich structural chunk spans overlap unsafely;
- explicit `base_config` input for repository indexing before KnowledgePack treatment.

## Already present

The Projection v3 contract revision/fingerprint update from #300 was already present
in FetchCore and was not re-applied.

## Skipped

The following remain historical AgentsOrch/SAP benchmark material and are not part
of FetchCore:

- SAP paired G0/G1 harness and benchmark protocol;
- frozen 15K benchmark controls and corpus-specific values;
- benchmark workflow/provisioning changes;
- runner capability routing and local Ollama runner labels;
- WSL/Windows Ollama discovery;
- execution reports and provider-runtime investigation;
- AgentsOrch lifecycle/context/provider-specific behavior.

## Scope note

Qdrant transport settings are not changed by this salvage. The extracted Qdrant
adapter already owns its own endpoint timeout/batch settings; unifying those with
the backend-neutral embedding envelope would be a separate contract evolution,
not a faithful salvage from #300.

After this change is merged and CI passes, AgentsOrchNext #300 no longer contains
generic retrieval/indexing hardening that FetchCore still needs.
