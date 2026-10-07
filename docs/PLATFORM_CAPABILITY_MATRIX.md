# Platform capability evidence

Evidence updated: 7 October 2026. Capability observations use `supported`, `unsupported`,
`unknown` and `unavailable`; a Mock/Live-tested development result does not establish
production platform eligibility. The final dispatcher must check the exact stored
account/adapter capability and current authority again.

| Capability | Export-only | Mock development connector | Business Cloud adapter | Personal linked device |
| --- | --- | --- | --- | --- |
| Authorized text history | Owner-provided file | Synthetic | No general history sync implemented | Unknown; no adapter shipped |
| Observed import coverage | Supported locally | Synthetic records only | Live history coverage unknown | Unknown |
| QR / pairing code | Unsupported | Unsupported | Unsupported by current adapter | Planned; unverified |
| Primary-phone continuity | No device connection | Not applicable | Unknown; requires eligible coexistence test | Unknown |
| Receive/edit/delete text | File snapshot only | Synthetic | Signed text webhook; edit/delete coverage needs provider test | Unknown |
| Ordinary text send | Unsupported | Synthetic acceptance | Implemented; actual eligible account/delivery unverified | Unknown |
| Native quote | Unsupported: file has no native original | Synthetic authentic-text fixture; same-chat source required | Not enabled by current transport | Unknown |
| Native reaction | Unsupported: file has no native original | Synthetic authentic-text fixture, verified habit and conservative semantics | Not enabled by current transport | Unknown |
| Native forward | Unsupported: file has no native original | Synthetic authentic-text fixture and exact two-audience route | Not enabled by current transport | Unknown |
| Delivery/read reconciliation | Unsupported | Synthetic correlated receipt | Receipt handling implemented; actual provider reconciliation unverified | Unknown |
| Group read/send and membership | File may describe a group | Synthetic; separate group authority required | Current adapter restricted to contact text | Unknown |
| Assistant-local contact save | Text export cannot verify a live identity | Exact identity SQL save; no external write | Local SQL capability independent of external contact write | Local SQL capability requires trusted eligible identity/source |
| WhatsApp contact write | Unsupported | No external write | Unavailable | Unknown; version-specific eligibility/device tests required |
| Google Contacts write | Separate authorization required | No external write | Separate authorization required | Separate authorization required |
| Native phone contact sync | Unsupported | No external write | Unavailable | Unknown; OS route/device permission required |

The Node package is a simulation, not a Baileys/WhatsApp session service. Its pinned runtime
is Node 24, with TypeScript 7.0.2 and `@types/node` 24.19.1. No Baileys version is pinned
because no Baileys adapter is shipped. The existing Business adapter uses the configured
Graph API version, default `v23.0`; this is a configuration default, not a current eligibility
or compatibility certification.

Evidence references: [QA report](QA_REPORT.md), backend `tests/test_webhooks.py`,
`tests/test_messaging.py`, `tests/test_automation.py`, and
`tests/test_native.py`, `tests/test_actions.py`, `tests/test_people.py`, and
`services/connector-gateway/tests/action-bridge.test.ts`. The local bridge smoke exercises
actual Python/Node HTTP with four simulated wire operations, four durable SQL attempts,
duplicate suppression and forged-recipient rejection. The Node typecheck and 33 tests
passed. This demonstrates service integration, without a provider operation.
Account-specific native operation results remain unknown until they
are measured on an explicitly permitted eligible test account.

Before marking a real capability supported, record adapter version, account type/market,
test date, test reference, actual source availability, recipient/audience restrictions,
primary-phone result where relevant, and observed receipt/reconciliation behavior. Account
connection alone does not authorize sending or cross-chat disclosure. Business customer
information must not be shared with another customer through an owner forwarding grant.

## Milo client and device contracts

| Capability | Implemented contract | Evidence / remaining boundary |
| --- | --- | --- |
| Web Google identity | Nonce-bound verified claim exchange, HttpOnly cookie and CSRF-protected mutations | Synthetic verification tests; registered real client/origin journey pending |
| Native Google identity | iOS/Android audience, single-use nonce and S256 proof exchange | Synthetic verification tests; actual system OAuth/redirect/device journey pending |
| Native access and refresh | Hash-only secrets, bounded access, rotating single-use refresh and original <=30-day deadline | CAS/concurrency/expiry/revoke fixtures; installed SecureStore behavior separately unverified |
| Security/device sessions | Owner-scoped `/auth/sessions` list/revoke plus native logout | Application session management; no APNs/FCM registration or push token system |
| Authorized UI snapshot | `/ui/bootstrap`, default 30/max 100 readable conversation page with cursor, bounded resources | Scoped API fixture tests; full 10,000-chat/200,000-message UX not measured |
| Safe deep-link lookup | Authenticated `/ui/resolve` checks exact object ownership/scope and has no side effect | Synthetic substitution/deleted-object negatives; installed links pending |
| Mobile recovery | Client fetches authorized snapshot and discards stale session responses | Native session/security helpers and all-platform export passed; no installed lifecycle, complete `/mobile/sync` resume/gap protocol or live provider recovery proof |
| Voice/transcription | Type path and explicit unavailable state | No configured speech provider, ambient capture or verified native microphone integration |
| Private push | Unavailable | Actual APNs/FCM, permissions, generic payloads and authenticated deep links need separate implementation/test |
| Native OS Contacts | Unavailable | Local SQL save is separate; destination/device intent plus actual OS receipt not yet shipped |
| Gmail/Calendar/social/meetings | Planned | Independent grants, eligible APIs and actual operation evidence required |

All client/provider states must retain these boundaries. The current UI's synthetic
workspace is a demonstration; changing a UI selection never establishes an eligible
WhatsApp session. See [parity](MOBILE_PARITY_REPORT.md), [test plan](TEST_PLAN.md) and
[reliability](RELIABILITY_AND_RECONCILIATION.md).
