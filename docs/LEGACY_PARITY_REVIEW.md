# student2.py parity review

**Historical audit before restoration.** See [the current implementation matrix](RESTORATION.md) for features restored since this comparison.

Reviewed the preserved `legacy/student2.py` (8,878 lines), including its Python endpoints and embedded browser application, against the current FastAPI service and frontend. This is source inspection, not a claim that every legacy path successfully ran. The legacy service was not executed and no live identity decision was made.

## Main finding

The replacement changed the product workflow and is not feature equivalent. The legacy student flow was: student ID/name lookup → ID upload with a face-presence check → selfie capture → server comparison → automatic completed status after a passing result → transition into the exam and monitoring. The replacement instead captures evidence and requires a staff review before start. Calling the replacement complete or production ready overstated what was delivered.

The old automatic transition is implemented at `capture_face` (line 3925, success branch near 4000), the browser `confirmFacePhoto` (6278), and `start_exam` (4130). The current start endpoint expressly requires staff approval. This was a deliberate behavior change, not merely a different face library.

## Feature differences

| Capability | What the legacy source contains | Current implementation and remaining difference |
|---|---|---|
| Student enrollment lookup | Student ID and exact name check against a student table, 3684–3867 | Institution account login and exam assignment; separate student-number/name entry flow is absent. |
| ID upload feedback | Decodes image, detects a face and rejects a dark image, 3870 | File decoding, size controls, encrypted storage and upload acknowledgement; the upload screen does not immediately show model face/quality findings. An uploaded image is not thereby authenticated as a genuine ID. |
| Selfie preview and retake | Capture/retake/confirm controls, 5974–6061 and 6278 | Direct camera snapshot/upload and recapture; separate still preview/confirm/retake interaction is missing. |
| Immediate comparison feedback | Match/failure result and specific ID-quality/selfie-quality messages, 3925–4060 | Optional advisory YuNet/SFace results stored for review; equivalent student-visible analysis feedback is missing. |
| Automatic match-to-exam transition | Passing comparison marks verification completed and starts exam flow, 4000–4030, 6278–6337 | Replaced by explicit staff approval; not restored. AI-only facial matching is not used to determine exam admission. |
| Face detection cadence | Client FaceMesh model, maximum two faces, nominal one-second loop, 5714 and 6699 | Server YuNet on three-second camera samples. This is a different monitoring architecture and does not offer the same immediate visual feedback. |
| Camera face overlays | Browser draws a face box, 6730–6749 | Camera preview exists; face overlays are missing. |
| No-face/multiple-face warnings | Live component messages and multiple-face event, 6699 | Source-linked face-count observations are now generated independently of identity-review mode; live student warnings and duration-based aggregation are missing. |
| Gaze/focus label | Iris-spacing heuristic, 6905 | Not carried over. The old calculation is not a validated gaze/attention measure. |
| Sound display | Live normal/anomaly status and numeric spectral level, 6936–6994 | Calibrated RMS sustained-sound observations and monitoring status. A numeric live sound meter is missing; neither implementation identifies voices or proves cheating. |
| Component status panel | Camera, audio, mobile, screenshots, recording, network and behavioral state, 7154–7406 | Some capture/sync/status indicators exist; equivalent consolidated live panel is missing. |
| Fullscreen recovery | Warning/countdown/recovery controls, 6584–6650 and 7565–7630 | Fullscreen/focus observations exist; equivalent countdown and recovery UI is missing. |
| Shortcut/context-menu restrictions | Page handlers block shortcuts and right click, 7906–7923 | Not carried over. Page handlers are not operating-system lockdown. Optional Safe Exam Browser verification is the separate native integration. |
| Multiple displays | Screen Details/isExtended plus geometry heuristics, 7630–7820 and browser monitor class | Capture surface validation and client observation schema exist; equivalent active multi-display inventory/status UI is missing. |
| Remote-control monitoring | Several heuristic classes, websocket paths and a random-demo endpoint, 2543–2707 and 2819 | Client observation hooks exist, not the old full detector/UI. The legacy `check_remote_tools` randomly emits indicators with 10% probability; this is not detection and should not be restored. |
| Mouse, typing and input patterns | Python/browser classes and tracking endpoints, 1563–2707, 6764–6883 and 8076 onward | No equivalent behavioral-analysis system. Some code is heuristic scaffolding, not validated identification of remote control. Raw keystroke/clipboard collection is not carried over. |
| Screen evidence | Server `mss` screenshots every five seconds and screen recording; browser requests sharing, 2947 and 3099 | Evidence comes from the student's shared display and ordered encrypted recording chunks. The default screen snapshot interval is 15 seconds, not five. Legacy server-side capture would capture the server's desktop in a cloud deployment. |
| Adaptive bandwidth | Measurement and recording/snapshot quality profiles, browser manager starting 8587 | Fixed bounded capture settings and encrypted retry queue; equivalent measured adaptive quality is missing. Settings endpoints in the old file do not by themselves prove the running recorder applied every advertised setting. |
| Mobile camera | QR JSON containing session/websocket data, offer handling and heartbeats, 6996–7153 | Reachable HTTPS URL, expiring single-use capability, desktop confirmation, scoped phone access, uploads and WebRTC signaling. Legacy QR JSON was not an ordinary phone camera launch URL. |
| Offline capture | Browser offline manager and sync routes, 7947 and 4179 | Persistent encrypted queue and answer recovery exist; implementation differs. |
| Timer and submission | Browser/server timers, countdown and warnings, 4946–5008 and 6184 | Durable server deadline and saved answers exist; timed reminder UI is not equivalent. |
| Violation consequences | Visible count; browser ends exam at exactly five events, 7450–7477; other classes have different termination rules | Reviewable observations, with staff action for consequential decisions. Old automatic punishments were not preserved. |
| Per-exam configuration | Face/audio/behavior/screen/mobile toggles and thresholds, 3740 onward | Validated monitoring policies exist, but behavior-monitor controls and full legacy configuration parity are absent. |

