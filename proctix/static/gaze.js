"use strict";
// Local, per-attempt calibration. No gaze coordinates, eye crops or model coefficients are uploaded.
function gazeFeatures(result) {
  if (result.faceLandmarks?.length !== 1) return null;
  const p = result.faceLandmarks[0];
  function eye(a, b, c) {
    const dx = p[b].x - p[a].x,
      dy = p[b].y - p[a].y,
      scale = Math.hypot(dx, dy);
    if (scale < 0.008) return null;
    const x = p[c].x - (p[a].x + p[b].x) / 2,
      y = p[c].y - (p[a].y + p[b].y) / 2;
    return [
      (x * dx + y * dy) / (scale * scale),
      (y * dx - x * dy) / (scale * scale),
    ];
  }
  if (!p[473]) return null;
  // Reject closed-eye geometry rather than treating a blink as a gaze direction.
  const distance = (a, b) => Math.hypot(p[a].x - p[b].x, p[a].y - p[b].y);
  if (
    distance(159, 145) / Math.max(distance(33, 133), 1e-6) < 0.06 ||
    distance(386, 374) / Math.max(distance(362, 263), 1e-6) < 0.06
  )
    return null;

  const left = eye(33, 133, 468),
    right = eye(362, 263, 473);
  if (!left || !right) return null;
  const width = Math.hypot(p[454].x - p[234].x, p[454].y - p[234].y);
  const f = [
    ...left,
    ...right,
    p[1].x,
    p[1].y,
    width,
    Math.atan2(p[263].y - p[33].y, p[263].x - p[33].x),
  ];
  return f.every(Number.isFinite) && width > 0.08 ? f : null;
}
function automaticDirection(result) {
  const features = gazeFeatures(result);
  if (!features) return null;
  const p = result.faceLandmarks[0];
  const span = Math.hypot(p[263].x - p[33].x, p[263].y - p[33].y);
  if (span < 0.04) return null;
  const noseOffset = (p[1].x - (p[33].x + p[263].x) / 2) / span;
  const eyesTurned =
    Math.abs(features[0]) > 0.22 &&
    Math.abs(features[2]) > 0.22 &&
    Math.sign(features[0]) === Math.sign(features[2]);
  // Geometry estimate, not a screen-boundary or attention classifier.
  return { away: eyesTurned || Math.abs(noseOffset) > 0.28 };
}
function solveGaze(matrix, vector) {
  const a = matrix.map((row, i) => [...row, vector[i]]),
    n = vector.length;
  for (let k = 0; k < n; k++) {
    let pivot = k;
    for (let i = k + 1; i < n; i++)
      if (Math.abs(a[i][k]) > Math.abs(a[pivot][k])) pivot = i;
    [a[k], a[pivot]] = [a[pivot], a[k]];
    if (Math.abs(a[k][k]) < 1e-10)
      throw Error(
        "Calibration is unstable; retry with clear eyes and consistent head position.",
      );
    const v = a[k][k];
    for (let j = k; j <= n; j++) a[k][j] /= v;
    for (let i = 0; i < n; i++)
      if (i !== k) {
        const s = a[i][k];
        for (let j = k; j <= n; j++) a[i][j] -= s * a[k][j];
      }
  }
  return a.map((row) => row[n]);
}
function fitGaze(samples) {
  if (samples.length < 30) throw Error("Not enough calibration samples.");
  const n = samples[0].f.length;
  const mean = Array.from(
    { length: n },
    (_, j) => samples.reduce((s, p) => s + p.f[j], 0) / samples.length,
  );
  const scale = mean.map((m, j) =>
    Math.max(
      0.02,
      Math.sqrt(
        samples.reduce((s, p) => s + (p.f[j] - m) ** 2, 0) / samples.length,
      ),
    ),
  );
  const rows = samples.map((s) => [
    1,
    ...s.f.map((x, j) => (x - mean[j]) / scale[j]),
  ]);
  const matrix = Array.from({ length: n + 1 }, (_, i) =>
    Array.from(
      { length: n + 1 },
      (_, j) =>
        rows.reduce((s, r) => s + r[i] * r[j], 0) +
        (i === j && i > 0 ? 0.1 : 0),
    ),
  );
  return {
    mean,
    scale,
    x: solveGaze(
      matrix,
      rows[0].map((_, j) =>
        rows.reduce((s, r, i) => s + r[j] * samples[i].x, 0),
      ),
    ),
    y: solveGaze(
      matrix,
      rows[0].map((_, j) =>
        rows.reduce((s, r, i) => s + r[j] * samples[i].y, 0),
      ),
    ),
  };
}
function predictGaze(model, f) {
  const row = [1, ...f.map((v, j) => (v - model.mean[j]) / model.scale[j])];
  return {
    x: row.reduce((s, v, j) => s + v * model.x[j], 0),
    y: row.reduce((s, v, j) => s + v * model.y[j], 0),
  };
}
function validateGaze(model, samples, width, height) {
  if (samples.length < 5) return { valid: false, error: Infinity };
  const d = Math.hypot(width, height),
    errors = samples
      .map((s) => {
        const p = predictGaze(model, s.f);
        return Math.hypot((p.x - s.x) * width, (p.y - s.y) * height) / d;
      })
      .sort((a, b) => a - b);
  const median = errors[Math.floor(errors.length / 2)],
    maximum = errors[errors.length - 1];
  return {
    valid: Number.isFinite(median) && median <= 0.12 && maximum <= 0.2,
    error: median,
  };
}
class GazeController {
  constructor(context) {
    this.context = context;
    this.epoch = 0;
    this.interval = setInterval(() => this.tick(), 500);
    this.status(
      "Automatic eye/head-direction checks start with the exam. Calibration is optional for screen-position estimates.",
      "Gaze: starts with exam",
    );
  }
  status(
    text,
    brief = this.model ? "Gaze: calibrated" : "Gaze: automatic estimates",
  ) {
    document.getElementById("gaze-status").textContent = text;
    document.getElementById("gaze-brief").textContent = brief;
  }
  async initialize() {
    if (this.landmarker) return;
    if (this.loading) return this.loading;
    this.loading = (async () => {
      const { FaceLandmarker, FilesetResolver } =
        await import("/static/vendor/gaze/vision_bundle.mjs");
      const model = await FaceLandmarker.createFromOptions(
        await FilesetResolver.forVisionTasks("/static/vendor/gaze/wasm"),
        {
          baseOptions: {
            modelAssetPath: "/static/vendor/gaze/face_landmarker.task",
            delegate: "CPU",
          },
          runningMode: "VIDEO",
          numFaces: 2,
        },
      );
      if (this.closed) model.close();
      else this.landmarker = model;
    })().finally(() => {
      this.loading = null;
    });
    return this.loading;
  }
  frame() {
    this.automaticSample = null;
    const video = document.getElementById("camera-preview");
    if (!video.videoWidth || video.readyState < 2) return null;
    this.lastTime = Math.max(performance.now(), (this.lastTime || 0) + 1);
    const result = this.landmarker.detectForVideo(video, this.lastTime);
    this.automaticSample = automaticDirection(result);
    return gazeFeatures(result);
  }
  async calibrate() {
    const video = document.getElementById("camera-preview");
    if (
      !video.srcObject?.getVideoTracks().some((t) => t.readyState === "live") ||
      !video.videoWidth
    )
      throw Error("Enable your camera before calibrating gaze.");
    if (
      this.context.state().session.policy.require_fullscreen &&
      !document.fullscreenElement
    )
      await document.documentElement.requestFullscreen?.().catch(() => {});
    const epoch = ++this.epoch;
    this.model = null;
    this.status("Loading local gaze landmarks…");
    let loadingTimer;
    try {
      await Promise.race([
        this.initialize(),
        new Promise((_, reject) => {
          loadingTimer = setTimeout(
            () =>
              reject(
                Error(
                  "Gaze model loading timed out. Check connectivity and try again.",
                ),
              ),
            30000,
          );
        }),
      ]);
    } catch (error) {
      this.status(
        "Gaze model unavailable. No gaze observations will be recorded.",
      );
      throw error;
    } finally {
      clearTimeout(loadingTimer);
    }
    if (epoch !== this.epoch) {
      this.landmarker?.close();
      this.landmarker = null;
      return;
    }
    const width = innerWidth,
      height = innerHeight,
      dialog = document.getElementById("gaze-calibration"),
      target = document.getElementById("gaze-target"),
      help = document.getElementById("gaze-calibration-help");
    const training = [...Array(3)].flatMap((_, y) =>
      [0.1, 0.5, 0.9].map((x) => ({ x, y: [0.1, 0.5, 0.9][y] })),
    );
    const validation = [
      { x: 0.3, y: 0.3 },
      { x: 0.7, y: 0.3 },
      { x: 0.3, y: 0.7 },
      { x: 0.7, y: 0.7 },
      { x: 0.5, y: 0.5 },
    ];
    const points = [...training, ...validation],
      samples = [],
      heldout = [];
    let index = 0;
    dialog.showModal();
    this.calibrating = true;
    const position = () => {
      const p = points[index];
      target.style.left = `${p.x * width}px`;
      target.style.top = `${p.y * height}px`;
      target.textContent = `${index + 1} / ${points.length}`;
      help.style.top = p.y < 0.3 ? `${height - 120}px` : "12px";
      help.textContent = `${index < 9 ? "Calibration" : "Independent validation"}: look directly at the numbered point, then click it and keep looking until it moves. Keep your head steady.`;
    };
    position();
    await new Promise((resolve) => {
      const finish = () => {
        this.cancelCalibration = null;
        target.onclick = null;
        dialog.oncancel = null;
        document.getElementById("cancel-gaze").onclick = null;
        dialog.close();
        this.calibrating = false;
        resolve();
      };
      this.cancelCalibration = () => {
        this.epoch++;
        this.status("Calibration cancelled. Gaze estimates are off.");
        finish();
      };
      dialog.oncancel = (e) => {
        e.preventDefault();
        this.cancelCalibration();
      };
      document.getElementById("cancel-gaze").onclick = this.cancelCalibration;
      target.onclick = async () => {
        target.disabled = true;
        try {
          if (width !== innerWidth || height !== innerHeight)
            throw Error("Window size changed. Cancel and recalibrate.");
          const collected = [];
          for (let i = 0; i < 5; i++) {
            await new Promise((r) => setTimeout(r, 240));
            if (epoch !== this.epoch) return;
            const f = this.frame();
            if (f) collected.push({ f, ...points[index] });
          }
          if (collected.length < 4)
            throw Error(
              "Eyes could not be measured. Face the camera alone, improve lighting, then try this point again.",
            );
          (index < 9 ? samples : heldout).push(...collected);
          index++;
          if (index === 9) this.candidate = fitGaze(samples);
          if (index === points.length) {
            const check = validateGaze(this.candidate, heldout, width, height);
            if (!check.valid) {
              this.status(
                "Screen-position calibration failed. Automatic direction estimates remain enabled; adjust lighting and retry calibration if desired.",
              );
              finish();
              return;
            }
            this.model = this.candidate;
            this.candidate = null;
            this.width = width;
            this.height = height;
            this.track = video.srcObject.getVideoTracks()[0].id;
            this.validationError = check.error;
            this.awaySince = null;
            this.lastAlert = 0;
            this.status(
              `Gaze calibrated · validation median error ${Math.round(check.error * 100)}% of screen diagonal. Estimates are advisory.`,
            );
            finish();
          } else position();
        } catch (error) {
          help.textContent = error.message;
        } finally {
          target.disabled = false;
        }
      };
    });
  }
  tick() {
    if (!this.model && !this.calibrating) {
      this.tickAutomatic();
      return;
    }
    if (
      !this.model ||
      this.calibrating ||
      document.hidden ||
      !document.hasFocus()
    ) {
      this.awaySince = null;
      if (this.model && !this.calibrating)
        this.status(
          "Gaze estimates pause while this window is inactive.",
          "Gaze: paused",
        );
      return;
    }
    const state = this.context.state(),
      video = document.getElementById("camera-preview");
    if (state.session?.status !== "in_progress" || state.stopping) {
      this.awaySince = null;
      this.status(
        "Calibration is ready. Gaze observations start with the exam.",
        "Gaze: ready for exam",
      );
      return;
    }
    if (
      innerWidth !== this.width ||
      innerHeight !== this.height ||
      video.srcObject?.getVideoTracks()[0]?.id !== this.track
    ) {
      this.model = null;
      this.status(
        "Gaze calibration invalidated by a window/camera change. Recalibrate; no gaze observations are recorded.",
      );
      return;
    }
    try {
      const f = this.frame();
      if (!f) {
        this.awaySince = null;
        this.status(
          "Gaze unavailable: a clear single face and visible eyes are needed.",
          "Gaze: eyes not visible",
        );
        return;
      }
      if (
        Math.abs(f[4] - this.model.mean[4]) > 0.15 ||
        Math.abs(f[5] - this.model.mean[5]) > 0.15 ||
        Math.abs(f[6] / this.model.mean[6] - 1) > 0.25
      ) {
        this.model = null;
        this.status(
          "Camera distance or head position changed. Recalibrate gaze before further estimates.",
        );
        return;
      }
      const p = predictGaze(this.model, f);
      if (
        !Number.isFinite(p.x) ||
        !Number.isFinite(p.y) ||
        Math.abs(p.x) > 4 ||
        Math.abs(p.y) > 4
      ) {
        this.awaySince = null;
        return;
      }
      const away = p.x < -0.15 || p.x > 1.15 || p.y < -0.15 || p.y > 1.15;
      this.status(
        away
          ? "Gaze estimate is outside the calibrated screen area. Check your position; this is not a misconduct decision."
          : "Gaze estimate is within the calibrated screen area · advisory only.",
        away ? "Gaze: outside screen" : "Gaze: within screen",
      );
      if (!away) {
        this.awaySince = null;
        return;
      }
      this.awaySince ??= Date.now();
      if (
        Date.now() - this.awaySince >= 5000 &&
        Date.now() - this.lastAlert >= 30000
      ) {
        this.lastAlert = Date.now();
        this.context.event("gaze_outside_calibrated_area", {
          duration_ms: Math.min(86400000, Date.now() - this.awaySince),
          value: Math.round(this.validationError * 10000) / 10000,
        });
      }
    } catch {
      this.awaySince = null;
      this.status("Gaze inference unavailable. No observation recorded.");
    }
  }
  tickAutomatic() {
    const state = this.context.state();
    if (state.session?.policy?.enable_face_detection === false) {
      this.automaticAwaySince = null;
      this.status("Direction checks disabled by organizer.", "Gaze: disabled");
      return;
    }
    if (
      state.session?.status !== "in_progress" ||
      state.stopping ||
      this.closed
    ) {
      this.automaticAwaySince = null;
      return;
    }
    if (document.hidden || !document.hasFocus()) {
      this.automaticAwaySince = null;
      this.status(
        "Direction estimates pause while this window is inactive.",
        "Gaze: paused",
      );
      return;
    }
    const video = document.getElementById("camera-preview");
    if (
      !video.srcObject?.getVideoTracks().some((t) => t.readyState === "live") ||
      !video.videoWidth
    ) {
      this.automaticAwaySince = null;
      this.status(
        "Connect the camera for direction estimates.",
        "Gaze: camera needed",
      );
      return;
    }
    if (!this.landmarker) {
      if (this.automaticLoading || Date.now() < (this.automaticRetryAfter || 0))
        return;
      this.automaticLoading = true;
      this.status("Loading automatic direction checks…", "Gaze: loading");
      this.initialize()
        .catch(() => {
          if (this.closed) return;
          this.automaticRetryAfter = Date.now() + 30000;
          this.status(
            "Direction model unavailable. Retrying shortly.",
            "Gaze: unavailable",
          );
        })
        .finally(() => {
          this.automaticLoading = false;
        });
      return;
    }
    try {
      this.frame();
      const sample = this.automaticSample;
      if (!sample) {
        this.automaticAwaySince = null;
        this.status(
          "A clear single face and visible open eyes are needed.",
          "Gaze: eyes not visible",
        );
        return;
      }
      this.status(
        sample.away
          ? "Eye/head direction suggests looking away; not proof of screen position or misconduct."
          : "Automatic direction checks active; no calibration required.",
        sample.away ? "Looking-away estimate" : "Gaze: monitoring",
      );
      if (!sample.away) {
        this.automaticAwaySince = null;
        return;
      }
      this.automaticAwaySince ??= Date.now();
      if (
        Date.now() - this.automaticAwaySince >= 5000 &&
        Date.now() - (this.lastAutomaticAlert ?? -Infinity) >= 30000
      ) {
        this.lastAutomaticAlert = Date.now();
        this.context.event("looking_away_estimate", {
          duration_ms: Math.min(
            86400000,
            Math.round(Date.now() - this.automaticAwaySince),
          ),
        });
      }
    } catch {
      this.automaticAwaySince = null;
      this.status(
        "Direction estimate unavailable for this frame.",
        "Gaze: unavailable",
      );
    }
  }
  stop() {
    this.closed = true;
    this.epoch++;
    this.cancelCalibration?.();
    clearInterval(this.interval);
    this.model = null;
    this.candidate = null;
    this.landmarker?.close();
    this.landmarker = null;
  }
}
if (typeof module !== "undefined" && module.exports)
  module.exports = {
    gazeFeatures,
    fitGaze,
    predictGaze,
    validateGaze,
    GazeController,
    automaticDirection,
  };
