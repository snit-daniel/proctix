const { test } = require("node:test");
const assert = require("node:assert/strict");
const { SoundActivityDetector } = require("../proctix/static/monitoring.js");
function calibrated(rms = 0.001) {
  const d = new SoundActivityDetector();
  for (let i = 0; i < 20; i++) assert.equal(d.sample(rms, i * 250), null);
  return d;
}
test("quiet baseline and brief sound do not produce an event", () => {
  const d = calibrated();
  assert.equal(d.sample(0.1, 5000), null);
  assert.equal(d.sample(0.001, 5250), null);
  assert.equal(d.sample(0.1, 5500), null);
  assert.equal(d.sample(0.001, 7000), null);
});
test("sustained sound records duration, is rate limited and re-arms after silence", () => {
  const d = calibrated();
  assert.equal(d.sample(0.1, 5000), null);
  assert.equal(d.sample(0.1, 6250), null);
  assert.deepEqual(d.sample(0.1, 6500), { value: 0.1, duration_ms: 1500 });
  assert.equal(d.sample(0.1, 7000), null);
  d.sample(0.001, 20000);
  assert.equal(d.sample(0.1, 21000), null);
  assert.ok(d.sample(0.1, 22500));
});
test("calibration adapts to a noisy room rather than flagging its baseline", () => {
  const d = calibrated(0.05);
  assert.equal(d.sample(0.05, 7000), null);
  assert.equal(d.sample(0.05, 10000), null);
  assert.equal(d.sample(NaN, 10500), null);
});

const {
  captureProfile,
  qualityMessage,
} = require("../proctix/static/monitoring.js");
test("capture adaptation respects organizer limits and reduces load under backlog", () => {
  const limits = {
    recording_fps: 4,
    recording_bitrate: 150000,
    screenshot_interval_seconds: 12,
    camera_interval_seconds: 6,
  };
  const slow = captureProfile(100, 0, limits);
  assert.equal(slow.width, 640);
  assert.ok(slow.fps <= 4);
  assert.ok(slow.bitrate <= 150000);
  assert.ok(slow.screenSeconds >= 12);
  assert.ok(slow.cameraSeconds >= 6);
  assert.equal(
    captureProfile(2000, 10 * 1048576, limits).name,
    "Low bandwidth",
  );
  assert.equal(captureProfile(null, 0, limits).name, "Standard");
});
test("quality feedback distinguishes absent faces, multiple faces, bad quality and model unavailability", () => {
  assert.match(
    qualityMessage({ status: "assessed", face_count: 0 }),
    /No clear face/,
  );
  assert.match(
    qualityMessage({ status: "assessed", face_count: 2 }),
    /More than one/,
  );
  assert.match(
    qualityMessage({
      status: "assessed",
      face_count: 1,
      brightness: 20,
      sharpness: 5,
      face_pixels: 30,
    }),
    /increase lighting.*improve focus.*move closer/,
  );
  assert.match(qualityMessage({ status: "unavailable" }), /unavailable/);
});

const { photoRetryMessage } = require("../proctix/static/monitoring.js");
test("photo guidance targets the unclear image and separates model failure from capture failure", () => {
  assert.match(
    photoRetryMessage(
      { status: "insufficient_quality", reference: { quality_ok: false } },
      1,
    ),
    /Replace the ID photo/,
  );
  assert.match(
    photoRetryMessage(
      { status: "insufficient_quality", candidate: { quality_ok: false } },
      2,
    ),
    /Retake your selfie alone/,
  );
  assert.match(
    photoRetryMessage({ status: "unavailable" }, 1),
    /retaking is not necessary/,
  );
  assert.match(
    photoRetryMessage({ status: "compared" }, 2),
    /not a verified identity/,
  );
});
test("five guided checks stop automatic reruns without claiming identity approval or denying account access", () => {
  assert.equal(photoRetryMessage(null, 0), "");
  assert.match(photoRetryMessage({ status: "compared" }, 4), /1 remaining/);
  const message = photoRetryMessage({ status: "insufficient_quality" }, 5);
  assert.match(message, /No further checks will run automatically/);
  assert.match(message, /another verification method/);
  assert.match(photoRetryMessage({}, 99), /5 of 5/);
});