## What “verified” meant in the old code

- Face recognition used `face_recognition` encodings and a distance threshold of 0.45 in `compare_faces_face_recognition` (754).
- `enhanced_face_verification` (812) combined a Boolean recognition result with structural similarity and grayscale histogram correlation using weights 0.8/0.15/0.05. Its weighted number is not a calibrated identity probability.
- Non-strict mode bypassed the exact-one-face requirement. The capture endpoint called it with `strict_mode=False`.
- Recognition exceptions could fall back to whole-image structural similarity. Whole-image similarity does not establish that two faces belong to the same person.
- ID upload checked image decoding, face presence and brightness. It did not validate government issuance, document security features, OCR fields or issuer records.
- `check_liveness` (957) is a single-image edge/color/noise heuristic. Searching this file finds its definition but no call site; it was not part of the actual capture verification path.

## Recommended implementation order

1. Restore immediate, truthful student feedback: ID face/quality results, selfie preview/retake, model-loading status, and clear comparison errors.
2. Restore a coherent live monitoring panel with face-count/no-face feedback, numeric sound level, capture status, network queue health and recording state.
3. Add warning persistence/debounce and clear fullscreen recovery. A transient detector output is an observation, not an automatic misconduct decision.
4. Implement browser-supported display inventory and measured capture adaptation, explicitly showing unavailable capabilities.
5. Validate ID/selfie matching and multiple-face detection using a representative, consented evaluation set before claiming improved accuracy. Document authentication, replay resistance and liveness remain separate work.
6. Choose an authorization flow explicitly. A self-service exam entry path can use verified institutional authentication/assignment while AI observations remain advisory. The old facial-match-only admission gate has not been recreated.

The feature inventory should guide the rebuild. It should not be interpreted as permission to reintroduce random alerts, unsupported authenticity/liveness claims, raw input surveillance, or automatic educational penalties from uncertain observations.
