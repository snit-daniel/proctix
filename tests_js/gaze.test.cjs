const { test } = require("node:test");
const assert = require("node:assert/strict");
const {
  fitGaze,
  predictGaze,
  validateGaze,
  gazeFeatures,
} = require("../proctix/static/gaze.js");
function sample(x, y) {
  return { f: [x, y, x * 0.3, y * 0.2, 0.5, 0.5, 0.4, 0], x, y };
}
test("per-user calibration learns a mapping and validates on independent points", () => {
  const samples = [];
  for (let i = 0; i < 3; i++)
    for (const x of [0.1, 0.5, 0.9])
      for (const y of [0.1, 0.5, 0.9])
        for (let n = 0; n < 2; n++) samples.push(sample(x, y));
  const model = fitGaze(samples),
    heldout = [
      sample(0.3, 0.3),
      sample(0.7, 0.3),
      sample(0.3, 0.7),
      sample(0.7, 0.7),
      sample(0.5, 0.5),
    ];
  assert.ok(Math.abs(predictGaze(model, sample(0.7, 0.3).f).x - 0.7) < 0.02);
  assert.equal(validateGaze(model, heldout, 1200, 800).valid, true);
  assert.equal(
    validateGaze(
      model,
      heldout.map((s) => ({ ...s, x: 1 - s.x, y: 1 - s.y })),
      1200,
      800,
    ).valid,
    false,
  );
});
test("static or missing eye features cannot pass validation or produce a gaze prediction", () => {
  const samples = [];
  for (const x of [0.1, 0.5, 0.9])
    for (const y of [0.1, 0.5, 0.9])
      for (let n = 0; n < 5; n++) samples.push({ f: Array(8).fill(0.5), x, y });
  const model = fitGaze(samples);
  assert.equal(
    validateGaze(
      model,
      [
        sample(0.3, 0.3),
        sample(0.7, 0.3),
        sample(0.3, 0.7),
        sample(0.7, 0.7),
        sample(0.5, 0.5),
      ],
      1200,
      800,
    ).valid,
    false,
  );
  assert.equal(gazeFeatures({ faceLandmarks: [] }), null);
  assert.equal(gazeFeatures({ faceLandmarks: [[], []] }), null);
  assert.throws(() => fitGaze([]), /Not enough/);
});

test("closed eye geometry is unavailable rather than a looking-away estimate", () => {
  const points = Array.from({ length: 478 }, () => ({ x: 0.5, y: 0.5 }));
  for (const [i, x, y] of [
    [33, 0.3, 0.4],
    [133, 0.4, 0.4],
    [468, 0.35, 0.4],
    [362, 0.6, 0.4],
    [263, 0.7, 0.4],
    [473, 0.65, 0.4],
    [454, 0.8, 0.5],
    [234, 0.2, 0.5],
    [159, 0.35, 0.39],
    [145, 0.35, 0.41],
    [386, 0.65, 0.39],
    [374, 0.65, 0.41],
  ])
    points[i] = { x, y };
  assert.equal(gazeFeatures({ faceLandmarks: [points] }).length, 8);
  points[159].y = 0.4;
  points[145].y = 0.4;
  assert.equal(gazeFeatures({ faceLandmarks: [points] }), null);
});

