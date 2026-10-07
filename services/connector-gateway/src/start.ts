/** Explicitly opt-in local mock bridge; this executable never opens a WhatsApp socket. */
import { createActionGateway, pythonAuthorityClient } from "./bridge-server.ts";

if (process.env.ENVIRONMENT === "production") throw new Error("Mock gateway is unavailable in production");
const port = Number(process.env.GATEWAY_PORT ?? "8090");
if (!Number.isSafeInteger(port) || port < 1 || port > 65535) throw new Error("Invalid GATEWAY_PORT");
const authorizer = pythonAuthorityClient({ authorityUrl: process.env.PYTHON_AUTHORITY_URL ?? "http://127.0.0.1:8000",
  internalToken: process.env.PYTHON_INTERNAL_TOKEN ?? "" });
const { server } = createActionGateway({ token: process.env.GATEWAY_TOKEN ?? "", readCurrentAuthority: authorizer });
server.listen(port, "127.0.0.1", () => {
  process.stdout.write(`Mock action gateway listening on 127.0.0.1:${port}; simulation only.\n`);
});
for (const signal of ["SIGINT", "SIGTERM"] as const) {
  process.on(signal, () => { server.close(() => { process.exit(0); }); });
}
