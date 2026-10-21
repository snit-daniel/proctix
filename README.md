# Proctix

A new implementation for institution-isolated exam delivery, interview responses, device-side evidence capture, and documented human review. The original `student2.py` and `admin2.py` are preserved byte-for-byte in `legacy/`; the running application does **not** import or expose them.

This is a tested development preview, not a claim of 100% accuracy, certified security, or completed Windows/macOS deployment validation. An unattended AI cheating verdict is deliberately never produced. See [release criteria](docs/RELEASE.md), [security design](docs/SECURITY.md), and [feature mapping](docs/FEATURES.md).

## Run locally

Python 3.12 or 3.13 is required. The commands below run from this directory.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install --require-hashes -r requirements-dev.lock
.venv/bin/python -m proctix.cli init-env
set -a
source .env
set +a
.venv/bin/python -m proctix.cli migrate
.venv/bin/python -m proctix.cli create-institution my-school 'My School' admin@example.org
.venv/bin/python scripts/fetch_models.py
.venv/bin/uvicorn proctix.app:create_app --factory --host 127.0.0.1 --port 8000 --env-file .env --no-access-log
```

Open `http://localhost:8000`. Institution codes and email addresses are required for sign-in. There are **no default accounts or passwords**. `create-institution` prompts privately for a password. Keep `.env` out of Git. If it already exists, `init-env` refuses to overwrite it.

In a second terminal with the same environment loaded:

```sh
.venv/bin/python -m proctix.worker
```

The worker closes elapsed attempts, cancels abandoned preflight sessions, expires signaling/authentication records, and purges evidence/answers under the session's retention policy. The server independently enforces deadlines on requests even when the worker is down.

For a separate fake-data demo, run `python -m proctix.cli demo` instead of creating an institution. It prompts for a password and creates `admin@example.test` and `student@example.test` in the `demo` institution. Demo seeding is disabled in production.

## Restored student features

Enrollment number/name checks, ID preview and quality feedback, selfie preview/confirm/retake, sampled face overlays and warnings, a live monitoring panel, calibrated sound levels, fullscreen recovery, display inventory, adaptive capture, and per-exam monitoring controls are implemented. See [the complete restoration matrix and explicit boundaries](docs/RESTORATION.md). Identity comparison is advisory; an administrator can select staff-review admission or assigned-account admission independently.

## Assessment workflow

1. An administrator creates accounts and an exam or interview assessment, then assigns students. Extra minutes and attempt limits are configurable per student.
2. The student signs in, opens an assigned assessment, reads the capture/retention notice, and records consent. Declining cancels the attempt without capturing evidence.
3. The student confirms enrollment, uploads a document, previews/confirms a camera selfie, and enables required devices. In staff-review mode they request identity review. In assigned-account mode, authentication/assignment and required uploads/devices authorize entry; face scores do not grant or deny access. A proctor compares evidence with the institution's enrollment record and records a reason for approval or rejection. Face scores are optional review aids.
4. If required, pair a phone using the QR/link. The link expires after three minutes and is single use. Enter the phone's six-digit code on the exam computer, then enable its camera. Only fresh evidence from that confirmed device establishes mobile connectivity. Use HTTPS reachable by both devices; a phone's `localhost` refers to itself.
5. The server starts a fixed deadline after approval and preflight checks. Responses autosave with revision conflict checks. Encrypted IndexedDB recovery preserves pending responses and recording chunks across reloads. Reloading requires the student to re-enable capture. The deadline continues.
6. Submission flushes queued evidence and saves answers before closing the attempt. The server rejects late edits. Staff can review images, recordings, model observations and documented decisions. Only staff can terminate a session for a stated reason.

## Windows/macOS lockdown

Proctix integrates **Safe Exam Browser (SEB)** Browser Exam Key verification. It does not ship a newly signed native browser. Institutions must distribute a final, protected `.seb` configuration, permit only the Proctix origin, configure the supported OS restrictions, enable request hashes, and register the Windows/macOS Browser Exam Keys in the assessment. These keys are encrypted on the server and never returned to students.

