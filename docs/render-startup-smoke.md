# Render database startup acceptance

[render_startup_smoke.py](../scripts/render_startup_smoke.py) exercises the real
`assistant.render_entrypoint` in separate Python processes against a uniquely
named disposable PostgreSQL database. It checks migration serialization and
worker startup independently of simulated Render responses. It does not contact
Render, a social provider or a model.

Run from the repository root with its locked Python dependencies installed and
local PostgreSQL available:

```sh
UV_CACHE_DIR=/workspace/.cache/uv uv run python scripts/render_startup_smoke.py --output .local/render-startup-smoke.json
```

The script selects credentials from `TEST_DATABASE_URL` when present, otherwise
from `Settings().database_url` using the usual environment/`.env` configuration.
Keep those values in protected environment variables or a private ignored `.env`
file. Do not put a connection string or encryption key in command arguments.
The selected role needs database creation/deletion permissions and permission
to inspect its own PostgreSQL sessions. CI can use its local PostgreSQL service
and existing protected `TEST_DATABASE_URL` without changing the command.

Only `localhost`, `127.0.0.1` and `::1` are accepted, and every resolved address
must be loopback. The script rebuilds the connection from those authority fields,
discards connection query options, and pins a verified loopback `hostaddr` so
inherited libpq settings cannot redirect it. It
connects to the administrative `postgres` database only to create and remove its
own `milo_render_startup_<random>` database. It never migrates, clears or drops
the configured database or the shared test database. PostgreSQL installations
without an administrative `postgres` database are unsupported by this check.

The startup observation and child-completion deadline is 90 seconds;
`--timeout` accepts 10–300 seconds. Schema verification checks the remaining
deadline before each query. Individual supervisor SQL statements are bounded
to five seconds and child migration statements to 60 seconds. An in-flight
supervisor statement can extend the deadline by up to five seconds; bounded
cleanup runs afterward and terminates only processes created by the script.
Every cleanup stage is attempted even if an earlier one fails. Run without
unrelated database load when using the result as release evidence.

The acceptance sequence verifies:

1. The disposable database has no public tables or Alembic version table.
2. A separate `wait-schema` process queries the absent version table and remains
   running before either migration process starts. A constant internal marker
   records PostgreSQL's missing-table error for that exact helper query. It
   avoids relying on a timing-sensitive sample of the query before rollback.
3. Two separate migration processes actually wait on the helper's exact advisory
   lock in that database. The supervisor briefly holds that lock to make the
   observation deterministic, then releases it.
4. Both migration processes and the original waiter exit successfully. The
   resulting version rows match the repository's complete expected head set.
   At the current revision, that is one head, `86b7bbad6fc1`, across eight
   migrations.
5. Every repository revision is applied exactly once across the two migration
   processes. A small observer enables only Alembic's revision-step records;
   this does not modify the entrypoint or migrations. Complete child output is
   captured in temporary private files, parsed internally and removed. Only
   revision counts enter the JSON evidence.
6. A third migration process applies zero revisions and leaves the exact head,
   tables, columns, indexes, constraints and row counts unchanged. Business
   tables remain empty, and the disposable database must stay below 100 MB.
7. All owned child processes stop and the disposable database is removed.

The script generates a fresh Fernet key in memory for its child environments,
disables authentication shortcuts, models and external sending, and leaves
configured application encryption keys unchanged. It writes a redacted JSON
report with timestamps, process IDs, revision counts, schema hash and cleanup
results. It never publishes connection strings, keys, SQL exceptions or child
log contents. A failed check exits nonzero and reports only its phase and error
class; `disposable_database_removed: false` requires inspecting that uniquely
named disposable database through protected local administration.

This evidence establishes local PostgreSQL startup behavior for the tested
source. It does not establish a live Render release, provider delivery, managed
database backup recovery, schema compatibility with already running workers or
a multi-service atomic deployment. Those remain separate checks in
[the Render deployment guide](render.md).

The final-source local run passed on 2026-10-07 at
11:30:55.586615–11:31:06.901547 UTC in 11.316 seconds. Both real migration
processes were observed waiting on the advisory lock, then exited zero with
upgrade counts `[8, 0]`. The pre-schema waiter exited zero at the exact head
`86b7bbad6fc1`; the repeat exited zero with no upgrades and the same schema
fingerprint. The resulting schema had 37 empty business tables plus the Alembic
version table, occupied 9,884,131 bytes, and was removed with all owned child
processes stopped. Provider and model calls were zero. The ignored local
evidence artifact is `.local/render-startup-smoke.json`; CI records its own run
rather than treating this local result as a remote workflow result.
