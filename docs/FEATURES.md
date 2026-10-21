# Feature preservation

The original files remain unchanged in `legacy/`. Their unsafe routes are not exposed and old clients are not wire-compatible.

See [the implemented restoration matrix](RESTORATION.md) for each legacy capability, its working replacement, admission options, native-browser boundaries, and explicit exclusions. [The initial source comparison](LEGACY_PARITY_REVIEW.md) records the gaps that prompted this restoration; it is historical, not the current feature list.

The service preserves assessment creation/assignment, enrollment checks, ID/selfie capture and comparison evidence, live camera/sound/display monitoring, snapshots and recordings, mobile pairing, bandwidth adaptation, offline recovery, timers, reports and per-exam controls. It does not certify ID authenticity, liveness or 100% accuracy. Staff-review admission and assigned-account admission are separate from advisory facial comparison.