Do not assume display capture works in every SEB platform/version. Proctix blocks an assessment that requires screen capture when the browser cannot supply it; it never substitutes the server's screen. Validate actual SEB, camera, microphone, display capture, IndexedDB, WebRTC and recording behavior on every supported Windows/macOS combination before approving it. See [deployment](docs/DEPLOYMENT.md).

## Free local face models

`fetch_models.py` installs **YuNet 2023mar** (MIT, OpenCV Zoo) and **AuraFace v1 ResNet100** (publisher lists Apache-2.0 and commercial use, fal/AuraFace-v1). Downloads use verified SHA-256 digests; AuraFace is pinned to publisher revision `af6d057c9b0ec4071d4c49c80e3539258798b609`. Model cards and license texts are stored alongside the weights. Missing or invalid primary weights report unavailable; no recognizer fallback is selected. The model weights are ignored by Git and downloaded when building the container. No identity image is sent to an external AI API.

These are practical, CPU-capable, freely distributed models—not a claim that one model is universally most accurate. Models return face count, quality measurements, and cosine similarity for review. An institution-specific accuracy study is required before interpreting a score. No photo-based liveness assertion, document-authenticity assertion or automatic identity approval is made. Missing models block creation of biometric-review assessments. Human-only review remains available.

## Validation

```sh
.venv/bin/python -m pytest -q
.venv/bin/ruff check proctix scripts tests
npm ci
npx playwright install chromium
npx playwright test
```

On this macOS 13 host the latest bundled Playwright Chromium is unsupported; tests can use an installed Chrome in an isolated temporary profile:

```sh
PROCTIX_TEST_BROWSER_CHANNEL=chrome npx playwright test
```

Browser tests use generated camera frames, isolated test accounts and `var/e2e/`; they do not access a personal browser profile or real camera. They test WebRTC, but do not certify iOS Safari, Android hardware, SEB or real network/TURN conditions.

PostgreSQL regression tests use `PROCTIX_TEST_DATABASE_URL`. The test harness refuses to drop tables unless the database is named **`proctix_test`**. CI runs the suite against SQLite and PostgreSQL 17. Never point the test harness at an institution's data.

Dependencies are pinned with hashes in `requirements.lock` and `requirements-dev.lock`. `package-lock.json` pins browser tooling. The vulnerability report is in `docs/dependency-audit.json`. To update dependencies, regenerate both locks, audit them and rerun validation.

## Deployment

[Cloud deployment instructions](docs/DEPLOYMENT.md) include PostgreSQL with a separate runtime role, TLS via Caddy, non-root containers, shared encrypted storage, maintenance, staff MFA, TURN configuration, backups and release gates. Deploying to a cloud account, installing SEB on user devices, and validating real devices require your hosting and device environment; no cloud deployment has been performed by this implementation.

Browser tests reset rate buckets only in the explicitly named disposable `var/e2e/browser-test.db` fixture between independent cases. Runtime authentication limits remain unchanged. Run sound/profile/feedback sequence tests with `node --test tests_js/monitoring.test.cjs`.


## Calibrated gaze and document data checks

The camera card offers optional local gaze calibration (9 training targets plus 5 validation targets). Only validated estimates can produce reviewable observations during the exam; no gaze coordinates or eye crops are uploaded. Recalibration is required after camera/window changes. This is not a validated attention or cheating detector.

The identity step checks whether the upload has ID-like features: a clear portrait, readable name and document fields, and passport, residency, student or other photo-ID headings. These are local English OCR heuristics, not a universal authenticity model. MRZ is optional; supported TD1/TD3 zones get consistency and expiry checks. Unclear results explain what to retake. The student interface requests only the portrait side; the optional document-check and back-side controls have been removed. Raw OCR text is not persisted. The primary ID upload requires OCR to find the registered student name before it is saved. Missing, unreadable, or mismatched names are rejected; unavailable OCR requires a retry or organizer assistance. Install `tesseract` locally; the production image includes English OCR. Missing OCR reports unavailable. Appearance results are advisory and do not prove an ID is genuine.

