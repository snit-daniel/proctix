# Security and trust boundaries

## What the service enforces

- Every authenticated account belongs to one institution. There is no tenant ID supplied by a student to select another institution. Named tenant filters and composite database foreign keys protect tenant-owned relationships. UUIDs are identifiers, not credentials.
- Institution admins manage users/assessments; admins and proctors review evidence; students operate only their assigned attempts. Mobile devices have a separate capability and cannot use normal account APIs.
- Argon2id hashes passwords. Opaque, randomly generated login cookies are HttpOnly, SameSite=Strict, Secure under HTTPS, time limited, and hashed in the database. Password changes/deactivation revoke logins. Production staff sign-in requires confirmed TOTP enrollment; each authenticator time step is single use. This is TOTP MFA, not phishing-resistant WebAuthn.
- Same-origin Origin validation and an HMAC-derived CSRF token protect state-changing requests. Browser code and styles are local, CSP excludes inline/evaluated scripts, and dynamic values use text nodes rather than HTML interpolation. No wildcard CORS.
- Login, verification, evidence, events, signaling and answer writes have persistent database rate limits. The reverse proxy caps bodies; the app bounds streamed request bodies, image dimensions and per-session storage. Add edge-level connection/DoS protection for Internet deployment.
- PostgreSQL row locks protect transitions, attempt allocation, revisions and recording sequence numbers. The development SQLite path is not a substitute for production concurrency validation.
- AES-256-GCM encrypts each evidence file, saved response document, staff MFA secret and stored SEB key. Unique nonces and authenticated context bind ciphertext to its tenant/object. The encryption key is supplied through secrets management, never stored in the database. Content hashes support recording integrity checks. Filesystem identifiers are server UUIDs and client filenames are ignored.
- Audit records include actor, action, target, timestamp and an HMAC. This detects modification of individual records when the signing key is unavailable to an attacker. It does not prevent deletion of records or protect against a compromised application/key. Export audit records to an independent, append-only destination for stronger assurance.
- Identity approval starts pending, requires consent/evidence, and is issued only by staff. Replacing identity evidence revokes prior approval. Model exceptions never imply successful identity verification.
- Server time governs fixed deadlines and accepted answers. A reload/repeated start cannot extend the deadline. A worker closes disconnected expired attempts and purges expired content.

## What remains untrusted

Browser focus, input, microphone availability, display metadata, timestamps and submitted sensor images are client-controlled. They are labeled client-reported observations. HTTPS authenticates transport; it does not prove a student device is uncompromised. SEB request verification is an integration control with known endpoint trust limits, not hardware-backed attestation. No ordinary web application can prove the absence of a second physical device, external helper, hardware capture device or privileged OS malware.

A selfie/document comparison is neither document authentication nor liveness. Reviewers must compare the person and document with an independently trusted institutional enrollment record. YuNet/SFace evidence must not become an automatic cheating/identity verdict. There is no validated gaze estimator, speech recognizer, deepfake detector or universal remote-control detector in this release. Noise and alternate input methods are not misconduct.

Live camera samples can establish that the *service received images*, not that those images came from an uncompromised physical camera. Mobile pairing establishes possession/confirmation of a short-lived capability, not identity of the person holding the phone.

## Offline data and disconnects

AES-GCM IndexedDB records use a persistent non-extractable Web Crypto key with object-specific associated data. Recovery survives reload, including queued recording chunks. Scripts running under the same origin can still use that key; device compromise and an active XSS can access local data. Private browsing/storage eviction can destroy queued content. Never promise perfect offline reliability.

The server records receipt times and treats late telemetry as untrusted. Pending answers must arrive before the fixed deadline. On reconnection, failed evidence remains queued and the UI reports it. Final submission waits for durable uploads and acknowledged answer saves. The exam timer does not pause when offline.

## Encryption operations

The initial release supports one configured encryption key version. Keep the key in an institution-approved secret manager and retain a tested offline backup. Replacing the key without re-encrypting every encrypted field/file makes existing data unreadable. Rotate by a controlled offline re-encryption migration with backups and a verified restore; do not change `PROCTIX_KEY_ID` casually. Auth-key rotation revokes CSRF tokens and changes audit verification; preserve the old key with historical audit exports. A KMS envelope-key adapter and automated key rotation are not implemented.

## Storage and retention

The reference deployment uses an encrypted shared filesystem volume and PostgreSQL. Every web/maintenance worker must see the same filesystem. Mount a durable encrypted network filesystem for multiple hosts; do not use independent ephemeral disks. A managed object-storage adapter is not implemented.

Retention clears identity/video/image contents and saved answers after each session's policy expires. The UI and database retain minimal review/audit metadata, which needs a separately documented institution retention schedule. Database backups and storage snapshots must have matching expiry/deletion rules. Browser queues can remain on an abandoned device; use managed-device cleanup and supply support for recovery/deletion. This is not a legal compliance certification.

## Operational requirements

Use a least-privilege runtime database role, a separate migration role, a managed TLS origin, trusted proxy IPs, protected staff enrollment, monitoring, backups and a restore drill. Never expose PostgreSQL or runtime port 8000 directly to the Internet. No secret credentials should be passed in query strings or recorded in access logs. Report unexpected access or integrity failures to the service operator; pause real assessments during investigation.
