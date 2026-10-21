# SHADOW-Q ECLIPSE PRO — Research OS

A cinematic local research environment featuring a real account system and the Qiskit-based experiment API.

## Run on Windows

Use your already-installed Qiskit environment to avoid repeated package downloads:

```powershell
& "C:\Users\User\Desktop\SHADOW_Q_Research_OS_Final\SHADOW_Q_Qiskit_FIXED\.venv\Scripts\python.exe" server.py --port 8771
```

Or double-click `START_WINDOWS.bat` (may need internet for first-time dependencies). Open http://localhost:8771. Select CREATE ACCOUNT on the first visit. Use your own password (minimum 10 characters); no hardcoded credentials are provided.

## Authentication model

Local SQLite accounts, salted PBKDF2-SHA256 password hashes (310,000 iterations), opaque random HttpOnly SameSite cookies, 12-hour in-memory sessions, login throttling, user-scoped experiment history. These protections are suitable for a localhost demonstration; **not audited or hardened for internet-facing deployment**. No email verification, password reset, MFA, TLS termination, or external identity provider is included. Sessions reset on server restart.

## Scientific integrity

The circuit theater is a conceptual visualization; the experiment API runs Qiskit. Exact mode checks mathematical consistency and does not establish measurement savings. Full-width classical shadows are not width-constrained. Adaptive superiority requires repeated matched-budget experiments.

## Verify

```powershell
python -m py_compile server.py shadowq\engine.py shadowq\adaptive.py shadowq\shadows.py
```

The app requires qiskit, qiskit-aer, numpy. For production deployment, use a proven identity provider and security review.
