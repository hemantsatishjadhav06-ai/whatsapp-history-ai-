# Google, WhatsApp and intelligence setup

Milo's public origin is `https://web-production-bde60.up.railway.app`.
The website, private API and native app share the same verified Google identity
and SQL owner. Identity does not grant access to WhatsApp or any Google service.
The native public API base is
`https://web-production-bde60.up.railway.app/native-api`; its dedicated proxy
accepts native bearer sessions and does not forward browser cookies.

## Google on the website and app

Create a **Web application** OAuth client in the owner's Google Cloud project.
Configure its consent screen and permitted test users or production publication
as required by Google. Register this JavaScript origin:

```text
https://web-production-bde60.up.railway.app
```

Register this exact authorized redirect URI on the same Web client:

```text
https://web-production-bde60.up.railway.app/api/auth/native/google/callback
```

Set these values privately on Railway API, Jobs and Retention. The Web client ID
is public; the client secret must stay server-side.

| Setting | Value |
| --- | --- |
| `GOOGLE_CLIENT_ID` | The registered Web OAuth client ID |
| `GOOGLE_CLIENT_SECRET` | Its secret, through secure settings |
| `GOOGLE_NATIVE_REDIRECT_URI` | The exact HTTPS callback above |
| `GOOGLE_NATIVE_APP_REDIRECT_URI` | `milo://oauth` |

Website login uses Google Identity Services, a single-use nonce, HttpOnly
session cookies and CSRF-protected writes. Native login opens Google's system
browser through the HTTPS broker. Google returns to the registered HTTPS
callback; Milo returns only a short-lived opaque handoff to the installed app.
The app must still possess its original S256 verifier to exchange that handoff
for its own rotating native session. No Google access token, ID token or client
secret appears in the app URL or bundle. Android's unsupported direct custom
Google redirect is not used. Installed-client IDs alone cannot enable this broker.

Build the app with the public `EXPO_PUBLIC_API_URL` above and a supported
`EXPO_PUBLIC_MILO_ENV`. The iOS identifier is `com.milocompanion.app`; the Android
package is the same. Installed-device redirects and SecureStore must be tested
on actual builds; a JavaScript export is not an installed-device result.

After configuration, verify `/api/auth/config` reports `google_configured` and
`google.native_broker_configured`. Test the same real Google account on both
clients, confirm the same owner/workspace, logout, refresh expiry and revocation.
Do not enable development login on Railway.

## Supported WhatsApp connection

The current connector uses an eligible **WhatsApp Business Cloud** number.
This pilot binds one server-configured number to one exact verified Google
owner. It does not let arbitrary customers claim that number. A general
multi-customer product needs approved Embedded Signup plus tenant-specific
encrypted asset/token grants and revocation; this pilot does not claim that flow.

Configure server-held `WHATSAPP_ACCESS_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`,
`WHATSAPP_APP_SECRET`, `WHATSAPP_VERIFY_TOKEN` and the owning Google account's
exact raw subject as `WHATSAPP_AUTHORIZED_OWNER_SUBJECT`. The raw subject is a
verified server identity, not an email address or an input from a client. Keep
these values out of browser/native storage and public build settings.

After signing in with the intended real Google owner and creating their workspace,
read `/api/integrations/whatsapp/operator-identity?workspace_id=<their-workspace-id>`
in that authenticated browser session. The returned `google_subject` is that
owner's verified raw subject; use it verbatim without a `google:` prefix. This
read-only endpoint returns no tokens, rejects development identities and cannot
read another owner's workspace. Native clients use the same endpoint under
`/native-api/v1` with their own bearer session.

Register Meta's public webhook callback:

```text
https://web-production-bde60.up.railway.app/api/webhooks/whatsapp
```

The public route preserves raw POST bytes and the provider HMAC signature.
Verification queries and signed payloads reach the private API without a
browser cookie, native bearer or internal service credential. Meta verification
alone does not prove message delivery.

In Connections, verify the configured number, then add each exact contact and
its permissions. Reading, retaining, learning, drafting and sending are separate
choices and default to false. Sending also requires recipient opt-in and current
server authority. An unselected contact is not automatically ingested. A
connection lease needs renewal; a gap fences pending actions and requires review.

Eligible Business app **Coexistence** can provide up to 180 days of individual
chat history after approved provider/Embedded Signup onboarding, provider history
sharing and per-contact read/retain consent. Request synchronization within Meta's
documented onboarding window. The request has a durable once-only claim; an
uncertain outcome is not automatically resubmitted. Completion percentages do not
prove complete history. Business employees' outgoing messages are not assumed to
be owner-authored; review exact examples before learning from them.

Consumer WhatsApp QR linking and live group connectivity are not implemented.
Authorized text exports remain the supported personal/group history route.
Sources:
[Google native OAuth](https://developers.google.com/identity/protocols/oauth2/native-app)
and [Meta Business app onboarding](https://developers.facebook.com/docs/whatsapp/embedded-signup/custom-flows/onboarding-business-app-users/).

## Intelligence, owner answers and replies

For a real provider, configure `MODEL_PROVIDER=openai`, a server-held
`MODEL_API_KEY`, an available `MODEL_NAME`, and the verified processing-region
and data-use settings. Model configuration is operator-managed, never chosen
from chat messages. Record exact-model pricing before enabling a monetary quota;
unknown pricing cannot satisfy a configured cost ceiling. Privacy exposes the
configured provider destination and data-use metadata for review; a configured
provider is not evidence that its privacy settings or reply quality were verified.

Owner-confirmed authorship and per-chat read/retain/learn grants enable automatic
local style statistics. Those statistics are bounded and separate for each
contact or group. They do not fine-tune a model or prove access to all account
messages. Retrieval uses bounded recent/relevant evidence from that exact chat;
forgetting, expiry and permission changes remove it from reuse.

Ask about this chat produces an answer for the owner, with source references and
unknown facts. It cannot create a send, grant or memory. The answer expires and
is removed when its source, memory, owner, permission or connection authority
changes. Writing a reply is a separate draft flow; forwarding has its own
audience/route checks and available transport requirements.

Keep `ENABLE_EXTERNAL_SENDS=false` until an eligible test account and an explicitly
opted-in recipient pass real receive, reply, delivery-receipt, pause, phone
takeover and uncertain-outcome tests. No synthetic transport test proves those
provider outcomes. Current account limits, security findings, storage recovery
and capacity evidence are recorded in [readiness](readiness.md) and
[QA](QA_REPORT.md).
