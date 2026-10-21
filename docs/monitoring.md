# Automatic monitoring

Camera samples default to every three seconds while capture is active; cadence is configurable and may be reduced by the adaptive bandwidth profile. This is sampled monitoring, not continuous frame-by-frame analysis. Face counting now runs independently of the identity-review policy. Abnormal face counts and face-image quality produce observations linked to the source evidence; they never automatically terminate an assessment or establish misconduct. Missing models produce an explicit unavailable observation, once per session.

YuNet detects faces. Face quality measures brightness and sharpness inside the detected face crop, rather than trusting the background. SFace comparison remains an advisory identity check for assessments configured for biometric review. Uploaded ID authenticity and liveness are explicitly unverified. A cosine similarity is not a probability or proof of identity. Model weights are hash checked. The current local test session has biometric review enabled; other identity policies are unchanged.

Microphone analysis uses RMS sound level, a five-second baseline, a 12 dB rise over the median baseline with a -38 dBFS floor, 1.5 seconds of sustained sound, and a 15-second reporting cooldown. This does not classify voices, distinguish speakers, or identify the sound source. It cannot establish cheating. Paused audio analysis is displayed explicitly. An institution must validate thresholds for its rooms and accommodations.

The expanded regression coverage and current feature boundaries are documented in [the restoration matrix](RESTORATION.md). These are implementation regressions, not measured real-world accuracy. No representative ID/selfie, multi-face or audio validation corpus was supplied. No best-model or guaranteed-accuracy claim is made.

Reference: [OpenCV face detection and recognition documentation](https://docs.opencv.org/4.13.0/d0/dd4/tutorial_dnn_face.html).
