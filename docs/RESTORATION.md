# Legacy feature restoration

The preserved `legacy/student2.py` is unchanged and matches the Downloads original by SHA-256 (`9c5b472671b355b2cfab12369d1c99659492f98aee4d6a254472018d4a2a4b22`). The new service uses scoped, authenticated APIs rather than exposing the legacy routes. This implementation is not a claim of exact behavioral equivalence, validated real-world detection accuracy, or completed production certification.

## Implemented replacements

| Original capability | Working replacement |
|---|---|
| Student ID and name lookup | Institution-scoped student-number records and administrator assignment/update controls. The setup details step accepts arbitrary or blank student-entered values, saves them with the attempt, and does not require a match to the signed-in account. |
| ID selection and confirmation | Automatic acknowledged upload, saved ID preview, decoded-image validation, and model face/lighting/focus/size feedback. The uploaded file is evidence, not an authenticated government document. |
| Selfie capture/retake/confirm | Camera preview, still-image draft, explicit **Use this selfie**, retake, acknowledged upload, and recovery of saved previews after reload. A draft is not described as uploaded. |
| ID/selfie comparison | Optional local, hash-checked YuNet/AuraFace comparison with quality diagnostics and an explicit uncalibrated similarity score. An unambiguous dominant ID portrait can be selected when a smaller secondary face is also present. The selfie still requires one face. No whole-image-similarity fallback is used as identity proof. |
| Admission workflow | Staff-review mode remains available. An administrator can also choose **Assigned institution account** admission: authenticated assignment and required uploads/devices authorize entry, independently of face scores. Advisory comparison is a separate setting and can run automatically after both images are saved. Neither option recreates facial-match-only admission. |
| Face overlays | Camera overlay draws normalized boxes from the most recent uploaded sample. Boxes expire when results are stale. The UI says these are sampled checks, not every video frame. |
| No-face/multiple-face checks | Live, actionable candidate feedback. Unclear/undetected-face events are warnings excluded from violation counts, including historical confirmations; the API prevents confirming these warnings as violations. Multiple-face observations remain separate reviewable events. Repeated camera warnings are rate limited. Face counting runs independently of the admission method. |
| Audio monitoring | Numeric dBFS/meter feedback, baseline calibration, sustained-sound detection and reporting cooldown. Browser-paused analysis is displayed explicitly. No voice, speaker, intent or cheating classification is claimed. |
| Consolidated component status | Face results, sound, recording, synchronized snapshot/chunk counts, upload throughput, pending evidence, capture profile, activity summaries, display information, recent observations and last refresh. |
| Fullscreen and recovery | Direct user-gesture fullscreen request, visible recovery countdown and recovery button. Expired grace periods create reviewable observations, not automatic punishment. |
| Copy/paste restrictions | Optional organizer policies block copy/cut/paste, context menus and print/save shortcuts only inside assessment answers, without reading clipboard contents. Escape, accessibility/IME input and browser/OS controls remain available; browser/OS-wide shortcut blocking is not claimed. |
| Input/typing/mouse tracking | Thirty-second aggregate counts for key, pointer, input, paste, composition, untrusted and focus events. No key values, text, clipboard payloads or pointer trajectories are collected. Summaries are encrypted for retry and idempotent at the server. No remote-control/attention verdict is inferred. |
| Window/display monitoring | Permission-triggered display inventory, `screenschange` updates where supported, honest unavailable/denied status, display-count observations, and window-resize observations. Window geometry is not treated as proof of developer tools or additional screens. |
| Remote-control concerns | Explicit candidate reporting and review workflow; the interface states that browser code cannot reliably scan OS remote-control tools. Native lockdown uses the existing approved Safe Exam Browser request integration. |
| Screen snapshots | Real student-device entire-display capture, configurable cadence (default five seconds), compressed/encrypted images and ordered upload recovery. No cloud-server-desktop screenshots. |
| Recording | Real device-side WebM/MP4 chunks, ordered/idempotent encrypted uploads, playback/download for authorized staff, configurable bitrate/FPS and explicit live state. |
| Bandwidth adaptation | Measured upload throughput and queued bytes choose bounded capture profiles. Profiles adjust snapshot size/cadence, supported track constraints, and recording streams. Changes use hysteresis and preserve separate ordered recording streams. No reserved-bandwidth claim is made. |
| Mobile camera | Reachable HTTPS launch URL in QR, single-use expiring pairing, desktop six-digit confirmation, scoped phone credentials, encrypted uploads, WebRTC signaling/ICE relay and optional TURN. Localhost explicitly reports that a phone cannot reach it. |
| Offline/reconnect | Existing encrypted IndexedDB evidence/answer recovery remains; monitoring summaries also use the encrypted queue. Offline capture is bounded by device/server storage and assessment deadlines. It is not unlimited storage or guaranteed eventual acceptance after expiry. |
| Timer and warnings | Durable server deadline, five-minute/one-minute reminders, expiry enforcement, autosaved answers and worker cleanup. A fullscreen interruption does not reset or pause the deadline. |
| Observation limits and review | Visible observation count and reasoned staff review/escalation. Normal face samples, enrollment checks, activity summaries and time warnings are excluded from the review-threshold count. |
| Exam settings | Face counting/comparison, sound/activity/display monitoring, snapshots/recording, adaptive capture, fullscreen/grace period, copy/paste policy, frame/snapshot cadence, FPS, bitrate, identity-request limit and review threshold. Existing assessments remain immutable; create a new version for a different policy. |
| Administrator evidence and reports | Existing institution-scoped sessions, answers, encrypted evidence, stream playback, observations, dispositions, overall review and signed audit records remain available. |
| Interview assessments | Existing assigned timed interview prompts and recording/review workflow remain. A live interviewer conference room was not a working feature of the legacy student file and is not supplied here. |

