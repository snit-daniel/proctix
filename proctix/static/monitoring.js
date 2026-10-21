"use strict";
class SoundActivityDetector {
  constructor() {
    this.baseline = 0;
    this.calibration = 0;
    this.started = null;
    this.lastEvent = -Infinity;
    this.armed = true;
    this.backgroundSamples = [];
  }
  get threshold() {
    return this.calibration < 20 ? null : Math.max(0.0015, this.baseline * 2.5);
  }
  sample(rms, now = Date.now()) {
    if (!Number.isFinite(rms) || rms < 0 || !Number.isFinite(now)) return null;
    if (this.calibration < 20) {
      this.backgroundSamples.push(rms);
      this.calibration++;
      if (this.calibration === 20) {
        // Brief speech/noise during setup must not inflate the noise floor.
        this.backgroundSamples.sort((a, b) => a - b);
        this.baseline = this.backgroundSamples[3];
        this.backgroundSamples = [];
      }
      return null;
    }
    const active = rms > this.threshold;
    if (!active) {
      this.started = null;
      this.armed = true;
      // Adapt only to near-background samples, not a sustained quiet voice.
      if (rms <= Math.max(0.0015, this.baseline * 1.5))
        this.baseline = 0.98 * this.baseline + 0.02 * rms;
      return null;
    }
    this.started ??= now;
    if (
      this.armed &&
      now - this.started >= 1500 &&
      now - this.lastEvent >= 15000
    ) {
      this.armed = false;
      this.lastEvent = now;
      return { value: rms, duration_ms: Math.round(now - this.started) };
    }
    return null;
  }
}
function captureProfile(kbps, pendingBytes = 0, policy = {}) {
  const low =
    (Number.isFinite(kbps) && kbps < 350) || pendingBytes > 8 * 1048576;
  return {
    name: low ? "Low bandwidth" : "Standard",
    width: low ? 640 : 1280,
    fps: Math.min(policy.recording_fps || 5, low ? 2 : 5),
    bitrate: Math.min(
      policy.recording_bitrate || 450000,
      low ? 150000 : 450000,
    ),
    screenSeconds: Math.max(
      policy.screenshot_interval_seconds || 5,
      low ? 10 : 5,
    ),
    cameraSeconds: Math.max(policy.camera_interval_seconds || 3, low ? 6 : 3),
  };
}
function qualityMessage(result) {
  if (!result || result.status !== "assessed")
    return "Image saved; automated face-quality analysis is unavailable. This is not a verified identity.";
  if (result.face_count === 0)
    return "No clear face found. Use a sharper, well-lit image with the face visible.";
  if (result.face_count > 1 && !result.document_portrait_selected)
    return "More than one face found. Use an image with one clear primary portrait.";
  const changes = [];
  if (result.brightness <= 40) changes.push("increase lighting");
  if (result.brightness >= 220) changes.push("reduce glare or overexposure");
  if (result.sharpness <= 40) changes.push("improve focus");
  if (result.face_pixels < 80) changes.push("move closer");
  if (!result.quality_ok)
    if (result.comparison_usable)
      return "Photo is usable for comparison. A sharper or higher-resolution photo is recommended.";
  if (!result.quality_ok)
    return (
      "Image needs improvement: " +
      (changes.join(", ") || "keep your whole face clear and centered") +
      "."
    );
  return (
    (result.document_portrait_selected
      ? "Primary ID portrait selected; a smaller secondary printed portrait is also present"
      : "One face found") +
    ". Image quality is suitable for comparison; document authenticity is not verified."
  );
}
function comparisonMessage(result) {
  if (result.status === "compared") {
    if (
      !Number.isFinite(result.cosine_similarity) ||
      !Number.isFinite(result.review_threshold)
    )
      return "Photo comparison complete. See comparison details.";
    return result.cosine_similarity < result.review_threshold
      ? "Photos appear different. Check that you uploaded the correct ID and selfie, or ask your organizer for another verification method."
      : "The photos have similar facial features. This is an advisory comparison result.";
  }
  if (result.status === "insufficient_quality")
    return "Face comparison needs clearer images. Check the ID and selfie quality messages, then retake.";
  if (result.status === "unavailable")
    return "Face comparison model unavailable. This is not a successful identity check.";
  return "Identity evidence submitted for review.";
}
function photoNeedsRetry(result) {
  return (
    result?.status === "insufficient_quality" ||
    (result?.status === "compared" &&
      Number.isFinite(result.cosine_similarity) &&
      Number.isFinite(result.review_threshold) &&
      result.cosine_similarity < result.review_threshold)
  );
}
function comparisonPresentation(result) {
  if (!result)
    return {
      title: "No comparison result yet",
      message: "Select Run image checks again after both photos are saved.",
    };
  if (result.status === "compared") {
    const scored =
      Number.isFinite(result.cosine_similarity) &&
      Number.isFinite(result.review_threshold);
    return {
      title: !scored
        ? "Comparison completed"
        : result.cosine_similarity < result.review_threshold
          ? "Faces appear different"
          : "Faces appear to match",
      message: comparisonMessage(result),
    };
  }
  return {
    title:
      result.status === "insufficient_quality"
        ? "Could not compare these photos"
        : result.status === "unavailable"
          ? "Comparison unavailable"
          : "Review pending",
    message: comparisonMessage(result),
  };
}
function photoRetryMessage(result, attempts) {
  if (!result) return "";
  const count = Math.max(0, Math.min(5, Number(attempts) || 0));
  if (count >= 5)
    return "5 of 5 image checks used. No further checks will run automatically. Ask your organizer for another verification method.";
  const remaining = `Check ${count} of 5 · ${5 - count} remaining. `;
  if (result.status === "compared" && photoNeedsRetry(result))
    return `Photos appear different — ${5 - count} ${5 - count === 1 ? "check" : "checks"} remaining. Replace your ID photo or retake your selfie, then run the check again. You may use account verification instead.`;
  if (result.status === "insufficient_quality") {
    if (result.reference?.quality_ok === false)
      return (
        remaining +
        "Replace the ID photo with the portrait side. Keep the whole document sharp and avoid glare."
      );
    if (result.candidate?.quality_ok === false)
      return (
        remaining +
        "Retake your selfie alone in good lighting, with your full face visible."
      );
    return (
      remaining +
      "Use the portrait side of your ID and a sharp, well-lit selfie with only your face visible."
    );
  }
  if (result.status === "unavailable")
    return (
      remaining +
      "The image model is unavailable; retaking is not necessary. Try again later or contact your organizer."
    );
  return (
    remaining +
    "Comparison complete; not a verified identity. Review your photos before continuing."
  );
}
if (typeof module !== "undefined" && module.exports)
  module.exports = {
    SoundActivityDetector,
    captureProfile,
    qualityMessage,
    comparisonMessage,
    comparisonPresentation,
    photoNeedsRetry,
    photoRetryMessage,
  };
