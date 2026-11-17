# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-11-17

### Added

- Initial release.
- Normalized records: `RawCustomer`, `RawTransaction` (with `LineItem`s), `ImportReport`, `Cursor`.
- Connectors: CSV/TSV/Excel files, Google Sheets by link, generic HTTP JSON APIs, Square, and a
  deterministic mock generator; a registry for adding your own.
- `automap`: column mapping guessed from headers and sample values, with confidence and reasons.
- Parsers for money, phone numbers, timestamps and names; SSRF-guarded HTTP client.
- `sync()` helper for incremental pulls with a resumable cursor.
- `tillsync preview` command.