## Explicit boundaries

- **No automatic educational admission/denial or penalties based solely on facial matching or uncertain biometric/behavioral observations.** The account-authorized option provides self-service entry without using facial similarity to decide eligibility.
- **No genuine-ID or liveness certification.** The old upload check only decoded an image, found a face and measured brightness. Its single-image liveness helper was not called by the capture path. Issuer-backed document authentication and independently validated replay resistance remain separate work.
- **No gaze-based attention/engagement inference.** The old iris-spacing rule is not reproduced as a focused/looking-away verdict.
- **No random remote-control alerts.** The old 10%-probability demo detector is excluded. Reliable OS process/display/app enforcement requires managed native software; the browser reports its limits.
- **No raw keystroke/clipboard surveillance or automatic five-event termination.** Aggregate activity and reviewable observations preserve monitoring without treating accessibility tools or transient detections as misconduct.

## Operations and validation

Schema version **2** adds only `enrollment_records`; an explicit transactional v1-to-v2 migration preserves existing users, sessions and evidence. Run `python -m proctix.cli migrate` with the migration role before starting updated workers/web processes. Back up the database and encryption keys first. Never point this migration at the old monolithic application's database.

Regression coverage includes PostgreSQL and SQLite backend behavior, tenant/owner isolation, the additive migration, image-preview access, student-entered details, face-sample debounce, summary privacy/idempotence, account-based authorization, synthetic camera/display/mobile flows, recording playback, offline recovery, accessibility, fullscreen recovery, and organizer controls. These tests establish implementation behavior, not face or audio accuracy on a representative population.

