This package contains connector contracts and an authenticated **mock** action gateway. It has no Baileys dependency, QR pairing, WhatsApp session, phone connection, or real provider socket.

`npm run check` runs TypeScript checks and connector tests. From the repository root, `uv run python scripts/bridge_smoke.py` runs a disposable Python API and Node gateway, then exercises text, authentic quotes, reactions and native forwarding through real loopback HTTP and the SQL authority endpoint. It verifies that duplicate dispatches keep one SQL attempt and forged destinations fail. All originals and accounts in this walkthrough are synthetic.

The four typed wire operations map the communication capabilities: ordinary and personalized replies use `SEND_TEXT`; authentic quotes use `QUOTE`; emoji reactions use `REACTION`; native forwarding uses `FORWARD`. Contact saving uses the separately authorized Python contact service and reports its actual destination. This Node adapter never claims a WhatsApp, Google, phone OS, or assistant-local contact write. Its capability records include the adapter version, evidence date and test reference, with simulation clearly identified.

For an opt-in local bridge, configure the Python API with `CONNECTOR_GATEWAY_URL=http://127.0.0.1:8090` and a separate `CONNECTOR_GATEWAY_TOKEN` of at least 32 bytes. Supply these environment variables to `npm run bridge`:

| Variable | Value source |
| --- | --- |
| `GATEWAY_TOKEN` | The same separate `CONNECTOR_GATEWAY_TOKEN` configured in Python |
| `PYTHON_INTERNAL_TOKEN` | The API's `INTERNAL_SERVICE_TOKEN` |
| `PYTHON_AUTHORITY_URL` | The configured API origin, default `http://127.0.0.1:8000` |
| `GATEWAY_PORT` | Optional loopback port, default `8090` |

The executable listens on `127.0.0.1` and refuses `ENVIRONMENT=production`. Plain HTTP authority origins must use loopback; configured remote authority uses HTTPS. Requests never select an authority URL, provide a permission reader, or redirect authentication to another endpoint.

`POST /v1/actions` requires the gateway token and a strict envelope containing immutable action/account/chat references, the exact recipient, payload fingerprint and connector fence. Each newly submitted action makes an authenticated `POST /internal/dispatch-authority` call to Python immediately before its simulated socket submission. Python resolves the current SQL action, submission attempt, permissions, grants, lease, source revisions, routes and controls. Node checks all returned identity bindings and a bounded five-second authority deadline. Quotes and forwards use the private authentic provider original returned by authority; reactions use its authentic key. Text exports cannot reconstruct these originals.

Acceptance is separate from delivery. An uncertain response is preserved without blind retry. The Python SQL attempt ledger provides durability; Node's duplicate/fence cache is bounded process memory and disappears on restart. A missing `GET /v1/actions/{id}` result means unknown, and never authorizes resubmission. Real network providers retain a race after the final authority read, and require a durable adapter/session service and external account evidence before production eligibility.
