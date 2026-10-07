Synchronous private-data export and erasure admit at most 200 conversations,
20,000 relevant rows and 32 MiB of encrypted payload bytes per operation.
The owner is checked first. SQL counts and byte lengths run before private
content is decrypted or any destructive change starts. An exceeded boundary
returns 413 with `PRIVATE_ROW_LIMIT_EXCEEDED` or
`PRIVATE_PAYLOAD_LIMIT_EXCEEDED`; it does not partially erase content.

The byte check includes encrypted columns from scoped auxiliary records,
including provider originals. This is a conservative admission size, rather
than the size of the returned export. PostgreSQL uses `octet_length` and SQLite
measures a BLOB so UTF-8 characters cannot be mistaken for bytes.

Fernet ciphertext includes authentication, padding and Base64 encoding, so it
is larger than its plaintext. JSON escaping, source-reference metadata and
Python objects add overhead after decryption. The 32 MiB gate bounds encrypted
payload admission; it does not claim a 32 MiB response or resident-memory ceiling.
The row and conversation gates also apply. Large exports and erasures require
a durable batching workflow, which remains unimplemented.

Owner list endpoints retain their array response and use a 100-row default,
a 200-row maximum and stable `(created_at, id)` ordering. Offset is bounded to
10,000. UI bootstrap uses its separate bounded conversation cursor.