function documentCheckMessages(result) {
  if (!result)
    return [
      "Upload the full photo side of your ID. Passport, residency card, student ID, or another photo ID.",
    ];
  if (result.status === "unavailable")
    return [
      result.reason || "Document data checks unavailable.",
      "Document authenticity is not verified.",
    ];
  const rows = [
    ...(result.appearance_guidance ? [result.appearance_guidance] : []),
    result.text_readable
      ? "Document text could be read."
      : "Not enough readable text. Use a sharp full-document image without glare.",
  ];
  if (result.mrz_detected) {
    rows.push(
      result.checksum_consistent
        ? "MRZ check digits are internally consistent. This does not establish authenticity."
        : "MRZ check digits are inconsistent or unreadable. Re-upload a clearer image; this is not proof of forgery.",
    );
    if (result.expired !== null)
      rows.push(
        result.expired
          ? "The MRZ expiry date is in the past. Check the document."
          : "The MRZ expiry date is not in the past.",
      );
  }
  if (result.registered_name_found != null)
    rows.push(
      result.registered_name_found
        ? "Registered name was found in the read text."
        : "Registered name was not found. OCR or name formatting may differ; review the image.",
    );
  rows.push(
    "Document appearance is checked locally. Genuine-document authenticity and issuing authority have not been verified.",
  );
  return rows;
}
if (typeof module !== "undefined" && module.exports)
  module.exports.documentCheckMessages = documentCheckMessages;
