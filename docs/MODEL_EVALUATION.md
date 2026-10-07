# Model and personalization evaluation

Updated 6 October 2026. No real model provider or owner-reviewed held-out personalization
evaluation has been completed. Current test fixtures exercise the structured proposal and
scope boundaries; they do not establish convincing voice, factual truth or reaction quality.

| Component | Current evidence | Interpretation |
| --- | --- | --- |
| Conversation style statistics | Synthetic human-owner sample tests | Inspectable provisional heuristics; no trained-owner-voice claim |
| Explicit owner rules | Scope/version/edit tests | Owner correction has priority within its permitted conversation |
| Grounded candidate/confirmed memory | Evidence, revision, expiry and suppression tests | Provenance controls; confirmation is not independent external fact verification |
| Mock drafting provider | Deterministic synthetic proposals | Plumbing and missing-fact behavior only |
| Configured structured model boundary | Mock HTTP/schema/ref validation | A real credential/model journey remains untested |
| Verified business-hours automation | Exact fact/template and intent policy tests | Narrow deterministic automation; no unconstrained model autonomy |
| Handoff reaction/action planner | Synthetic policy/action tests | Static safe acknowledgments/clarifications and prior-habit reactions; semantic veto rules are a bounded baseline, not an evaluated general classifier |

Before extending autonomous intents, compare scoped personalization against a generic
baseline using owner-reviewed held-out situations. Use separate conversations for different
people and groups; include no-history cases and intended pilot languages. Evaluate factual
correctness, privacy/audience correctness, date ambiguity, style preference, inappropriate
reactions, unwanted intervention, abstention and human takeover behavior. Reply acceptance
alone is insufficient.

Use genuine verified human owner turns and reactions as evidence. Exclude AI echoes,
uncertain owner/operator output, quoted/forwarded source content, deleted/expired evidence
and templates. Keep training/evaluation sources within their authorized audiences. Do not
make a profile readiness threshold a production quality certification.

Required adversarial cases include requests to reveal other chats, forged scoped source
refs, invented forwarding routes, private facts from another person, expired commitments,
bereavement/laugh mismatch and payment/legal-consent reactions. Models may select only
server-issued scoped references; deterministic policy verifies current authority outside
the model. Missing facts produce a permitted clarification or abstention.

For each real evaluation record pinned model/provider, processing region/data settings,
prompt/schema version, corpus provenance, scope/language/sample counts, budget/quota,
latency, failures and owner review. Synthetic policy tests and real output-quality scores
must be reported separately. Fine-tuning, speech transcription and meeting understanding
are not implemented or evaluated in this increment.

Workspace action/token/cost quotas use a metadata-only reservation ledger. The model boundary
reserves the complete request's UTF-8 byte count plus a 2,048-unit framing allowance and
the 1,000-output-token ceiling before network submission. Synthetic provider tests verify
integer usage reconciliation. Missing/invalid usage or ambiguous failure retains the
conservative reservation as uncertain. Configured cost caps require operator-attested
per-million-token prices for the exact configured model; unverified pricing blocks the
call with `QUOTA_HELD`. These conservative units do not establish the provider's invoiced
cost or quota terms.

`/v1/privacy/model-processing` exposes declared destination/region/data-use/accounting
metadata, without credential values. Configuration is not proof of actual processing
location or privacy terms. No real model usage/billing or region observation has been
measured. Provider pricing and measured token counts need dated independent evidence
before making budget or cost promises to customers.