test("document guidance explains appearance and does not require an MRZ", () => {
  const { documentCheckMessages } = require("../proctix/static/monitoring.js");
  const messages = documentCheckMessages({
    appearance_guidance:
      "ID features and portrait found; not proof of a genuine document.",
    text_readable: true,
    mrz_detected: false,
    registered_name_found: true,
  }).join(" ");
  assert.match(messages, /ID features and portrait/);
  assert.match(messages, /not proof/);
  assert.doesNotMatch(messages, /MRZ not found/);
  assert.match(documentCheckMessages(null).join(" "), /student ID/);
});

test("comparison summaries are brief and low similarity gives an action, not an access verdict", () => {
  const { comparisonMessage } = require("../proctix/static/monitoring.js");
  const low = comparisonMessage({
    status: "compared",
    cosine_similarity: 0.1,
    review_threshold: 0.4,
  });
  assert.match(low, /Photos appear different/);
  assert.match(low, /another verification method/);
  assert.doesNotMatch(low, /prohibited|denied|approved/);
  assert.equal(
    comparisonMessage({
      status: "compared",
      cosine_similarity: 0.7,
      review_threshold: 0.4,
    }),
    "The photos have similar facial features. This is an advisory comparison result.",
  );
});

test("quiet sustained input above a quiet-room baseline is observable", () => {
  const d = calibrated(0.0005);
  assert.equal(d.threshold, 0.0015);
  assert.equal(d.sample(0.004, 5000), null);
  assert.deepEqual(d.sample(0.004, 6500), { value: 0.004, duration_ms: 1500 });
  assert.equal(d.sample(0.004, 25000), null);
});
test("calibration resists brief loud contamination and background noise does not trigger", () => {
  const d = new SoundActivityDetector();
  assert.equal(d.threshold, null);
  for (let i = 0; i < 20; i++) d.sample(i < 8 ? 0.1 : 0.0005, i * 250);
  assert.equal(d.threshold, 0.0015);
  const noisy = calibrated(0.02);
  for (let i = 0; i < 100; i++)
    assert.equal(noisy.sample(0.02, 5000 + i * 250), null);
});

test("high-resolution audio timestamps produce integer durations accepted by the API", () => {
  const d = calibrated(0.0005);
  d.sample(0.004, 5000.125);
  assert.deepEqual(d.sample(0.004, 6500.875), {
    value: 0.004,
    duration_ms: 1501,
  });
});

test("usable but non-ideal photos receive a recommendation rather than a rejection", () => {
  assert.match(
    qualityMessage({
      status: "assessed",
      face_count: 1,
      quality_ok: false,
      comparison_usable: true,
    }),
    /usable for comparison/,
  );
  const { comparisonMessage } = require("../proctix/static/monitoring.js");
  assert.equal(
    comparisonMessage({ status: "compared" }),
    "Photo comparison complete. See comparison details.",
  );
});

test("comparison headings distinguish similar, different and unassessable photos", () => {
  const { comparisonPresentation } = require("../proctix/static/monitoring.js");
  assert.equal(
    comparisonPresentation({
      status: "compared",
      cosine_similarity: -0.01,
      review_threshold: 0.4,
    }).title,
    "Faces appear different",
  );
  assert.equal(
    comparisonPresentation({
      status: "compared",
      cosine_similarity: 0.7,
      review_threshold: 0.4,
    }).title,
    "Faces appear to match",
  );
  assert.equal(
    comparisonPresentation({ status: "insufficient_quality" }).title,
    "Could not compare these photos",
  );
  assert.equal(comparisonPresentation(null).title, "No comparison result yet");
});

test("a differing photo result offers four retries after check one and preserves an alternative route", () => {
  const {
    photoNeedsRetry,
    photoRetryMessage,
  } = require("../proctix/static/monitoring.js");
  const low = {
    status: "compared",
    cosine_similarity: 0.1,
    review_threshold: 0.4,
  };
  assert.equal(photoNeedsRetry(low), true);
  assert.match(photoRetryMessage(low, 1), /4 checks remaining/);
  assert.match(photoRetryMessage(low, 1), /account verification instead/);
  assert.equal(photoNeedsRetry({ ...low, cosine_similarity: 0.8 }), false);
  assert.equal(photoNeedsRetry({ status: "compared" }), false);
});
