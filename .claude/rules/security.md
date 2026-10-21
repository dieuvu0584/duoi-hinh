# Security rules

- Secrets belong in environment variables or a secret manager. `.env` must be gitignored. `.env.example` contains placeholders only.
- Never place server credentials in any client code (web or mobile), build assets, remote config or logs.
- All admin routes require server-side authentication. Use secure sessions or short-lived access tokens with refresh/revocation designed explicitly; do not invent a weak custom crypto scheme.
- Hash the owner password with argon2id (established library). Use short-lived access tokens and rotating one-time refresh tokens stored hashed per device; support revocation.
- Pairing/login should be rate-limited and auditable. For the web admin use SameSite cookies or in-memory bearer tokens, CSRF protection and a strict CSP; for a mobile client use platform secure storage.
- Admin access should be private-network-only where feasible. If public exposure is unavoidable, document TLS, firewall, rate limits, patching and monitoring.
- Treat all source text, user-submitted topics and uploaded files as untrusted (prompt injection, SSRF, XSS): follow `docs/threat-model.md` T-1..T-13.
- Redact authorization headers, cookies, tokens, passwords, secret query params and personal data from logs.
- Upload validation: maximum bytes, decoded dimensions, allowed formats, content sniffing, safe filenames, generated storage keys and malware scanning hook where available.
- Apply least privilege to Meta permissions, object storage and database accounts.
- Include backup/restore, token revocation, key rotation and incident response notes before production.
