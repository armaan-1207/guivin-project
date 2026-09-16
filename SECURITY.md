# Security Policy

## Reporting a Vulnerability

This project is a hackathon demo system.
If you discover a security issue, please open a GitHub Issue or contact the maintainer directly.

## Sensitive Data

**Never commit:**
- `.env` files containing API keys or passwords
- `backend/guivin.db` (may contain camera metadata and alert history)
- `backend/evidence/` (may contain footage frames)
- Any real Sentinel login credentials

All of the above are covered by `.gitignore`.
