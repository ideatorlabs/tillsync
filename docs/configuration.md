# Configuration

| Option | Where | Meaning |
| --- | --- | --- |
| `TILLSYNC_DEFAULT_COUNTRY` | environment | Calling code for bare national phone numbers when a connector has no `default_country` (default `1`). |
| `timezone` | CSV, Sheets, HTTP | IANA zone for times without an offset (default UTC). |
| `default_country` | CSV, Sheets, HTTP, Square | Calling code such as `"91"` or `"44"` for bare numbers. |
| `date_formats`, `dayfirst` | CSV, Sheets, HTTP | Extra `strptime` patterns; whether `03/04/2026` is 3 April (automap detects this from the data when it can). |
| `currency` | CSV, Sheets, HTTP | Default currency code when neither the cell nor the header names one. |
| `mapping` | CSV, Sheets | Dict or `AutoMapping`; guessed when omitted. |
| `allow_private` | Sheets, HTTP | Allow loopback and private-network URLs (link-local stays blocked). |
| `auth` | HTTP | `bearer`, `basic`, `header` or `query`; secrets as `token_env`/`password_env`/`value_env` to read environment variables. |
| `pagination` | HTTP | `none`, `page`, `offset`, `cursor`, `next_url`. |
| `since_param`, `since_format` | HTTP | Query parameter for incremental pulls and its format (`iso`, `iso_z`, `epoch`, `epoch_ms`, or a strftime pattern). |
| `amount_divisor` | HTTP | Divide amounts, e.g. `100` for APIs that send cents. |
| `max_pages`, `timeout` | HTTP | Safety limits (default 10,000 pages, 30 s). |
| `base_url`, `location_ids` | Square | API host (production by default; `SANDBOX_URL` for the sandbox) and locations to read. |
