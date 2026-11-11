# Security policy

## Reporting a vulnerability

Please do not open a public issue for security problems.

Report it privately through GitHub: go to the **Security** tab of this repository and choose **Report a vulnerability**. Include what you found, how to reproduce it and what impact you expect.

You can expect a first reply within 7 days. Once a fix is released, you are welcome to be credited in the release notes.

## Supported versions

Only the latest version on the `main` branch is supported.

## What is in place

- Secrets live only in environment variables; `.env` is never committed.
- CI runs secret scanning (gitleaks), dependency auditing (pip-audit) and ruff's security rules.
- Dependabot keeps Python dependencies and GitHub Actions up to date.
- Incoming GitHub webhooks are accepted only with a valid HMAC signature, compared in constant time.
- `main` is protected: changes land only through pull requests with green CI.
