const { test, expect } = require("@playwright/test");
const { execFileSync } = require("node:child_process");
test.beforeEach(() =>
  execFileSync(".venv/bin/python", ["scripts/reset_e2e_limits.py"]),
);
test.afterEach(async ({ page }) => {
  if (!page.proctixDisposableAttempt) return;
  if (new URL(page.url()).origin !== "http://localhost:8765")
    throw Error("Refusing cleanup outside disposable test server");
  const response = await page.request.get("/api/auth/me");
  if (!response.ok()) return;
  const me = await response.json();
  if (me.organization?.slug !== "browser-test")
    throw Error("Refusing cleanup outside disposable tenant");
  const sid = page.proctixDisposableAttempt;
  const current = await page.request.get(`/api/sessions/${sid}`);
  if (!current.ok()) return;
  const attempt = await current.json();
  if (attempt.id !== sid || attempt.status !== "preflight") return;
  await page.request.post(`/api/sessions/${sid}/consent`, {
    headers: { Origin: "http://localhost:8765", "X-CSRF-Token": me.csrf_token },
    data: { accepted: false },
  });
});
async function open(page) {
  await page.goto("/");
  await page.getByLabel("Organization code").fill("browser-test");
  await page
    .getByLabel("Email", { exact: true })
    .first()
    .fill("student@example.test");
  await page
    .getByLabel("Password", { exact: true })
    .fill("Browser-testing-only-2026!");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  const created = page.waitForResponse(
    (r) =>
      r.url() === "http://localhost:8765/api/sessions" &&
      r.request().method() === "POST",
  );
  await page
    .locator("#exams article")
    .filter({
      has: page.getByRole("heading", {
        name: "E2E assigned-account",
        exact: true,
      }),
    })
    .getByRole("button", { name: "Open assessment" })
    .click();
  const attempt = await (await created).json();
  page.proctixDisposableAttempt = attempt.id;
  await page.locator("#consent-check").check();
  await page.locator("#consent").click();
}
async function details(page) {
  await page.locator("#enrollment-name").fill("Test student");
  await page.locator("#enrollment-reference").fill("student@example.test");
  await page.locator("#confirm-enrollment").click();
}
test("setup has separate pages, manually entered details, uploads and a final review", async ({
  page,
}) => {
  await open(page);
  await expect(page.locator("#enrollment-name")).toHaveValue("");
  await expect(page.locator("#enrollment-reference")).toHaveValue("");
  await expect(page.locator("#setup-step-2")).toBeHidden();
  await expect(page.locator("#setup-step-4")).toBeHidden();
  await details(page);
  await expect(page.locator("#enrollment-card")).toBeHidden();
  await expect(page.locator("#setup-step-2")).toBeVisible();
  const { cameraBox, displayBox } = await page.evaluate(() => ({
    cameraBox: document
      .getElementById("camera-device-card")
      .getBoundingClientRect()
      .toJSON(),
    displayBox: document
      .getElementById("display-device-card")
      .getBoundingClientRect()
      .toJSON(),
  }));
  expect(Math.abs(cameraBox.y - displayBox.y)).toBeLessThan(2);
  expect(displayBox.x).toBeGreaterThan(cameraBox.x + cameraBox.width);
  await expect(
    page.locator("#camera-device-preview #camera-frame"),
  ).toHaveCount(1);
  await page.evaluate(() => window.scrollTo({ top: 0, behavior: "instant" }));
  await page.screenshot({
    path: "docs/ui/device-cards-desktop.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  const { mobileCameraBox, mobileDisplayBox } = await page.evaluate(() => ({
    mobileCameraBox: document
      .getElementById("camera-device-card")
      .getBoundingClientRect()
      .toJSON(),
    mobileDisplayBox: document
      .getElementById("display-device-card")
      .getBoundingClientRect()
      .toJSON(),
  }));
  expect(mobileDisplayBox.y).toBeGreaterThan(
    mobileCameraBox.y + mobileCameraBox.height,
  );
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  const DeviceAxeBuilder = require("@axe-core/playwright").default;
  const deviceAccessibility = await new DeviceAxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
    .analyze();
  expect(
    deviceAccessibility.violations.map((v) => ({
      id: v.id,
      targets: v.nodes.map((n) => n.target),
    })),
  ).toEqual([]);
  await page.evaluate(() => window.scrollTo({ top: 0, behavior: "instant" }));
  await page.screenshot({
    path: "docs/ui/device-cards-mobile.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.locator("#devices-next").click();
  await expect(page.locator("#setup-step-3")).toBeVisible();
  await expect(page.locator("#setup-step-2")).toBeHidden();
  await expect(page.locator("#photos-next")).toBeDisabled();
  await expect(page.locator("#photo-ready-count")).toHaveText("0 / 2");
  await page.evaluate(() => window.scrollTo({ top: 0, behavior: "instant" }));
  await page.screenshot({
    path: "docs/ui/identity-photos-empty.png",
    fullPage: true,
  });
  await page.locator("#selfie").click();
  await page.locator("#confirm-selfie").click();
  const frame = await page.locator("#camera-preview").evaluate((v) => {
    const c = document.createElement("canvas");
    c.width = 320;
    c.height = 240;
    c.getContext("2d").drawImage(v, 0, 0, 320, 240);
    const ctx = c.getContext("2d");
    ctx.fillStyle = "white";
    ctx.fillRect(0, 200, 320, 40);
    ctx.fillStyle = "black";
    ctx.font = "24px Arial";
    ctx.fillText("Name: Test student", 10, 228);
    return c.toDataURL("image/jpeg").split(",")[1];
  });
  await page.locator("#id-file").setInputFiles({
    name: "test-id.jpg",
    mimeType: "image/jpeg",
    buffer: Buffer.from(frame, "base64"),
  });
  await expect(page.locator("#photo-retry")).toBeVisible();
  await expect(page.locator("#photo-retry-message")).toContainText(
    "Check 1 of 5",
  );
  await expect(page.locator("#photo-retry-message")).toContainText(
    "model is unavailable",
  );
  await expect(page.locator("#retry-id")).toBeEnabled();
  await expect(page.locator("#retry-selfie")).toBeEnabled();
  await expect(page.locator("#id-back-file")).toHaveCount(0);
  await expect(
    page.getByText("Optional document checks", { exact: true }),
  ).toHaveCount(0);
  await expect(page.locator("#comparison-title")).toHaveText(
    "Comparison unavailable",
  );
  await page.screenshot({
    path: "docs/ui/document-checks.png",
    fullPage: true,
  });
  await expect(page.locator("#photos-next")).toBeEnabled();
  await expect(page.locator("#photo-ready-count")).toHaveText("2 / 2");
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.evaluate(() => window.scrollTo({ top: 0, behavior: "instant" }));
  const AxeBuilder = require("@axe-core/playwright").default;
  const accessibility = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
    .analyze();
  expect(
    accessibility.violations.map((v) => ({
      id: v.id,
      nodes: v.nodes.map((n) => n.target),
    })),
  ).toEqual([]);
  await page.screenshot({
    path: "docs/ui/identity-photos-mobile.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.locator("#photos-next").click();
  await expect(page.locator("#setup-step-4")).toBeVisible();
  await expect(page.locator("#ready-title")).toHaveText(
    "Review before you begin",
  );
  expect(
    await page
      .locator("#readiness-list")
      .evaluate(
        (list) => getComputedStyle(list).gridTemplateColumns.split(" ").length,
      ),
  ).toBe(2);
  await expect(page.locator("#final-face-result")).toHaveText(
    "Comparison unavailable",
  );
  await expect(page.locator("#final-face-result")).toBeVisible();
  await expect(page.locator('#readiness-list [data-check="id"]')).toContainText(
    "Saved",
  );
  await expect(
    page.locator('#readiness-list [data-check="selfie"]'),
  ).toContainText("Saved");
  await expect(
    page.locator('#readiness-list [data-check="screen"]'),
  ).toContainText("Not required");
  await expect(
    page.locator('#readiness-list [data-check="screen"]'),
  ).not.toHaveClass(/done/);
  await expect(
    page.locator('#readiness-list [data-check="staff-review"]'),
  ).toContainText("Not required");
  await expect(
    page.locator('#readiness-list [data-check="details"]'),
  ).toContainText("Saved as entered");
  await expect(page.locator("#readiness-list")).not.toContainText(
    "Assigned institution account authorized",
  );
  await expect(page.locator("#readiness-list")).not.toContainText(
    "Registered details confirmed",
  );
  let simulatedScore = 0.8;
  await page.route("**/monitoring", async (route) => {
    const response = await route.fetch();
    const data = await response.json();
    data.latest.identity_comparison = {
      details: {
        status: "compared",
        model: "Synthetic test score",
        cosine_similarity: simulatedScore,
        review_threshold: 0.4,
        evidence_ids: data.identity_evidence_ids,
      },
    };
    await route.fulfill({ response, json: data });
  });
  await expect(page.locator("#final-face-result")).toHaveText(
    "Faces appear to match",
    { timeout: 10000 },
  );
  simulatedScore = -0.1;
  await expect(page.locator("#final-face-result")).toHaveText(
    "Faces appear different",
    { timeout: 10000 },
  );
  await page.unroute("**/monitoring");
  await expect(page.locator("#ready-description")).not.toContainText(
    "Automated face scores",
  );
  await expect(page.locator("#message")).toHaveText("", { timeout: 9000 });
  await expect(page.locator("#setup-step-3")).toBeHidden();
  await page.screenshot({
    path: "docs/ui/paged-setup-final.png",
    fullPage: true,
  });
  await page.reload();
  await page
    .locator("#sessions article")
    .first()
    .getByRole("button", { name: "Open session" })
    .click();
  await expect(page.locator("#enrollment-reference")).toHaveValue("");
  await expect(page.locator("#enrollment-card")).toBeVisible();
  await page.locator('[data-step="1"]').click();
  await page.locator("#decline").click();
});
test("unanswered native display picker times out and permits a successful retry", async ({
  page,
}) => {
  await open(page);
  await expect(page.locator("#demo-details")).toBeVisible();
  await page.locator("#demo-details").selectOption("sample");
  await expect(page.locator("#enrollment-name")).toHaveValue("Test student");
  await expect(page.locator("#enrollment-reference")).toHaveValue(
    "student@example.test",
  );
  await page.locator("#confirm-enrollment").click();
  await page.evaluate(() => {
    navigator.mediaDevices.getDisplayMedia = () => new Promise(() => {});
  });
  await page.clock.install();
  await page.locator("#screen").click();
  await expect(page.locator("#screen-progress")).toContainText(
    "Chrome or Edge",
  );
  await page.clock.fastForward(20001);
  await expect(page.locator("#screen")).toBeEnabled();
  await expect(page.locator("#screen-error")).toContainText(
    "No display was selected",
  );
  await page.evaluate(() => {
    navigator.mediaDevices.getDisplayMedia = async () => {
      const c = document.createElement("canvas");
      c.width = 640;
      c.height = 480;
      const stream = c.captureStream(10);
      const track = stream.getVideoTracks()[0];
      track.getSettings = () => ({ displaySurface: "monitor" });
      window.testPaint = setInterval(() => {
        c.getContext("2d").fillRect(0, 0, 640, 480);
      }, 50);
      return stream;
    };
  });
  await page.clock.resume();
  await page.locator("#screen").click();
  await expect(page.locator("#screen-progress")).toContainText(
    "Entire display connected",
  );
  await expect(page.locator("#screen")).toBeEnabled();
});

test("gaze runtime loads locally and cannot calibrate an unreadable synthetic face", async ({
  page,
}) => {
  await open(page);
  await details(page);
  await page.locator("#camera").click();
  await page.locator("#camera-monitoring-settings > summary").click();
  await page.locator("#calibrate-gaze").click();
  await expect(page.locator("#gaze-calibration")).toBeVisible({
    timeout: 30000,
  });
  await expect(page.locator("#gaze-target")).toContainText("1 / 14");
  await page.screenshot({ path: "docs/ui/gaze-calibration.png" });
  await page.locator("#gaze-target").click();
  await expect(page.locator("#gaze-calibration-help")).toContainText(
    "Eyes could not be measured",
    { timeout: 10000 },
  );
  await page.locator("#cancel-gaze").click();
  await expect(page.locator("#gaze-status")).toContainText("cancelled");
  await expect(page.locator("#gaze-calibration")).toBeHidden();
  await page.locator('[data-step="1"]').click();
  await page.locator("#decline").click();
});

test("local landmark model extracts eye features from a public research example", async ({
  page,
}) => {
  const fs = require("node:fs");
  const source = "/private/tmp/proctix-model-sanity/img2.jpeg";
  test.skip(
    !fs.existsSync(source),
    "Optional public model sanity image is not installed",
  );
  await page.goto("/");
  await page.waitForFunction(() => typeof gazeFeatures === "function");
  const result = await page.evaluate(async (data) => {
    const { FaceLandmarker, FilesetResolver } =
      await import("/static/vendor/gaze/vision_bundle.mjs");
    const model = await FaceLandmarker.createFromOptions(
      await FilesetResolver.forVisionTasks("/static/vendor/gaze/wasm"),
      {
        baseOptions: {
          modelAssetPath: "/static/vendor/gaze/face_landmarker.task",
          delegate: "CPU",
        },
        runningMode: "IMAGE",
        numFaces: 1,
      },
    );
    try {
      const image = new Image();
      image.src = "data:image/jpeg;base64," + data;
      await image.decode();
      const result = model.detect(image);
      const f = gazeFeatures(result);
      return {
        faces: result.faceLandmarks.length,
        landmarks: result.faceLandmarks[0]?.length,
        features: f?.length,
        finite: f?.every(Number.isFinite),
      };
    } finally {
      model.close();
    }
  }, fs.readFileSync(source).toString("base64"));
  expect(result.faces).toBe(1);
  expect(result.landmarks).toBe(478);
  expect(result.features).toBe(8);
  expect(result.finite).toBe(true);
});

test("correcting a failed action clears its notification and temporary errors expire", async ({
  page,
}) => {
  await open(page);
  await page.route(
    "**/api/sessions/*/enrollment",
    (route) =>
      route.fulfill({
        status: 400,
        contentType: "application/json",
        body: JSON.stringify({ detail: "Check your enrollment details." }),
      }),
    { times: 1 },
  );
  await details(page);
  await expect(page.locator("#message")).toHaveText(
    "Check your enrollment details.",
  );
  await page.locator("#confirm-enrollment").click();
  await expect(page.locator("#setup-step-2")).toBeVisible();
  await expect(page.locator("#message")).toHaveText("");
  await page.locator('[data-step="5"]').click();
  await page.route(
    "**/api/sessions/*/enrollment",
    (route) =>
      route.fulfill({
        status: 400,
        contentType: "application/json",
        body: JSON.stringify({ detail: "Temporary test error." }),
      }),
    { times: 1 },
  );
  await page.clock.install();
  await page.locator("#confirm-enrollment").click();
  await expect(page.locator("#message")).toHaveText("Temporary test error.");
  await page.clock.fastForward(8100);
  await expect(page.locator("#message")).toHaveText("");
});

test("quiet audio is visible, recorded only during the exam, and paused analysis can resume", async ({
  page,
}) => {
  await open(page);
  await details(page);
  await expect(page.locator("#audio-analysis-status")).toContainText(
    "starts after Begin",
  );
  expect(await page.evaluate(() => !!state.audioContext)).toBe(false);
  await page.locator("#devices-next").click();
  await page.locator("#selfie").click();
  await page.locator("#confirm-selfie").click();
  const frame = await page.locator("#camera-preview").evaluate((v) => {
    const c = document.createElement("canvas");
    c.width = 320;
    c.height = 240;
    c.getContext("2d").drawImage(v, 0, 0, 320, 240);
    const ctx = c.getContext("2d");
    ctx.fillStyle = "white";
    ctx.fillRect(0, 200, 320, 40);
    ctx.fillStyle = "black";
    ctx.font = "24px Arial";
    ctx.fillText("Name: Test student", 10, 228);
    return c.toDataURL("image/jpeg").split(",")[1];
  });
  await page.locator("#id-file").setInputFiles({
    name: "synthetic-audio-test-id.jpg",
    mimeType: "image/jpeg",
    buffer: Buffer.from(frame, "base64"),
  });
  await expect(page.locator("#photos-next")).toBeEnabled();
  await page.locator("#photos-next").click();
  await page.locator("#start").click();
  await expect(page.locator("#questions")).toBeVisible();
  await expect
    .poll(() => page.evaluate(() => !!state.gaze?.landmarker), {
      timeout: 30000,
    })
    .toBe(true);
  await expect(page.locator("#gaze-brief")).not.toContainText(
    "calibration needed",
  );
  await page.locator("#camera-monitoring-settings > summary").click();
  await expect(page.locator("#audio-analysis-status")).toBeVisible();
  await page.evaluate(async () => {
    await state.audioContext?.close();
    const generator = new AudioContext();
    await generator.resume();
    const tone = generator.createOscillator();
    tone.frequency.value = 700;
    const gain = generator.createGain();
    gain.gain.value = 0;
    const destination = generator.createMediaStreamDestination();
    tone.connect(gain);
    gain.connect(destination);
    tone.start();
    window.audioTestSignal = { generator, tone, gain };
    startAudioMeter(destination.stream);
  });
  await expect(page.locator("#audio-analysis-status")).toContainText(
    "monitoring active",
    { timeout: 10000 },
  );
  await page.evaluate(() => state.audioContext.suspend());
  await expect(page.locator("#resume-audio")).toBeVisible();
  await page.locator("#resume-audio").click();
  await expect(page.locator("#resume-audio")).toBeHidden();
  const received = page.waitForResponse(
    (r) =>
      r.url().endsWith("/events") &&
      r.request().postDataJSON()?.kind === "audio_level_change",
  );
  await page.evaluate(() => {
    window.audioTestSignal.gain.gain.value = 0.006;
  });
  const audioResponse = await received;
  expect(audioResponse.ok(), await audioResponse.text()).toBe(true);
  await page.evaluate(() => {
    window.audioTestSignal.gain.gain.value = 0;
  });
  await expect(page.locator("#live-warning")).toContainText(
    "Sustained sound detected",
  );
  await expect(page.locator("#recent-observations")).toContainText(
    "Sustained sound detected",
  );
  await expect(page.locator("#sound-value")).toContainText("dBFS");
  await expect(page.locator("#live-warning")).not.toContainText(
    "Sustained sound detected",
    { timeout: 10000 },
  );
  await page.evaluate(async () => {
    window.audioTestSignal.tone.stop();
    await window.audioTestSignal.generator.close();
  });
  page.once("dialog", (dialog) => dialog.accept());
  await page.locator("#submit").click();
  await expect(page.locator("#session-state")).toContainText("submitted");
  await expect(page.locator("#resume-audio")).toBeHidden();
  await expect(page.locator("#completion-receipt")).toBeVisible();
  await expect(page.locator(".time-card")).toBeHidden();
  await expect(page.locator(".session-aside")).toBeHidden();
  await expect(page.locator("#questions")).toBeHidden();
  await expect(page.locator("#control-note")).toBeHidden();
});

test("fullscreen exit allows five seconds, recovery cancels, and expiry submits final answers with retry", async ({
  page,
}) => {
  await open(page);
  await details(page);
  await page.locator("#devices-next").click();
  await page.locator("#selfie").click();
  await page.locator("#confirm-selfie").click();
  const frame = await page.locator("#camera-preview").evaluate((v) => {
    const c = document.createElement("canvas");
    c.width = 320;
    c.height = 240;
    c.getContext("2d").drawImage(v, 0, 0, 320, 240);
    const ctx = c.getContext("2d");
    ctx.fillStyle = "white";
    ctx.fillRect(0, 200, 320, 40);
    ctx.fillStyle = "black";
    ctx.font = "24px Arial";
    ctx.fillText("Name: Test student", 10, 228);
    return c.toDataURL("image/jpeg").split(",")[1];
  });
  await page.locator("#id-file").setInputFiles({
    name: "synthetic-fullscreen-id.jpg",
    mimeType: "image/jpeg",
    buffer: Buffer.from(frame, "base64"),
  });
  await expect(page.locator("#photos-next")).toBeEnabled();
  await page.locator("#photos-next").click();
  await page.locator("#start").click();
  await expect(page.locator("#questions")).toBeVisible();
  await page.clock.install();
  await page.evaluate(() => {
    window.testFullscreen = true;
    Object.defineProperty(document, "fullscreenElement", {
      configurable: true,
      get: () => (window.testFullscreen ? document.documentElement : null),
    });
    document.documentElement.requestFullscreen = async () => {
      window.testFullscreen = true;
      document.dispatchEvent(new Event("fullscreenchange"));
    };
    state.session.policy.require_fullscreen = true;
    window.testFullscreen = false;
    document.dispatchEvent(new Event("fullscreenchange"));
  });
  await expect(page.locator("#fullscreen-help")).toContainText("5 seconds");
  await page.evaluate(() => {
    for (let i = 0; i < 10; i++) window.dispatchEvent(new Event("resize"));
  });
  await expect
    .poll(async () =>
      page.evaluate(async () => {
        const data = await (
          await fetch(`/api/sessions/${state.session.id}/monitoring`)
        ).json();
        return data.recent.filter((o) =>
          [
            "window_geometry_change",
            "fullscreen_exit",
            "fullscreen_recovery_timeout",
          ].includes(o.kind),
        ).length;
      }),
    )
    .toBe(1);
  await expect(page.locator("#recover-fullscreen")).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(page.locator("#recover-fullscreen")).toBeFocused();
  expect(
    await page
      .locator("#fullscreen-countdown")
      .evaluate((el) => parseFloat(getComputedStyle(el).fontSize)),
  ).toBeGreaterThanOrEqual(80);
  expect(
    (await page.locator("#recover-fullscreen").boundingBox()).height,
  ).toBeGreaterThanOrEqual(64);
  await page.screenshot({ path: "docs/ui/fullscreen-recovery-large.png" });
  await page.clock.fastForward(4000);
  await expect(page.locator("#session-state")).not.toContainText("submitted");
  await page.locator("#recover-fullscreen").click();
  await expect(page.locator("#fullscreen-recovery")).toBeHidden();
  await page.clock.fastForward(2000);
  await expect(page.locator("#session-state")).not.toContainText("submitted");
  // A resize while the browser still reports fullscreen needs explicit recovery.
  await page.evaluate(() => window.dispatchEvent(new Event("resize")));
  await expect(page.locator("#fullscreen-recovery")).toBeVisible();
  await page.clock.fastForward(2000);
  await page.evaluate(() => window.dispatchEvent(new Event("resize")));
  await expect(page.locator("#fullscreen-countdown")).toHaveText("3");
  await page.locator("#recover-fullscreen").click();
  await expect(page.locator("#fullscreen-recovery")).toBeHidden();
  await expect
    .poll(async () =>
      page.evaluate(async () => {
        const data = await (
          await fetch(`/api/sessions/${state.session.id}/monitoring`)
        ).json();
        return data.recent.filter(
          (o) =>
            ["window_geometry_change", "fullscreen_exit"].includes(o.kind) &&
            o.details.recovery_status === "restored",
        ).length;
      }),
    )
    .toBe(2);
  await page
    .getByLabel("Explain your reasoning.")
    .fill("Answer preserved at fullscreen timeout");
  await page.route(
    "**/submit?reason=fullscreen_timeout",
    (route) =>
      route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({ detail: "Temporary test connection failure" }),
      }),
    { times: 1 },
  );
  await page.evaluate(() => {
    window.testFullscreen = false;
    document.dispatchEvent(new Event("fullscreenchange"));
  });
  await page.clock.fastForward(5100);
  await expect(page.locator("#fullscreen-help")).toContainText(
    "retrying automatically",
  );
  await page.clock.fastForward(3100);
  await expect(page.locator("#session-state")).toContainText("submitted");
  await expect(page.locator("#fullscreen-recovery")).toBeHidden();
  const saved = await page.evaluate(
    async () => await (await fetch(`/api/sessions/${state.session.id}`)).json(),
  );
  expect(saved.answers.q1).toBe("Answer preserved at fullscreen timeout");
  expect(saved.submission_reason).toBe("fullscreen_timeout");
  await expect(page.locator("#completion-receipt")).toBeVisible();
  await expect(page.locator("#completion-message")).toContainText(
    "fullscreen was not restored",
  );
  await expect(page.locator(".time-card")).toBeHidden();
  await expect(page.locator(".session-aside")).toBeHidden();
  await expect(page.locator("#questions")).toBeHidden();
  await page.locator("#completion-responses > summary").click();
  await expect(page.locator("#completion-answers")).toContainText(
    "Answer preserved at fullscreen timeout",
  );
  await page.screenshot({
    path: "docs/ui/submitted-receipt.png",
    fullPage: true,
  });
});

test("start again creates a fresh setup row with empty photos and zero checks", async ({
  page,
}) => {
  await open(page);
  await details(page);
  await page.locator("#devices-next").click();
  await page.locator("#selfie").click();
  await page.locator("#confirm-selfie").click();
  const oldId = page.proctixDisposableAttempt;
  const created = page.waitForResponse(
    (r) =>
      r.url() === "http://localhost:8765/api/sessions" &&
      r.request().method() === "POST",
  );
  await page.locator("#restart-setup").click();
  const fresh = await (await created).json();
  page.proctixDisposableAttempt = fresh.id;
  expect(fresh.id).not.toBe(oldId);
  expect(fresh.verification_attempts).toBe(0);
  await expect(page.locator("#setup-step-1")).toBeVisible();
  await expect(page.locator("#consent-check")).not.toBeChecked();
  const history = await (
    await page.request.get(`/api/sessions/${oldId}`)
  ).json();
  expect(history.status).toBe("cancelled");
  expect(history.identity_evidence.selfie).toBe(true);
  const current = await (
    await page.request.get(`/api/sessions/${fresh.id}`)
  ).json();
  expect(current.identity_evidence).toEqual({ id: false, selfie: false });
  await page.locator("#consent-check").check();
  await page.locator("#consent").click();
  await expect(page.locator("#enrollment-name")).toHaveValue("");
  await details(page);
  await page.locator("#devices-next").click();
  await expect(page.locator("#selfie-preview-panel")).toBeHidden();
  await expect(page.locator("#id-saved-preview")).toBeHidden();
  await expect(page.locator("#comparison-title")).toHaveText(
    "Add your ID and selfie",
  );
});

test("different-face estimates stay on photos with retries and an account-verification alternative", async ({
  page,
}) => {
  await open(page);
  await details(page);
  await page.locator("#devices-next").click();
  await page.locator("#selfie").click();
  await page.locator("#confirm-selfie").click();
  let score = 0.1;
  const comparison = (details) => ({
    ...details,
    status: "compared",
    model: "Synthetic browser test",
    cosine_similarity: score,
    review_threshold: 0.4,
  });
  await page.route("**/verification", async (route) => {
    const response = await route.fetch();
    const data = await response.json();
    await route.fulfill({ response, json: comparison(data) });
  });
  await page.route("**/monitoring", async (route) => {
    const response = await route.fetch();
    const data = await response.json();
    if (data.latest.identity_comparison)
      data.latest.identity_comparison.details = comparison(
        data.latest.identity_comparison.details,
      );
    await route.fulfill({ response, json: data });
  });
  const frame = await page.locator("#camera-preview").evaluate((v) => {
    const c = document.createElement("canvas");
    c.width = 320;
    c.height = 240;
    c.getContext("2d").drawImage(v, 0, 0, 320, 240);
    const ctx = c.getContext("2d");
    ctx.fillStyle = "white";
    ctx.fillRect(0, 200, 320, 40);
    ctx.fillStyle = "black";
    ctx.font = "24px Arial";
    ctx.fillText("Name: Test student", 10, 228);
    return c.toDataURL("image/jpeg").split(",")[1];
  });
  await page.locator("#id-file").setInputFiles({
    name: "synthetic-retry-id.jpg",
    mimeType: "image/jpeg",
    buffer: Buffer.from(frame, "base64"),
  });
  await expect(page.locator("#photo-retry-message")).toContainText(
    "4 checks remaining",
  );
  await expect(page.locator("#setup-step-3")).toBeVisible();
  await expect(page.locator("#photos-next")).toHaveText(
    "Use account verification instead",
  );
  await page.locator("#verify").click();
  await expect(page.locator("#photo-retry-message")).toContainText(
    "3 checks remaining",
  );
  await expect(page.locator("#setup-step-3")).toBeVisible();
  await page.locator("#photos-next").click();
  await expect(page.locator("#setup-step-4")).toBeVisible();
  await expect(page.locator("#final-face-result")).toHaveText(
    "Faces appear different",
  );
  await page
    .getByRole("button", { name: "Review or replace photos", exact: true })
    .click();
  score = 0.8;
  await page.locator("#verify").click();
  await expect(page.locator("#setup-step-4")).toBeVisible();
  await expect(page.locator("#final-face-result")).toHaveText(
    "Faces appear to match",
  );
});

test("an ID without the registered student name is rejected", async ({
  page,
}) => {
  await open(page);
  await details(page);
  await page.locator("#devices-next").click();
  const frame = await page.locator("#camera-preview").evaluate((video) => {
    const canvas = document.createElement("canvas");
    canvas.width = 320;
    canvas.height = 240;
    canvas.getContext("2d").drawImage(video, 0, 0, 320, 240);
    return canvas.toDataURL("image/jpeg").split(",")[1];
  });
  await page.locator("#id-file").setInputFiles({
    name: "portrait-without-name.jpg",
    mimeType: "image/jpeg",
    buffer: Buffer.from(frame, "base64"),
  });
  await expect(page.locator("#id-upload-error")).toContainText(
    "No readable English name text",
  );
  await expect(page.locator("#message")).not.toContainText(
    "Identity document uploaded.",
  );
  await expect(page.locator("#photos-next")).toBeDisabled();
});

test("student details accept arbitrary values without matching the account", async ({
  page,
}) => {
  await open(page);
  await page.locator("#enrollment-name").fill("Any name ! 123");
  await page.locator("#enrollment-reference").fill("any reference / xyz");
  await page.locator("#confirm-enrollment").click();
  await expect(page.locator("#setup-step-2")).toBeVisible();
  const saved = await page.evaluate(async () =>
    (await fetch(`/api/sessions/${state.session.id}`)).json(),
  );
  expect(saved.submitted_details).toEqual({
    name: "Any name ! 123",
    reference: "any reference / xyz",
  });
  expect(saved.enrollment_confirmed).toBe(true);
});

test("ID checks the entered name and explains which name OCR missed", async ({
  page,
}) => {
  await open(page);
  await page.locator("#enrollment-name").fill("Alex Candidate");
  await page.locator("#enrollment-reference").fill("any reference");
  await page.locator("#confirm-enrollment").click();
  await page.locator("#devices-next").click();
  for (const name of ["Alex Candidate", "Someone Else"]) {
    const frame = await page.evaluate((name) => {
      const canvas = document.createElement("canvas");
      canvas.width = 640;
      canvas.height = 400;
      const ctx = canvas.getContext("2d");
      ctx.fillStyle = "white";
      ctx.fillRect(0, 0, 640, 400);
      ctx.fillStyle = "black";
      ctx.font = "32px Arial";
      ctx.fillText(`Name: ${name}`, 30, 100);
      return canvas.toDataURL("image/jpeg").split(",")[1];
    }, name);
    await page.locator("#id-file").setInputFiles({
      name: "id.jpg",
      mimeType: "image/jpeg",
      buffer: Buffer.from(frame, "base64"),
    });
    if (name === "Alex Candidate") {
      await expect(page.locator("#id-upload-status")).toContainText(
        "Saved for this attempt",
      );
      await expect(page.locator("#id-upload-error")).toBeEmpty();
    } else {
      await expect(page.locator("#id-upload-error")).toContainText(
        'Name checked: "Alex Candidate" (from Your details).',
      );
      await expect(page.locator("#id-upload-error")).toContainText(
        "Text was detected",
      );
    }
  }
});

test("final review distinguishes skipped, missing and not-required steps", async ({
  page,
}) => {
  await open(page);
  await page.locator("#confirm-enrollment").click();
  await page.locator('[data-step="4"]').click();
  const row = (key) => page.locator(`#readiness-list [data-check="${key}"]`);
  await expect(page.locator("#readiness-list")).toBeVisible();
  await expect(row("details")).toContainText("Skipped");
  await expect(page.locator("#check-approval")).toBeHidden();
  await expect(row("details")).not.toHaveClass(/done/);
  await expect(row("notice")).toContainText("Accepted");
  await expect(row("notice")).toHaveClass(/done/);
  await expect(row("id")).toContainText("Not uploaded");
  await expect(row("selfie")).toContainText("Not uploaded");
  for (const key of ["camera", "audio", "screen", "staff-review"]) {
    await expect(row(key)).toContainText(/not required/i);
    await expect(row(key)).not.toHaveClass(/done/);
    await expect(row(key).locator(".check-indicator")).not.toHaveText("✓");
  }
  await page.evaluate(() => {
    state.session.policy.require_camera = true;
    renderSetup();
  });
  await expect(row("camera")).toContainText("Not connected");
  await expect(row("camera")).not.toHaveClass(/done/);
  await expect(page.locator("#start")).toBeDisabled();
  await page.evaluate(() => window.scrollTo({ top: 0, behavior: "instant" }));
  await page.screenshot({
    path: "docs/ui/final-review-statuses.png",
    fullPage: true,
  });
});

test("device cards report only actual required connections", async ({
  page,
}) => {
  await open(page);
  await details(page);
  await page.evaluate(() => {
    state.session.policy.require_camera = true;
    state.session.policy.require_audio = true;
    state.session.policy.require_screen = true;
    renderSetup();
  });
  await expect(page.locator("#device-ready-count")).toHaveText("0 / 2");
  await expect(page.locator("#devices-next")).toBeDisabled();
  await page.locator("#camera").click();
  await expect(page.locator("#camera-status")).toContainText(
    "Camera & microphone active",
  );
  await expect(page.locator("#device-ready-count")).toHaveText("1 / 2");
  await expect(page.locator("#devices-next")).toBeDisabled();
  await page.evaluate(() => {
    navigator.mediaDevices.getDisplayMedia = async () => {
      const canvas = document.createElement("canvas");
      canvas.width = 640;
      canvas.height = 360;
      canvas.getContext("2d").fillRect(0, 0, 640, 360);
      const stream = canvas.captureStream(5);
      stream.getVideoTracks()[0].getSettings = () => ({
        displaySurface: "monitor",
      });
      return stream;
    };
  });
  await page.locator("#screen").click();
  await expect(page.locator("#screen-status")).toHaveText("Display shared");
  await expect(page.locator("#device-ready-count")).toHaveText("2 / 2");
  await expect(page.locator("#display-device-preview video")).toBeVisible();
  await expect(page.locator("#devices-next")).toBeEnabled();
  await page.evaluate(() => window.scrollTo({ top: 0, behavior: "instant" }));
  await page.screenshot({
    path: "docs/ui/device-cards-connected.png",
    fullPage: true,
  });
});