test("calibrated outside-screen estimates produce observations only after sustained active-exam samples", () => {
  const { GazeController } = require("../proctix/static/gaze.js");
  const original = {
    document: global.document,
    innerWidth: global.innerWidth,
    innerHeight: global.innerHeight,
    now: Date.now,
  };
  const nodes = {
    "gaze-status": { textContent: "" },
    "gaze-brief": { textContent: "" },
    "camera-preview": {
      srcObject: { getVideoTracks: () => [{ id: "test-camera" }] },
    },
  };
  global.document = {
    hidden: false,
    hasFocus: () => true,
    getElementById: (id) => nodes[id],
  };
  global.innerWidth = 1200;
  global.innerHeight = 800;
  let now = 100000;
  Date.now = () => now;
  const state = { session: { status: "preflight" }, stopping: false },
    events = [];
  const c = new GazeController({
    state: () => state,
    event: (kind, details) => events.push({ kind, details }),
  });
  clearInterval(c.interval);
  try {
    const samples = [];
    for (const x of [0.1, 0.5, 0.9])
      for (const y of [0.1, 0.5, 0.9])
        for (let i = 0; i < 5; i++) samples.push(sample(x, y));
    c.model = fitGaze(samples);
    c.width = 1200;
    c.height = 800;
    c.track = "test-camera";
    c.validationError = 0.05;
    c.lastAlert = 0;
    c.frame = () => sample(1.6, 0.5).f;
    c.tick();
    now += 6000;
    c.tick();
    assert.equal(events.length, 0);
    state.session.status = "in_progress";
    c.tick();
    now += 4500;
    c.tick();
    assert.equal(events.length, 0);
    now += 500;
    c.tick();
    assert.equal(events.length, 1);
    assert.equal(events[0].kind, "gaze_outside_calibrated_area");
    assert.equal(events[0].details.duration_ms, 5000);
    assert.match(nodes["gaze-brief"].textContent, /outside screen/);
    now += 1000;
    c.tick();
    assert.equal(events.length, 1);
    c.frame = () => null;
    c.tick();
    assert.equal(c.awaySince, null);
    assert.match(nodes["gaze-brief"].textContent, /eyes not visible/);
    c.frame = () => sample(0.5, 0.5).f;
    c.tick();
    assert.match(nodes["gaze-brief"].textContent, /within screen/);
  } finally {
    c.stop();
    Date.now = original.now;
    global.document = original.document;
    global.innerWidth = original.innerWidth;
    global.innerHeight = original.innerHeight;
  }
});

test("automatic eye direction does not require calibration and rejects blinks or multiple faces", () => {
  const { automaticDirection } = require("../proctix/static/gaze.js");
  const p = Array.from({ length: 478 }, () => ({ x: 0.5, y: 0.5 }));
  for (const [i, x, y] of [
    [33, 0.3, 0.4],
    [133, 0.4, 0.4],
    [468, 0.35, 0.4],
    [362, 0.6, 0.4],
    [263, 0.7, 0.4],
    [473, 0.65, 0.4],
    [454, 0.8, 0.5],
    [234, 0.2, 0.5],
    [159, 0.35, 0.39],
    [145, 0.35, 0.41],
    [386, 0.65, 0.39],
    [374, 0.65, 0.41],
  ])
    p[i] = { x, y };
  assert.equal(automaticDirection({ faceLandmarks: [p] }).away, false);
  p[468].x = 0.38;
  p[473].x = 0.68;
  assert.equal(automaticDirection({ faceLandmarks: [p] }).away, true);
  assert.equal(automaticDirection({ faceLandmarks: [p, p] }), null);
  p[159].y = p[145].y;
  assert.equal(automaticDirection({ faceLandmarks: [p] }), null);
});

test("uncalibrated direction controller records only sustained active-exam estimates", () => {
  const { GazeController } = require("../proctix/static/gaze.js");
  const old = { document: global.document, now: Date.now };
  let now = 100000;
  const nodes = {
    "gaze-status": { textContent: "" },
    "gaze-brief": { textContent: "" },
    "camera-preview": {
      videoWidth: 640,
      srcObject: { getVideoTracks: () => [{ readyState: "live" }] },
    },
  };
  global.document = {
    hidden: false,
    hasFocus: () => true,
    getElementById: (id) => nodes[id],
  };
  Date.now = () => now;
  const state = {
      session: { status: "preflight", policy: {} },
      stopping: false,
    },
    events = [];
  const c = new GazeController({
    state: () => state,
    event: (kind, details) => events.push({ kind, details }),
  });
  clearInterval(c.interval);
  c.landmarker = { close() {} };
  c.frame = () => {
    c.automaticSample = { away: true };
  };
  try {
    c.tick();
    now += 6000;
    c.tick();
    assert.equal(events.length, 0);
    state.session.status = "in_progress";
    c.tick();
    now += 5000;
    c.tick();
    assert.equal(events.length, 1);
    assert.equal(events[0].kind, "looking_away_estimate");
    assert.deepEqual(events[0].details, { duration_ms: 5000 });
    now += 1000;
    c.tick();
    assert.equal(events.length, 1);
    global.document.hidden = true;
    c.tick();
    assert.equal(c.automaticAwaySince, null);
    global.document.hidden = false;
    c.frame = () => {
      c.automaticSample = null;
    };
    c.tick();
    assert.match(nodes["gaze-brief"].textContent, /eyes not visible/);
  } finally {
    c.stop();
    global.document = old.document;
    Date.now = old.now;
  }
});
