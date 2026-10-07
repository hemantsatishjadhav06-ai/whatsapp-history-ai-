# One authoritative pilot gateway

The first working backend uses a Python dispatcher and a co-located async HTTPS client
for the official Business API. Keeping current policy and the submit decision in the
same service reduces the prototype's security boundary. The TypeScript package defines
the capability/permit interface and tests an isolated mock adapter; it is not a running
personal-account gateway or integrated live connector.

Owner changes and final submit decisions share process-local workspace guards and SQL
revision/claim checks. SQL remains authoritative; no row lock is held over a slow provider
call. This is suitable for the tested single-gateway development configuration. It is
not a complete distributed fencing or account-cell migration protocol. Multiple active
dispatch gateways require additional service ownership and distributed coordination.
Observed phone events invalidate pending work; delayed/unobserved events cannot recall
a send already submitted to an external provider.

Bounded automation initially supports only exact business-hours questions with an
owner-selected verified low-risk fact and fixed template. Other questions use configured
model drafts for owner review. No model output directly grants automatic send permission.
Groups remain excluded from automatic replies. Calendar, Gmail and other social APIs
require independent connector capabilities and authorization.