Browser gaze runtime assets are already under `proctix/static/vendor/gaze`; update them only from the pinned npm runtime and run `python scripts/fetch_gaze_model.py` to verify the versioned landmark bundle. Retain its Apache license and third-party notices when packaging.

## Fullscreen recovery and staff history

During an active attempt, resizing the assessment window opens the five-second fullscreen recovery countdown. Repeated resize events and related fullscreen changes stay in one incident; restoring fullscreen updates that incident, and a later interruption starts a new one. Timeout updates the original incident before the existing automatic submission flow runs. Browser resize events caused by entering or restoring fullscreen have a brief settling period.

The staff review workspace shows a newest-first incident history with status filters, name search, older-record pagination, expandable evidence, and timestamped review notes. Routine checks have their own filter. Confirming the same incident repeatedly counts it only once toward the existing five confirmed-violation limit.

## Student assessment reports

Staff dashboards show available assessments and student attempts. Only the inactive "Proctix device and review demonstration" card is hidden from staff. Each report includes student details, saved ID and selfie images, student answers, and named incidents with timestamps and nearby camera/screen images (within 10 seconds, labelled by capture receipt time). Routine checks remain available through the history filter.

Select **Prepare full recording** to combine all available screen-recording streams into one seekable, downloadable MP4. FFmpeg and FFprobe are required on the server (`brew install ffmpeg` on macOS or `apt-get install ffmpeg` on Debian/Ubuntu); the Docker image includes them. Existing audio is preserved. Preparation uses a private temporary directory that is removed after delivery. Missing or expired segments are reported rather than silently omitted. Orange flags and incident buttons seek to approximate incident times; gaps between recording restarts are omitted. No video can be recovered from attempts where recording was disabled or not captured.

The **Your details** setup step accepts arbitrary or blank names and student numbers/emails. Confirm & next saves these values with the attempt without matching them to the signed-in account. Entered details appear in staff review; account ownership remains tied to the authenticated student. The ID-upload check uses the latest nonblank name entered in Your details, falling back to the signed-in name if no name was entered.

ID-upload errors show the exact name being checked and distinguish unreadable text, partial matches, a name not found by OCR, unsupported name scripts, missing OCR, processing errors, and timeouts. A second sparse-text OCR layout pass reduces missed names. An OCR failure does not establish that the ID is incorrect.

The final setup review reports each item separately. Student-entered details are labelled as self-reported (or skipped), ID and selfie report saved/upload status independently, and each device reports its live state and whether it is required. Not-required items use a neutral indicator. Staff identity approval is shown only when the session contains an approved staff decision. The account-based admission mode is not presented as a verified identity.

## Camera warnings and workspace design

Unclear-face image quality remains a camera warning and is excluded from violation counts. Persistent missing-face detections are reviewable incidents; staff may confirm them as violations after reviewing the evidence. Five staff-confirmed violations submit the attempt. The student sees actionable lighting/focus/distance guidance. Gaze/eye-direction estimates are separate observations. Staff can find camera warnings using the history filter; warnings are omitted from violation video flags.

The shared `workspace.css` design system covers student/staff dashboards, attempt search and status filters, consent, details/device setup, exam answers, monitoring, administration forms, account settings, reports and completion views.

Device setup now uses paired camera/microphone and display cards with embedded local previews, per-card permission feedback, and progress based only on required connections. Secondary-camera and advanced settings are grouped below; required secondary-camera settings expand automatically. Consent information and student details use a vertical layout. Final-review statuses and password fields use paired groups on desktop and stack on phones. Navigation preserves each setup page and each camera/display stream.

Control and Command shortcuts are blocked inside active assessment answer fields. Browser-reserved and operating-system shortcuts require a managed exam browser such as SEB. New assessments default to five identity-review requests; photo comparisons remain advisory and admission uses staff review or assigned-account authorization.