Browser display inventory requires permission and is not available in every browser; see [Chrome's Window Management documentation](https://developer.chrome.com/docs/capabilities/web-apis/window-management). Track constraint support is browser dependent; see [applyConstraints](https://developer.mozilla.org/en-US/docs/Web/API/MediaStreamTrack/applyConstraints). Validate the intended Windows/macOS browsers, accommodations, cameras and networks before institutional deployment.


## Local trial

Both local instances have a new **Proctix full-feature self-service demonstration** assignment. It uses assigned-account admission, advisory image checks when model weights are available, unlimited identity-check retries, ten minutes of assessment time, and one-day retention for that new demo. Existing attempts and their admission policies are preserved. The original local review-mode attempt still uses staff-review admission. The isolated phone demo remains separate from the main local database.

Use test images for the temporary public demo. Open the HTTPS demo on the assessment computer as well as the phone; a phone cannot connect to the computer's localhost origin. The public page/readiness was checked without sending the generated demo password through the tunnel. A physical-phone/device validation still needs the user's test.

Latest automated verification: **52 PostgreSQL backend checks**, **51 SQLite backend checks plus one PostgreSQL-only skip**, **13 browser flows**, and **5 sound/profile/feedback sequence checks**. Browser checks use synthetic media and Chrome on the local host; they do not establish representative face/audio accuracy or complete Windows/macOS native validation.

### Submission and observation fixes — 20 October 2026

Final answer submission now validates and saves the final response revision in the same transaction that closes the attempt. It does not depend on the media upload queue. Retries return the saved terminal state without changing answers. Invalid answers and revision conflicts leave the attempt open. Browser requests have a 20-second timeout; failed submissions retain an encrypted local answer snapshot and offer retry.

The evidence queue shares a single upload operation and respects server Retry-After delays rather than repeatedly issuing requests. Pending evidence is not cleared on submission. Authenticated camera, display and recording uploads have a two-minute post-submission drain window, subject to existing quotas and rate limits. These arrivals are explicitly audited as late; their capture time is not independently verified. Answers and identity images cannot change during that window. Evidence remaining after the window stays local and needs organizer support; this is not a guarantee of delivery during prolonged outages.

A sticky assessment notice shows camera warnings and recent observations above the assessment. Command/Control+C, X and V and clipboard actions inside answer fields are observed; blocking remains an explicit organizer policy. Clipboard contents and actual typed keys are never collected. Browser monitoring does not provide an operating-system-wide shortcut lock.

When the detector can assess an identity photo and finds no face, the upload is rejected with instructions to provide the ID front or retake the selfie. Previously saved no-portrait photos also require replacement in account-admission mode, with a staff-approved alternative available. This is a photo-completeness check, not ID authentication or a same-person admission decision. Account-admission mode remains explicitly labelled as identity not verified. Facial similarity is advisory, and model-unavailable states remain visible rather than being labelled a successful check.

### Observation attribution

Every observation is stored under its tenant and assessment attempt, whose foreign keys identify the exam and student. Observation and monitoring responses now explicitly include `exam_id`, `exam_name`, `student_id` (account ID), `student_name`, `student_number` when assigned, and `session_id` (attempt ID), plus a readable event `name`. These values are resolved from server records, never accepted from client event payloads. Review cards show those identifiers and the timestamp individually; session lists show the candidate name and student identifier. No existing records are combined or reassigned. A regression test covers two students across two exams and rejects cross-student monitoring access.

### Monitoring begins with the assessment

Device setup opens local previews and checks browser-reported camera, microphone, entire-display and phone readiness. Automatic camera snapshots, display snapshots, recordings, sound observations and client violation events begin only after the server accepts Begin assessment. Explicit ID uploads and confirmed selfies remain setup actions. The media endpoint rejects monitoring uploads during preflight; the event endpoint rejects preflight events. Mobile readiness uses an authenticated heartbeat and does not require an uploaded frame or pre-exam recording. Readiness is client-reported availability, not trusted proof of device capture.

Candidate observation counts and face-sample results are limited to the selected attempt and timestamps on or after its start. Legacy setup observations remain available in staff history but do not count toward exam flags, and legacy queued setup captures are excluded from the current attempt upload drain. Resuming an active attempt preserves that attempt's exam history; reloading does not erase its observations.

Current-attempt alerts and recent named observations appear above the questions. Detailed capture, network, sound and activity statistics are in a separate collapsible device section. Tab visibility changes and window blur events record a debounced focus-loss observation only during an active exam. These browser signals do not reveal the other application, URL or tab, and are not independently proof of misconduct.

### Five confirmed violations

The fifth **proctor-confirmed** non-informational observation during an active attempt submits the latest server-saved answers. Raw camera, sound, focus and other automated observations never trigger this submission on their own. Confirmation requires an authorized staff account and a specific review reason. Counting is scoped to the attempt and its post-start timestamps; setup history, informational checks, duplicate confirmations and dismissed observations are excluded.

The review transaction locks the parent attempt, counts distinct confirmed observation records and records one `exam.auto_submitted` audit entry with the reviewer, threshold, triggering observation and saved answer revision. The attempt becomes submitted, further answer edits are rejected and phone access is revoked. Candidates see a separate confirmed-violation counter and an explicit submission receipt. An unsynchronized local draft is retained for organizer follow-up rather than represented as submitted. Reviewing or dismissing an observation after submission does not reopen the attempt.

### Truthful setup readiness

Setup readiness now includes the consent, saved images, device requirements, registered-detail confirmation for account-based admission, pending replacements, selfie drafts, photo-completeness feedback and upload errors. The ready heading is withheld until missing checks are resolved. Clearing registered-detail fields immediately disables Begin; account-based start requests also require an existing enrollment confirmation on the server.

Saved uploads from the same attempt are explicitly labelled and previewed, because browsers clear file-picker selections on reload. Session polling replaces cached saved-image flags rather than merging them with a permanent `true`; missing evidence removes its preview and disables Begin. The server checks for both evidence records and actual stored files, and refuses checks or account-based admission when a file is missing. Registration confirmation and account admission are not biometric identity verification.

Manual image-check messages use the actual returned result. An unavailable model, insufficient image quality, a failed request, or a superseded result cannot produce the former generic “Image checks completed” success message.

### Photo warning provenance

Photo-quality labels reset when opening an attempt and show neutral upload/capture instructions until a current result exists. A warning is explicitly labelled as a result for the saved ID image or saved selfie. Session and monitoring payloads include current evidence IDs; historical quality/comparison observations are not used unless their evidence IDs match the currently saved files. Delayed session polls and upload acknowledgements from another attempt cannot overwrite the newly opened attempt. Setup controls become visible only after initialization, preventing an initial reset from overwriting a consent click.

### Display-selection recovery

Display sharing invokes the native picker directly from the click handler and shows distinct picker, preview and connected states. A shared pending-request guard prevents duplicate pickers. Selection has a 20-second deadline and preview startup has an 8-second deadline, with a cancel/retry control and permission guidance. Cancelling the web request cannot dismiss the browser's system picker; the UI tells the candidate to close it if necessary. A stream arriving after cancellation, timeout or navigation is stopped instead of attached to the assessment.

The display video is attached as a visible local preview, avoiding an indefinitely waiting detached video element. Server heartbeats and bounded quality adjustments run separately from selection. Connected status requires a live track and a started preview; stopping sharing restores the original button and clears its preview. Automatic snapshots and recording still wait until Begin assessment.

### Organized setup and photo-state distinctions

Setup uses separate pages for registered details, devices with their local previews and phone controls, identity photos, then final review. A Next action card points to the first incomplete task, with the full remaining-check list available underneath. Navigation buttons scroll to the relevant section. Completed checklist ticks are hidden while requirements remain incomplete.

Saved no-portrait ID results are labelled as a replacement task rather than a successful upload. A saved selfie displays its saved state without a confirmation button. Retaking creates a new unsaved preview: only then does Use this selfie appear, and saved selfie quality/comparison text is hidden until the draft is confirmed. Existing evidence remains stored under its original attempt; resuming an attempt does not silently discard or replace it.


### Paged setup

Setup now displays one page at a time: consent, manually entered registered details, devices, identity photo uploads, and final review. Next/Back and the step navigation select the actual visible page. Photo review has a separate Next control so an already-submitted review can be revisited without submitting it again. Student name and reference fields start blank when opening setup; saved photos are scoped to their original attempt and remain available when resuming that attempt. A new attempt requires its own uploads.

The desktop sidebar is 260px wide and institution/navigation labels wrap within it. Display selection expires after 20 seconds when the browser does not return a stream, with explicit Chrome/Edge and macOS recording-permission guidance. A native picker or operating-system permission cannot be granted by page JavaScript.


### Guided advisory photo retries

Account-admission setup provides targeted ID/selfie retake instructions, explicit model-unavailability guidance, and up to five guided comparison checks based on the attempt's persisted verification count. After five checks the browser stops automatic reruns and disables the advisory rerun button; this is not an admission decision or a server security limit. Replacement uploads remain possible. Photo guidance is scoped to current evidence, hidden while a new selfie is unconfirmed, and cleared on replacement. Final review says account access is ready while identity remains unverified. No similarity threshold grants or denies exam access.


### Submission cleanup and visible observations

Stopping monitoring and rendering any terminal attempt immediately clears the time-warning and fullscreen-recovery banners. The exam observation list is placed in the session aside with the camera, rather than above the long assessment page. Face count and current face guidance are visible outside collapsed diagnostics. Up to ten named current-attempt observations are shown, with a scrollable list.

A first sampled multi-face result now creates a reviewable observation (30-second deduplication); no-face and image-quality warnings still require consecutive samples. Detection remains sampled and may miss appearances between captures. No model observation alone establishes misconduct or submits an attempt.

Legacy student2.py gaze analysis compares the horizontal distance between iris landmarks against face width. This measures eye separation, not gaze direction, so it has not been restored as reliable eye tracking. YuNet/AuraFace model files load with their verified hashes, but that does not establish real-world accuracy or document authenticity. Current photo processing locates and compares portraits; it cannot certify that an uploaded document is genuine.


### Live face consistency

When face detection and face comparison are enabled, a clear single-face camera sample is compared with the last selfie saved before Begin, at most once per 15 seconds. The pre-exam reference cannot be replaced during the attempt. Identity comparison uses five-landmark ArcFace-compatible alignment from YuNet landmarks and hash-pinned AuraFace v1 ResNet100 weights, requiring a single face in both images; embeddings are not persisted and no extra camera frames are requested.

The provisional AuraFace cosine review threshold (0.4) is not institution-calibrated, is not a probability, and is not inherited from SFace. It must be evaluated on a consented representative dataset before making any accuracy claim. Two successive low-score checks within 60 seconds create a named `possible_person_change` observation with both evidence references and the baseline ID, deduplicated for 60 seconds. Unclear/multiple-face images are excluded from this comparison; multiple-face handling remains independent. Inference failures remain advisory and do not discard camera evidence. Raw checks are informational and do not count as violations. Possible-change observations require human confirmation before counting toward any existing confirmed-violation workflow.

This feature cannot prove identity, detect a change before the initial selfie, establish liveness, or reliably identify substitutions occurring entirely between samples. Real-camera and population accuracy has not been benchmarked. Model source: https://huggingface.co/fal/AuraFace-v1


### Free primary recognition model

AuraFace v1 ResNet100 is the primary recognition model. The publisher's model card explicitly lists Apache-2.0 and commercial use. `scripts/fetch_auraface.py` downloads only the recognition weight, pins its publisher revision and SHA-256, and preserves the model card and Apache license text. The AuraFace repository's additional detector, demographic and landmark weights are not installed. YuNet remains the separately licensed primary face detector. Existing SFace files may remain on disk but are not selected, and no silent fallback is used. Missing, corrupt or unloadable primary weights make the vision engine unavailable.

Alignment uses a five-point similarity transform into the ArcFace 112px reference geometry. RGB inputs are normalized by 127.5; 512-dimensional outputs are normalized before cosine comparison. Startup runs actual inference on the model graph. Raw embeddings are not stored. Alert streaks cannot span different recognition models. `docs/model-sanity.json` records checks on public research-author example images; these are not a dataset-level or demographic accuracy evaluation. One small public example is correctly rejected by the production minimum-face-size guard; its recognition-core result is diagnostic only.

Eye gaze and document authenticity are not implemented by AuraFace. No research-only gaze or detector checkpoints are added, and model similarity does not independently grant or deny educational access.


### Optional calibrated gaze estimates

`gaze.js` uses locally hosted MediaPipe Tasks Vision 0.10.32 and the hash-pinned FaceLandmarker v1 bundle to obtain iris/face landmarks. A student explicitly chooses calibration. Nine screen targets supply local training samples; five separate targets validate the fitted ridge-regression mapping. Estimates remain off when validation fails (median error over 12% or maximum error over 20% of viewport diagonal). Calibration is per attempt and is not persisted. Changing viewport, camera track, or substantial camera distance/head position invalidates the mapping. Hidden/unfocused pages reset the observation timer. No inference of attention, engagement, intent or misconduct is made.

During an active exam only, a calibrated estimate remaining beyond a 15% margin outside the screen for five seconds can create a client-reported `gaze_outside_calibrated_area` observation, deduplicated for 30 seconds. Eye crops, gaze coordinates, raw landmarks and model coefficients are never uploaded; only the event duration and normalized validation error are sent. Events require human review and cannot automatically submit an exam. Poor/absent/multiple-face frames do not produce gaze observations. A real-user screen-point accuracy evaluation is still required; synthetic regression tests and landmark sanity checks are not an accuracy certification.

MediaPipe assets are served from `/static/vendor/gaze` with Apache notices, without CDN calls. CSP permits WebAssembly compilation using `wasm-unsafe-eval` while retaining the script-source restriction. The npm runtime is exact-version pinned and model download is SHA-256 checked. This implementation does not distribute WebGazer/RealEye code or research-only Gaze360 weights.

### Flexible ID appearance checks

ID images remain flexible across issuing countries and types. The student interface now contains only front-side ID and selfie cards. Historical supporting-side evidence remains scoped to its attempt under `id_back` and cannot change after Begin. Local Tesseract English OCR reads the image; the application checks supported TD1/TD3 MRZ check digits and expiry and searches for registered-name tokens. Unsupported formats and poor OCR produce limited/unavailable results rather than a forgery verdict. Raw OCR output, document numbers and parsed names are not persisted. Replacing the main ID invalidates older document-result metadata.

The requested scope is appearance plausibility, not issuing-authority authentication. English OCR heading/field heuristics recognize passport, residency, student, national identity and driving-license cues. A positive `id_features_found` result requires readable text, a supported document heading, name and document-number fields plus another field (or a checksum-consistent MRZ), a clear unambiguous portrait, and the registered name when available. An ordinary portrait with a name caption, a heading alone, or a letter does not satisfy those requirements. MRZ is optional; student cards need not have one. Unclear fields, absent/poor portraits, OCR name differences or inconsistent MRZ return retry guidance. Missing model/language support stays inconclusive. A supporting back-side image cannot confirm the portrait side.

This is a bounded heuristic, not a validated universal ID classifier. All results explicitly retain `authenticity=not_verified` and `issuer_verified=false`; even a consistent MRZ and plausible layout can be copied. No issuing-authority source is configured. Appearance results remain advisory, are attached to the uploaded evidence and attempt, and do not independently authorize or deny educational access. Unit tests cover the supported document cues, portrait failures, caption/letter false positives, unreadable text, supporting-side scope and non-persistence of raw personal data.

### Candidate setup copy and notification cleanup

Setup now uses short next-action messages and “Setup complete” after the required setup checks. This label describes setup completion, not a biometric identity verdict. Model scores, document diagnostics and assessment rules remain accessible under expandable details. Successful photo-quality messages and the retry panel after a completed comparison no longer repeat across the page. Unresolved capture-quality issues remain next to their fields and update from current evidence.

Success notifications expire after 4.5 seconds and errors after 8 seconds. Successfully retrying the same failed action clears its old notification immediately without dismissing a newer message. Five guided checks remain in place; this UI change does not introduce automatic biometric admission or denial. Browser tests cover corrected-error clearing, expiration, the setup wizard, and screen-picker recovery.

### Quiet-sound monitoring and visible microphone status

The previous fixed RMS floor of 0.02 missed quiet inputs. Sound detection now uses a five-second background calibration (low percentile to resist brief louder contamination), a threshold of max(0.0015 RMS, 2.5 × background), and 1.5 seconds of sustained above-threshold sound. It re-arms after quiet and rate-limits observations to one per 15 seconds. Quiet input is no longer absorbed into the background estimate unless it is near the existing noise floor. These are unvalidated engineering settings, not measured whisper/speech accuracy.

The microphone meter and status are visible beside the camera. The meter uses a dB display scale so quiet input remains visible. A suspended AudioContext shows a Resume button that runs within the click gesture; missing/muted tracks show actionable status. Reconnection stops sampling the old analyser. Sustained sound produces a six-second candidate notice and an attempt-scoped `audio_level_change` observation. No automatic sound observations occur before Begin or after submission. The detector does not transcribe speech, distinguish speakers, prove assistance, or establish that a whisper contains answers. Microphone distance, hardware processing and room noise still limit detection.

An end-to-end generated-audio test also exposed fractional `performance.now()` durations being rejected by the integer-only event schema. The detector now rounds duration milliseconds before upload; a regression test covers fractional timestamps. Browser verification exercises real Web Audio analysis with a quiet synthetic tone, suspension/resume, successful server receipt, the named student observation, temporary notice clearing and submission cleanup. Synthetic tones test signal plumbing, not whisper recognition accuracy.

### One assessment flow

The separate fictional simulator has been removed at the user's request. Its former URL `/static/decision-demo.html` forwards to the main workspace. Proctix has one candidate flow: notice, registered details, devices, identity photos and comparison, final setup, assessment, and receipt. The main flow retains five guided image checks and automatic submission after five staff-confirmed violations. It does not admit or exclude students solely on biometric scores or count unreviewed model flags as confirmed violations.

### Resolution-aware comparison eligibility and compact camera panel

The original fixed 80-pixel portrait and raw Laplacian cutoff rejected some otherwise comparable photos before AuraFace ran. The assessor now also measures sharpness on a normalized 112×112 face crop. Ideal capture recommendations remain separate from inference eligibility: comparison requires a selected portrait, at least 48 source face pixels, brightness within 25–235, and normalized sharpness at least 20. Missing/ambiguous faces, severe blur, extreme lighting and very small portraits remain unassessable. Non-ideal usable photos return a comparison with `quality_warning=true`, rather than an artificial mismatch or unconditional quality rejection. These engineering cutoffs require representative evaluation; they do not establish higher population-level accuracy. The primary YuNet/AuraFace models and provisional similarity threshold are unchanged.

The camera panel now keeps the preview, brief face status and actionable warnings visible. Microphone diagnostics, gaze calibration, continuity details and explanatory copy are collapsed under Monitoring settings. The resume-audio action stays visible when needed. Quality recommendations no longer use error styling when inference remains possible. Regression coverage includes moderate printed portraits, resolution-normalized sharpness, severe blur, the complete setup, optional gaze calibration and quiet-audio monitoring.

### Five-second fullscreen recovery and automatic submission

During an active assessment requiring fullscreen, an exit immediately opens a five-second countdown. A successful return cancels the countdown and a later exit starts a fresh five-second window. At expiry, the browser submits current answers through the same atomic answer-plus-submission endpoint used by manual Submit, without a confirmation prompt. The page freezes answer inputs while submission is pending, retains its encrypted local answer backup, and retries failures every three seconds. Restoring fullscreen after expiry does not undo pending submission. This deterministic browser-state rule is independent of biometric or model observations and is disclosed before the assessment.

Submission uses the owner-scoped endpoint with `reason=fullscreen_timeout`; the server records a client-reported `exam.fullscreen_submitted` audit entry and returns that reason on repeat requests and later receipt refreshes. Terminal attempts remain idempotent. No fullscreen observation or timeout submission occurs during preflight or when fullscreen is not required. Existing longer legacy grace values are capped at five seconds by the recovery controller; new policy defaults and the organizer form use five seconds. The backend cannot independently inspect OS/browser fullscreen, and a suspended/closed browser or offline connection cannot guarantee server receipt at the exact five-second boundary.

Tests cover a four-second exit followed by recovery, cancellation of the original timer, a subsequent exit reaching the deadline, a temporary submission failure followed by retry, final-answer preservation, submission-reason persistence and idempotence. The browser test uses synthetic fullscreen transitions and a disposable assessment; native fullscreen remains browser-dependent.

### Clear identity outcome and visible calibrated-gaze state

The identity step now has a single prominent comparison outcome above the photos: Similar facial features, Low facial similarity, Could not compare, Comparison unavailable, or No comparison result yet. These are advisory results, not access verdicts. Saved-image notices, repeat instructions and non-actionable quality recommendations are suppressed once photos are saved; optional document-check/back-side controls have been removed from the student interface, and model metrics stay collapsed. Front-side checks use `document_checks` and supporting-side checks use a separate informational `document_back_checks` kind. Legacy front-result lookup excludes supporting-side records, so uploading the back no longer overwrites the front assessment. Both remain tied to the current primary evidence ID.

A compact gaze status and Set up gaze check shortcut remain visible without expanding diagnostics. Status distinguishes calibration needed, ready, eyes unavailable, paused window, inside screen and outside screen. The local validated estimator records an advisory event only after five sustained seconds outside the calibrated area during an active, focused exam. A controller-level regression test verifies this path, preflight exclusion, cooldown and streak reset on unclear eyes. This is physical gaze estimation; attention, intent and misconduct are not inferred. Real-user accuracy remains unvalidated.

### Automatic direction estimates without calibration

The default exam path now starts local MediaPipe landmark inference automatically after Begin when a live camera is present and face detection is enabled. It does not run automatic monitoring during preflight. Conservative iris-position agreement and nose-to-eye geometry cues provide an uncalibrated physical eye/head-direction estimate. Five sustained seconds of a looking-away estimate can emit `looking_away_estimate`, deduplicated for 30 seconds, with duration only. Blinks, absent/multiple faces, stale/unavailable frames and inactive windows reset the streak. Nothing is uploaded except the event; no attention, intent, speaker identity or exact screen-boundary inference is made.

Optional validated screen calibration is retained as an advanced mode; it is no longer required for the automatic approximate direction check. UI/notice text distinguishes the two modes. The default heuristic is not a pretrained gaze estimator and has not been evaluated on representative people/devices; anatomy, off-center cameras and ocular conditions can cause missed or incorrect estimates. Observations remain reviewable and do not automatically affect exam access or the confirmed-violation submission count. Unit tests cover straight/turned iris geometry, blinks/multiple faces, active-only persistence, cooldown and reset. Browser testing verifies automatic model startup without calibration and the existing optional-calibration and sound paths.

### Face result on the final setup page

The final setup page now includes an ID photo vs selfie result card, rather than showing only technical setup readiness. It reuses the current evidence-scoped comparison status and shows Faces appear to match, Faces appear different, unassessable/unavailable, or pending. Estimated outcomes are distinct from account authorization, with a brief quality/uncertainty note and a link to replace photos. Monitoring refreshes update this card without a page reload. Browser verification covers unavailable, synthetic above-threshold and below-threshold outcomes on the final page.

### Start again creates a fresh attempt row

Opening an assessment from the available-assessments list now requests `fresh_start=true`. A Start again with new photos action is also available during setup. It cancels only the candidate's unfinished preflight rows for that assessment, revokes their mobile pairings, and creates a new session with no consent, enrollment confirmation, uploads, comparisons, observations or answers and zero verification checks. Prior rows and evidence remain in history with an audited preflight restart. Replaced/unstarted cancelled setups do not consume the completed-exam attempt allowance for an explicit fresh start. The legacy API behavior without `fresh_start` still resumes existing work and retains its previous attempt-limit behavior.

An in-progress exam is always resumed with its existing ID and deadline; starting fresh cannot bypass its timer, submission, or completed-attempt limits. Dashboard launch buttons reject duplicate clicks while requests run. Backend and browser tests cover fresh IDs, cleared photos/checks/consent, preserved evidence/history, repeated setup restarts, active-exam resumption and exhausted completed-attempt limits.

### Photo retry guidance without automatic exclusion

A low-similarity or unassessable result from a requested photo check stays on the photo step rather than automatically advancing. A low result after the first check displays four checks remaining, with ID replacement and selfie-retake actions. A similar result can advance to final setup. The five-check limit remains per attempt. The existing non-biometric account route remains explicitly available as Use account verification instead; the final page retains the comparison result rather than implying a verified match. No face score alone grants or denies exam access. Browser tests use synthetic scores to verify retry navigation/counts, the account alternative, and advancement after a similar result.

### Prominent fullscreen recovery and terminal receipt

Fullscreen recovery now uses a full-viewport dark backdrop, centered dialog, 80–128px countdown and a full-width return button at least 64px tall. Focus moves to the return button, Tab stays in the warning, and successful recovery restores prior focus. The existing five-second deadline, cancellation and submission retry behavior is unchanged.

Terminal attempts render a completion receipt instead of the active workspace. The time card, editable question form, submission controls, camera/mobile/monitoring sidebar and setup guidance are hidden; timer rendering stops at Ended. Saved answers remain accessible in a collapsed read-only receipt section with the attempt reference and completion time. The receipt distinguishes manual submission, fullscreen timeout and confirmed-violation submission. Browser tests cover manual and automatic completion cleanup, saved-answer visibility and large/keyboard-accessible fullscreen controls.
