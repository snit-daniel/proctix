# Cloud deployment for multiple institutions

## Prerequisites

A Linux host or container platform, public HTTPS domain, persistent PostgreSQL, durable shared evidence storage, secret management and a TURN service for restrictive networks. The Docker Compose reference runs multiple institutions on one host with a shared encrypted evidence volume. It does not establish a high-availability, multi-region service.

Generate two independent 32-byte Base64 keys using `python -m proctix.cli init-env` or your secret manager. Keep them private. Add `PROCTIX_DOMAIN`, a unique URL-safe `POSTGRES_PASSWORD` for migrations, and a different URL-safe `PROCTIX_DB_PASSWORD` for runtime access to the root `.env`. Production validation requires HTTPS and PostgreSQL; Compose supplies the correct production URL/driver.

```sh
docker compose --env-file .env -f deploy/compose.yml build
docker compose --env-file .env -f deploy/compose.yml up -d db
docker compose --env-file .env -f deploy/compose.yml --profile operations run --rm migrate
docker compose --env-file .env -f deploy/compose.yml run --rm web python -m proctix.cli create-institution my-school 'My School' admin@example.org
docker compose --env-file .env -f deploy/compose.yml run --rm web python -m proctix.cli enroll-mfa my-school admin@example.org
docker compose --env-file .env -f deploy/compose.yml up -d web worker proxy
```

Enrollment prompts for a password and authenticator confirmation. Administrator accounts are created only by the operator CLI. Proctor accounts created in the dashboard require the same MFA enrollment step before production sign-in. No shared default credentials exist.

The initialization script creates `proctix_app` with data access but no schema privileges. The migration role owns schema updates. On an existing PostgreSQL deployment, provision these roles/grants yourself before starting Proctix; entrypoint scripts only run on an empty Postgres data directory. For managed PostgreSQL use its TLS connection parameters and network controls. Never run tests against institution databases.

Caddy terminates TLS, limits requests, and forwards only within the private container network. Uvicorn trusts forwarded headers only from Caddy's explicit address. If your container network conflicts with `172.29.0.0/24`, change both the network and trusted proxy address. Only 80/443 are published. App containers run without root, writable root filesystems, extra capabilities or public runtime ports. The migration credential is not passed to web/worker environments.

Container images use tagged upstream bases; your release pipeline should build, scan, sign and pin the resulting image digest before deployment. Run schema migration once before workers start. Schema version 2 adds an institution-scoped enrollment table through an explicit additive migration from version 1; run the migration role before updating web/worker processes. Future schema revisions must ship explicit forward migrations. Never point the new service at the old `proctoring.db`.

## Lockdown on Windows and macOS

Use current institution-approved SEB builds and their configuration tools. Finalize the allowed origin, prohibited/permitted applications, virtual-machine policy, display restrictions, quit/admin password and required media permissions. Enable Browser Exam Key headers. Copy **every approved platform/version's key** into the assessment, keeping the `.seb` configuration protected. New configurations/versions can change those keys.

Proctix calculates the request hash using the configured absolute public URL and approved key. A supplied User-Agent is insufficient and is not used as proof. The configured public URL must match the browser's origin exactly. Subpath hosting is not supported in this release. API query-string ordering must be preserved by the proxy.

SEB media support varies by OS/version. Validate the complete camera/microphone/display-capture/WebRTC/IndexedDB/MediaRecorder path in the actual approved builds. If required display capture is unavailable, the server refuses to start; select an approved deployment arrangement that supports capture, or explicitly run an institution-approved browser-proctoring assessment with the relevant policy changed. There is no bundled signed native capture agent in this release.

Sources: [SEB integration](https://www.safeexambrowser.org/developer/seb-integration.html), [Browser Exam Key specification](https://safeexambrowser.org/developer/documents/SEB-Specification-BrowserExamKey.pdf), [FastAPI HTTPS deployment](https://fastapi.tiangolo.com/deployment/https/).

## Phone camera and TURN

Both devices need the same public HTTPS origin. Pairing secrets are in URL fragments, not query strings or access logs; the mobile page removes the fragment after claiming it. The student confirms a phone-generated code on the desktop. The phone does not become connected until it uploads evidence after confirmation. Student submission, staff termination and expiry revoke mobile access.

WebRTC uses database-backed polling for offer/answer/ICE, so web workers need no in-memory session registry. Configure a TURN server with REST/HMAC shared-secret authentication:

```text
PROCTIX_TURN_URL=turns:turn.example.org:5349?transport=tcp
PROCTIX_TURN_SECRET=<your TURN server's shared secret>
```

The service issues one-hour TURN credentials. TURN must allow the needed relay port range and have valid TLS configuration. Without TURN, local/LAN live preview can work, but connectivity across restrictive NAT/firewalls is not guaranteed. Phone snapshots and recording uploads work over HTTPS independently of live preview. Test real iOS Safari and Android lifecycle/background/power behavior. Backgrounding the phone can suspend capture.

## Monitoring, storage and recovery

Monitor public `/health/ready`, HTTP errors, database locks, worker progress, pending evidence growth, disk capacity, recording gaps and key/model availability. The container's local socket health check establishes only process availability; it does not replace external readiness and maintenance monitoring.

Web workers and the maintenance worker must share evidence storage and encryption keys. Use a durable encrypted volume; for multiple hosts, mount a shared filesystem with correct non-root UID/GID permissions. The app-level evidence quota is per session; also enforce tenant storage/billing limits and edge-level rate/concurrency limits before opening public enrollment at scale.

Back up PostgreSQL and ciphertext storage together, and back up encryption/auth keys separately. Restore into a private staging environment and verify an actual recording, saved response, staff MFA enrollment and audit record. Define backup expiry compatible with institution retention. Re-encryption/key rotation is a controlled offline migration, not an environment-variable toggle.

The worker uses locked row transitions for deadlines and policy-driven evidence deletion. Operate it continuously and alert on repeated failures. Expiry on incoming API requests prevents late answers even if worker execution is delayed. Completed answers/evidence become unavailable on retention expiry; preserve only metadata as documented in the institution's separate retention schedule.

## Before real assessments

Complete the documented [release gates](RELEASE.md). In particular: real Windows/macOS SEB acceptance; real iOS/Android mobile/TURN tests; independent security review; representative institution face-model evaluation; accessibility accommodations; production PostgreSQL concurrency/load tests; and backup/restore/retention exercises. This repository's unit and synthetic-browser tests do not substitute for those checks.
