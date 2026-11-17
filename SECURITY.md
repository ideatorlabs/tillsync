# Security policy

## Supported versions

| Version | Supported |
| ------- | --------- |
| 0.1.x   | Yes       |

## Reporting a vulnerability

Please email **ideatorlabs.ai@gmail.com** with a description, steps to reproduce and the affected
version. Do not open a public issue for security problems. We aim to acknowledge reports within
three working days and to ship a fix or mitigation as soon as we can.

Areas we especially care about: the SSRF guard (`tillsync.netguard`), handling of credentials
in connector configs, and parsing of untrusted files.
