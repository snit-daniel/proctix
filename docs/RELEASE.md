# Release evidence and outstanding acceptance

Implementation date: October 20, 2026. See [restored features and limitations](RESTORATION.md). Status: development preview with restored legacy capabilities; production validation remains required. The original files remain unchanged in Downloads and are preserved in `legacy/`. This is a new application/schema, not an in-place update of an unknown old database.

## Recorded results

- SQLite: 38 passed; the PostgreSQL-specific concurrency test is skipped.
- Real PostgreSQL: 39 passed, including concurrent attempt allocation and conflicting answer writes.
- Browser: 5 passed using isolated Chrome with synthetic device streams.
- Runtime dependency audit: zero known vulnerabilities reported across the active runtime dependencies.
- Python lint, JavaScript syntax and frontend formatting checks pass.
- Original-file checksums match the preserved copies.

See `validation-summary.json` and `dependency-audit.json`. The test client emits an upstream deprecation warning for its httpx compatibility adapter; this does not affect the deployed API.

## Automated checks

The local regression suite covers authentication/authorization, institution isolation, role separation, CSRF/Origin/Host checks, MFA replay, persistent rate limits, revocation, encryption tampering, SEB request hashes, review-gated identity, evidence replacement, attempts, durable answers, idempotency/revision conflicts, server expiry, capture prerequisites, lack of automatic cheating verdicts, evidence validation, recording order/reassembly, retention, audit tampering, mobile pairing/scope/signaling and model-unavailability behavior.

The same suite runs against real PostgreSQL in addition to development SQLite. PostgreSQL-specific tests race attempt allocation and answer revisions. Browser tests exercise an actual browser with synthetic camera/display streams: identity review, response autosave, network interruption, reload recovery, capture recovery, final submission, mobile confirmation, live WebRTC video, revocation, encrypted queue persistence and playable recording chunks. Synthetic streams are clearly not a substitute for physical device testing.

The production dependencies are hash-pinned, scanned and the report is saved in `dependency-audit.json`. Model files are SHA-256 checked and licensed. CI reproduces checks against Linux PostgreSQL 17 and Chromium. The CI definition is included but has not been run on a remote CI provider from this workspace.

## Required before using real exams/interviews

- Confirm the real approved Windows and macOS SEB builds, protected configuration, key headers and every required browser API. If capture is unsupported, an approved native capture integration is still needed. This release does not bundle that agent.
- Test physical iOS/Android devices, cellular/Wi-Fi transitions, NAT/TURN, camera switching, permission revocation, screen lock, background suspension and low battery. Ensure honest disconnected states.
- Have an independent reviewer assess tenant boundaries, account recovery/MFA administration, secret handling, upload parsers and native-client trust assumptions. Automated tests do not certify the absence of vulnerabilities.
- Benchmark target concurrency, request latency, database contention, disk throughput, recording continuity and resource exhaustion on the intended cloud hardware. Apply capacity limits and alerting. No universal capacity figure is claimed.
- Run a representative consented face evaluation, document unacceptable quality/failure conditions and accessibility alternatives, and train staff reviewers. No empirical local population accuracy has been measured yet. No model is claimed to be 100% accurate.
- Define institution notices, enrollment-record review, accommodations, data retention, review/appeal handling and backup expiry; test those operational procedures. No legal compliance certification is claimed.
- Configure production TLS, least-privilege database roles, secrets management, trusted proxies, durable shared storage, backups, worker monitoring and alert routing. Perform a restore drill with the exact release image and secrets.

## Face-model evaluation

Supply a CSV of independently labeled, consented holdout pairs, relative paths and optional anonymous cohort labels:

```text
reference,candidate,same_person,group
images/enrolled-001.jpg,images/session-001.jpg,1,cohort-a
images/enrolled-001.jpg,images/session-002.jpg,0,cohort-a
```

Run `python scripts/evaluate_faces.py pairs.csv --threshold <candidate-cosine-threshold> --output report.json`. The tool reports false-match/nonmatch rates, image-quality rejections and 95% Wilson intervals overall and by cohort. It does not enable a threshold or approve deployment. With too few independent cases, low observed error is not evidence of low real-world error. Liveness, document authenticity, replay/deepfake resistance, camera spoofing and adversarial robustness require separate evaluation.
