/** The isolated session process must not serialize third-party Signal console arguments. */
export function silenceDependencyConsole(): void {
  for (const name of ["log", "info", "warn", "error", "debug", "trace", "dir", "dirxml", "table", "assert",
    "group", "groupCollapsed", "groupEnd", "time", "timeEnd", "timeLog", "count", "countReset", "clear"] as const) {
    Object.defineProperty(console, name, { value: () => {}, writable: false, configurable: false });
  }
}
