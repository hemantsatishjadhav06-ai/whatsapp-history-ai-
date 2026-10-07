# Milo mobile security and storage

Updated 7 October 2026. This is the implemented storage design and its test
boundary, not a device security certification.

## Session storage

One SecureStore JSON record holds application access token/expiry/session ID,
optional rotating refresh token/fixed expiry, trusted API origin and environment.
`sessionRecord()` whitelists these fields; OAuth responses, user email/profile,
chat archives, WhatsApp keys and model/provider secrets are not persisted.
Native token shape, access lifetime <=24 hours and refresh lifetime <=30 days
are checked, with a small clock allowance. Expired access cannot display private
content until renewal/current bootstrap succeeds.

The key is namespaced by API origin and build environment. HTTPS is mandatory;
explicit loopback HTTP is accepted only in development. Credentials, paths,
queries and fragments are rejected in the configured origin. Requests bind one
captured native bearer and use `credentials: omit` and redirect rejection.
Browser cookies/CSRF sessions are separate. No native API key or OAuth client
secret belongs in `EXPO_PUBLIC_*`.

SecureStore writes use `WHEN_UNLOCKED_THIS_DEVICE_ONLY`. A single ordered queue
serializes deletion and replacement; stale epochs skip queued writes. A system-
browser OAuth result that arrives after leaving sign-in is discarded and its
returned native session is revoked when reachable. Refresh is single-flight;
the server consumes one refresh credential and rotates both secrets without
extending the original deadline. GET reads may retry once after renewal; an
outward mutation is never automatically replayed.

## Private local data

Snapshots, message pages, assistant/evidence results and unsent drafts are in
memory only. No AsyncStorage, SQLite, browser localStorage, notification body,
analytics pipeline or persistent private cache is configured. Draft scope keys
encode owner/workspace/conversation/exact recipient and cannot collide through
delimiter characters. Sign-out/account changes clear private state immediately;
the storage deletion is awaited before a new session is saved. Server sign-out
failure is explicitly reported and can be handled by another authenticated
client's device revocation.

Authorized snapshot replacement removes revoked rows and draft caches for absent
chats. Session epochs fence delayed requests/control acknowledgements. Read
generations fence delayed pages and mutations. Private result entries bind to
the server's `snapshot_version` (or the complete synthetic view): a changed view
hides them during rendering before cleanup runs. An old response is not relabelled
with a new generation. A changed response can only be displayed as the object
re-read from a newly authorized collection; otherwise private fields are cleared.
Owner form text is not silently sent or moved to another recipient.

Deep links contain canonical object references, are gated by application auth,
and call the owner/read-scoped resolver. Opening a link performs no external
operation. Unavailable/foreign/revoked references do not fall back to another
recipient. No push/device routing token can authorize a send.

## Backup and lifecycle boundary

Android backup is disabled in app configuration; the SecureStore plugin also
excludes its encrypted preferences from Android backup. On iOS the device-only
keychain accessibility class is selected. iOS keychain items can outlive app
uninstall: server expiry/revocation remains authoritative. No restore/uninstall,
Keystore/keychain extraction, device-lock, biometric-change or process-kill test
has been run. App-switcher snapshot protection is not implemented or certified.
Physical verification is required before a private pilot.

The 16 cloud helper tests exercise actual session/API/queue/private-result code
with fake storage and network. They verify races and isolation, not hardware
protection. Source files: `lib/security.ts`, `session-guard.ts`, `native-api.ts`,
`private-results.ts`, `use-private-result.ts` and `state.tsx`.
