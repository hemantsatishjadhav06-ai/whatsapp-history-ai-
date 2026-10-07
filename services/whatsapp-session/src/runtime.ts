import { silenceDependencyConsole } from "./isolated-console.ts";
// Install before the first dynamic Baileys/libsignal import. Only this dedicated process is affected.
silenceDependencyConsole();
await import("./start.ts");
