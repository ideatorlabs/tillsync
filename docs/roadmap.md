# Limits & roadmap

## Limitations and non-goals

- No storage, scheduling or deduplication across runs: that is your callback's job (upsert by
  `external_id`). Delivery is at-least-once.
- Customer-summary exports (one row per customer with lifetime totals instead of one row per bill)
  are not interpreted; map them yourself.
- Phone handling is deliberately light (no per-country numbering plans). Use `phonenumbers` if you
  need strict validation.
- `automap` is a heuristic. Review `explain()` before trusting it with a new export layout.
- Google Sheets must be readable by link; there is no OAuth. Legacy `.xls` files are not read.
- The SSRF guard checks resolved addresses but cannot stop DNS rebinding between the check and the
  connection.
- Square: customers and orders only (no Payments API); refunds are read from the order.
- No ready-made presets for specific POS products: describe their API with an HTTP config.

## Roadmap

- Customer-summary file support.
- Optional `phonenumbers` integration.
- Webhook receiver helpers (signature checks) on top of `map_transaction`.
- More connectors contributed as HTTP configs.
