# Security

ReqDock is a local, single-user Windows tool. Keep its loopback service private; it is not a multi-user or internet-facing server. A fresh workspace requires local pairing. Explicit local-only mode still checks loopback peers, Host and browser Origin.

Credentials belong in Windows Credential Manager, never in this repository. The ignored `workspace/` contains private environment settings, requests, responses, connection profiles and media. Do not attach that directory or raw logs to public issues. Review exports before sharing, since arbitrary business text may contain sensitive information that field-based redaction cannot recognize.

SQL write switches supplement database permissions; they do not make privileged accounts read-only. Use the least privileges needed and inspect every confirmation. Outbound requests can reach configured private services; do not accept unreviewed environment configurations from others.

For suspected vulnerabilities, use GitHub private vulnerability reporting if enabled. Do not include working credentials, private hostnames or personal data in public issues.
