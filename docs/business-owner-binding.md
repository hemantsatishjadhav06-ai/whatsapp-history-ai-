# Single-number Business Cloud owner binding

The optional Business Cloud transport uses one operator-configured phone-number ID
and server-held Meta credentials. It is not a multitenant Meta OAuth/account-linking
integration. A public Google sign-in or knowledge of that number ID must never
allow someone to claim those credentials.

Set `WHATSAPP_AUTHORIZED_OWNER_SUBJECT` privately at runtime to the exact raw `sub`
from the intended operator's verified Google identity. The corresponding stored
application identity is `google:<sub>`. Do not use an email address, application
user ID, role, `google:` prefix or value submitted by a customer. Only that exact
verified Google identity may create or verify the configured Business connector.
All API and worker processes need the same binding, number ID and encryption key.

The default binding is blank. Blank or different identity blocks new claims,
verification and Business draft submission, and ignores signed webhook messages
and receipts for already registered unauthorized workspaces. Existing rows are
checked again; deploying this guard does not transfer an old connector, erase
previously retained data or recall a provider submission already in progress.
Handle a prior unauthorized registration through reviewed incident cleanup.

Outbound checks use the persisted workspace owner's verified subject, so a worker
actor or a client-supplied role cannot bypass the binding. The final dispatch
check repeats this immediately before submission. Webhook mapping repeats the
check after waiting for the workspace guard. Verification also requires the
stored account ID to match the currently configured phone-number ID before any
Meta request. Mock/export-only paths keep their existing separate restrictions.

The focused tests exercise nonce-bound Google exchanges with simulated verified
claims, unauthorized first claims, same-email development identities, existing
unauthorized rows, exact-owner verification, configuration revocation and late
dispatch/webhook checks. Meta responses are fixtures; no real account is contacted.
The binding itself is not proof of number eligibility or delivery. Keep
`ENABLE_EXTERNAL_SENDS=false` until the eligible operator account, opt-in/window,
lease renewal, accepted/delivered receipts and stop/recovery have live evidence.
Personal WhatsApp pairing/sync and general live Auto/native operations still need
their separate implementations; this guard does not enable them.
