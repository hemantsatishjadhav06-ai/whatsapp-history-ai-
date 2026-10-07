# Locked dependency provenance

Recorded from the installed npm package metadata and `package-lock.json` on
7 October 2026. The production image installs the frozen lock with lifecycle
scripts disabled, retains each dependency's packaged notices, and includes the
unchanged license texts in `LICENSES/`.

| Package | Locked version | Declared license | Source and packaged notice |
| --- | --- | --- | --- |
| `@whiskeysockets/baileys` | `7.0.0-rc14` | MIT | [WhiskeySockets/Baileys](https://github.com/WhiskeySockets/Baileys); [MIT notice](LICENSES/baileys-MIT.txt), copyright 2025 Rajeh Taher/WhiskeySockets |
| `libsignal` | `6.0.0` | GPL-3.0 | [WhiskeySockets/libsignal-node](https://github.com/WhiskeySockets/libsignal-node); [packaged GPL v3 text](LICENSES/libsignal-GPL-3.0.txt) |
| `whatsapp-rust-bridge` | `0.5.4` | MIT | [jlucaso1/whatsapp-rust-bridge](https://github.com/jlucaso1/whatsapp-rust-bridge); npm package metadata declares MIT but the root package ships no LICENSE file |
| `pg` | `8.23.1` | MIT | [brianc/node-postgres](https://github.com/brianc/node-postgres/tree/master/packages/pg); [MIT notice](LICENSES/pg-MIT.txt), copyright 2010–2021 Brian Carlson |

The lock records these verified npm tarball integrities:

```text
@whiskeysockets/baileys@7.0.0-rc14
sha512-WK+X8ju8TPGxvWIsP8hrY6JB6FltYuFe+vsqKfjOYX25JObij9qLf2c3ZGdl1Q+vhFwbnT+AZmWAB5pTvzmSiQ==
libsignal@6.0.0
sha512-d/5V3YFtDljbFMufz4ncyUYGYhJl+vzAe+c2EFFBQ6bz1h8Q3IOMEGXYMzlibU60I+e8GagMMpji18iez3P1hA==
whatsapp-rust-bridge@0.5.4
sha512-yYO1qSs0Fe7tGtnxOFHomocUD6IZtoAgmA4oDFyGIRZ67D3QZk3w7swA6XXFXNQngiyrg2k7tul6IrM3eUFh7A==
pg@8.23.1
sha512-aL96AHANtWjPLDOLqnhx+ngp9+UK7ETEU8VJrDCGvsSSi/mGLcWYsS6Herg7lmaBJe4uwrfqsa7gTEFaSizDoQ==
```

Baileys is an unofficial SDK and its pinned release is a release candidate. The
MIT declaration of the SDK does not replace the GPL-3.0 declaration of its
libsignal dependency. This record provides provenance and preserves notices; it
does not resolve distribution obligations or select licensing terms. Release
review should include the complete production dependency tree and platform-native
Rust packages. A production-only npm audit of this lock on 7 October 2026 reported
zero advisories; this is a point-in-time registry report.
