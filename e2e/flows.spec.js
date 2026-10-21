const { test, expect } = require("@playwright/test");
const { execFileSync } = require("node:child_process");
test.beforeEach(() => {
  execFileSync(".venv/bin/python", ["scripts/reset_e2e_limits.py"]);
});
async function login(page, role = "student") {
  await page.goto("/");
  await page.getByLabel("Organization code").fill("browser-test");
  await page
    .getByLabel("Email", { exact: true })
    .first()
    .fill(role + "@example.test");
  await page
    .getByLabel("Password", { exact: true })
    .fill("Browser-testing-only-2026!");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.locator("#dashboard")).toBeVisible();
}
async function request(page, url, method = "GET", body) {
  return page.evaluate(
    async ({ url, method, body }) => {
      const me = await (await fetch("/api/auth/me")).json();
      const r = await fetch(url, {
        method,
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": me.csrf_token,
        },
        body: body === undefined ? undefined : JSON.stringify(body),
      });
      const data = r.status === 204 ? null : await r.json();
      if (!r.ok) throw new Error(JSON.stringify(data));
      return data;
    },
    { url, method, body },
  );
}
async function prepare(page, name = "E2E assessment") {
  await page
    .locator("#exams article")
    .filter({ has: page.getByRole("heading", { name, exact: true }) })
    .getByRole("button", { name: "Open assessment" })
    .click();
  await page.locator("#consent-check").check();
  await page
    .getByRole("button", { name: "Accept & continue", exact: true })
    .click();
  await page.locator("#enrollment-name").fill("Test student");
  await page.locator("#enrollment-reference").fill("student@example.test");
  await page.locator("#confirm-enrollment").click();
  await page
    .getByRole("button", { name: "Enable camera and microphone", exact: true })
    .click();

  await page.locator("#devices-next").click();
  await page
    .getByRole("button", { name: "Capture camera selfie", exact: true })
    .click();
  await expect(page.locator("#selfie-preview-panel")).toBeVisible();
  await page
    .getByRole("button", { name: "Use this selfie", exact: true })
    .click();
  await expect(page.locator("#selfie-upload-status")).toHaveText(
    "Selfie uploaded successfully",
  );
  // Use a frame from the browser's synthetic test camera as the test ID image.
  const frame = await page.locator("#camera-preview").evaluate((video) => {
    const c = document.createElement("canvas");
    c.width = 320;
    c.height = 240;
    c.getContext("2d").drawImage(video, 0, 0, 320, 240);
    const ctx = c.getContext("2d");
    ctx.fillStyle = "white";
    ctx.fillRect(0, 200, 320, 40);
    ctx.fillStyle = "black";
    ctx.font = "24px Arial";
    ctx.fillText("Name: Test student", 10, 228);
    return c.toDataURL("image/jpeg").split(",")[1];
  });
  await page.locator("#id-file").setInputFiles({
    name: "synthetic-test-id.jpg",
    mimeType: "image/jpeg",
    buffer: Buffer.from(frame, "base64"),
  });

  await expect(page.locator("#message")).toHaveText(
    "Identity document uploaded. Registered student name found.",
  );
  await page
    .getByRole("button", { name: "Request identity review", exact: true })
    .click();
  await expect(page.locator("#message")).toContainText(
    "Identity review requested",
  );
}
async function pendingSession(adminPage, examName) {
  const exams = await request(adminPage, "/api/exams");
  const exam = exams.find((e) => e.name === examName);
  const sessions = await request(adminPage, "/api/sessions?exam_id=" + exam.id);
  return sessions.find(
    (s) =>
      !["submitted", "expired", "terminated", "cancelled"].includes(s.status),
  );
}
async function approveInUI(adminPage, sid) {
  await adminPage.getByRole("button", { name: "Refresh", exact: true }).click();
  await adminPage
    .locator(`#sessions article[data-session-id="${sid}"]`)
    .getByRole("button", { name: "Review session" })
    .click();
  await adminPage
    .getByText("Identity review decision", { exact: true })
    .click();
  await adminPage
    .locator("#review-reason")
    .fill(
      "Synthetic identity evidence checked against the test enrollment record.",
    );
  await adminPage
    .getByRole("button", { name: "Approve identity", exact: true })
    .click();
  await expect(adminPage.locator("#review-state")).toContainText("approved");
}

test.afterEach(async ({ browser }) => {
  const context = await browser.newContext();
  try {
    const login = await context.request.post(
      "http://localhost:8765/api/auth/login",
      {
        headers: { Origin: "http://localhost:8765" },
        data: {
          institution: "browser-test",
          email: "admin@example.test",
          password: "Browser-testing-only-2026!",
        },
      },
    );
    const credentials = await login.json();
    expect(
      login.ok(),
      "Test cleanup must authenticate; fixture limits reset between tests",
    ).toBeTruthy();
    const response = await context.request.get(
      "http://localhost:8765/api/sessions",
    );
    for (const session of await response.json())
      if (
        !["submitted", "expired", "terminated", "cancelled"].includes(
          session.status,
        )
      ) {
        await context.request.post(
          `http://localhost:8765/api/sessions/${session.id}/terminate`,
          {
            headers: {
              Origin: "http://localhost:8765",
              "X-CSRF-Token": credentials.csrf_token,
            },
            data: {
              decision: "rejected",
              reason: "Generated-data browser test cleanup after the scenario.",
            },
          },
        );
      }
  } finally {
    await context.close();
  }
});

test("identity review, answer autosave, reload recovery, and final submission", async ({
  browser,
  page,
}) => {
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await login(page);
  await prepare(page);
  const staff = await browser.newContext();
  const adminPage = await staff.newPage();
  await login(adminPage, "admin");
  const session = await pendingSession(adminPage, "E2E assessment");
  await approveInUI(adminPage, session.id);
  await page.getByRole("button", { name: "Begin assessment" }).click();
  await expect(page.locator("#questions")).toBeVisible();
  await expect(
    page.locator(".session-aside #assessment-observations"),
  ).toBeVisible();
  await expect(page.locator(".preview-card #face-live-status")).toBeVisible();
  await page.route("**/monitoring", async (route) => {
    const response = await route.fetch();
    const data = await response.json();
    data.latest.face_continuity_sample = {
      received_at: Date.now() / 1000,
      details: { status: "compared", below_reference_threshold: true },
    };
    data.recent.unshift({
      kind: "possible_person_change",
      name: "Possible person change — review needed",
      received_at: Date.now() / 1000,
    });
    data.observation_count += 1;
    await route.fulfill({ response, json: data });
  });
  await expect(page.locator("#face-continuity-status")).toContainText(
    "similarity is low",
    { timeout: 10000 },
  );
  await expect(page.locator("#recent-observations")).toContainText(
    "Possible person change",
  );
  await page.unroute("**/monitoring");

  await page
    .getByLabel("Explain your reasoning.")
    .fill("A complete response saved by the browser test.");
  await expect(page.locator("#save-state")).toContainText("Saved to server");
  await page.context().setOffline(true);
  await page
    .getByLabel("Explain your reasoning.")
    .fill("Response recovered after a network interruption.");
  await expect(page.locator("#save-state")).toContainText("encrypted locally");
  await page.context().setOffline(false);
  await expect(page.locator("#save-state")).toContainText("Saved to server");
  await page.reload();
  await expect(page.locator("#dashboard")).toBeVisible();
  await page
    .locator(`#sessions article[data-session-id="${session.id}"]`)
    .getByRole("button", { name: "Open session" })
    .click();
  await expect(page.getByLabel("Explain your reasoning.")).toHaveValue(
    "Response recovered after a network interruption.",
  );
  await page.evaluate(() => state.experience.checkFullscreen());
  if (await page.locator("#fullscreen-recovery").isVisible()) {
    await page.locator("#recover-fullscreen").click();
    await expect(page.locator("#fullscreen-recovery")).toBeHidden();
  }
  await expect(
    page.getByRole("button", {
      name: "Re-enable camera and microphone",
      exact: true,
    }),
  ).toBeVisible();
  await page
    .getByRole("button", {
      name: "Re-enable camera and microphone",
      exact: true,
    })
    .click();
  await page.evaluate(() => {
    document.getElementById("time-warning").hidden = false;
    document.getElementById("time-warning").textContent =
      "Five minutes or less remain.";
    document.getElementById("fullscreen-recovery").hidden = false;
    document.getElementById("fullscreen-help").textContent =
      "Return to fullscreen in 10 seconds.";
  });
  page.once("dialog", (d) => d.accept());
  await page
    .getByRole("button", { name: "Save answers and submit", exact: true })
    .click();
  await expect(page.locator("#session-state")).toContainText("submitted");
  await expect(page.locator("#time-warning")).toBeHidden();
  await expect(page.locator("#fullscreen-recovery")).toBeHidden();
  await expect(page.locator("#assessment-observations")).toBeHidden();
  expect(errors).toEqual([]);
  await staff.close();
});

test("mobile pairing, real synthetic camera uploads, signaling, and revocation", async ({
  browser,
  page,
}) => {
  await login(page);
  await prepare(page, "E2E mobile");
  await page
    .getByRole("button", { name: "Pair mobile camera", exact: true })
    .click();
  await expect(page.locator("#pair-link")).toHaveAttribute("href", /mobile#/);
  const link = await page.locator("#pair-link").getAttribute("href");
  const phoneContext = await browser.newContext({ permissions: ["camera"] });
  const phone = await phoneContext.newPage();
  await phone.goto(link);
  await expect(phone.locator("#mobile-code")).toContainText("Enter this code");
  const code = (await phone.locator("#mobile-code").textContent()).match(
    /\d{6}/,
  )[0];
  await page.locator("#pair-code").fill(code);
  await page
    .getByRole("button", { name: "Confirm device", exact: true })
    .click();
  await expect(page.locator("#message")).toContainText("Phone confirmed");
  await phone.locator("#mobile-consent").check();
  await expect(phone.locator("#phone-start")).toBeEnabled({ timeout: 10000 });
  await phone.locator("#phone-start").click();
  await expect(phone.locator("#mobile-status")).toContainText(
    "No photos or recording",
  );
  const staff = await browser.newContext();
  const adminPage = await staff.newPage();
  await login(adminPage, "admin");
  const session = await pendingSession(adminPage, "E2E mobile");
  const before = await request(
    adminPage,
    `/api/sessions/${session.id}/evidence`,
  );
  expect(
    before.filter((item) => ["mobile", "mobile_recording"].includes(item.kind)),
  ).toEqual([]);
  await approveInUI(adminPage, session.id);
  await expect(page.locator("#start")).toBeEnabled();
  await page.locator("#start").click();
  await expect(page.locator("#mobile-state")).toContainText("uploading", {
    timeout: 20000,
  });
  await expect
    .poll(() => page.locator("#mobile-preview").evaluate((v) => v.readyState), {
      timeout: 20000,
    })
    .toBeGreaterThanOrEqual(2);
  await page
    .getByRole("button", { name: "Disconnect mobile", exact: true })
    .click();
  await expect(phone.locator("#message")).toContainText(
    "Pair this mobile device again",
    { timeout: 10000 },
  );
  await expect
    .poll(() =>
      phone
        .locator("#phone-preview")
        .evaluate((video) =>
          video.srcObject
            .getTracks()
            .every((track) => track.readyState === "ended"),
        ),
    )
    .toBeTruthy();
  await phoneContext.close();
  await staff.close();
});

test("AES-GCM queue survives reload and uploads the original bytes", async ({
  page,
}) => {
  await login(page);
  const result = await page.evaluate(async () => {
    const q = await new EvidenceQueue("browser-queue-test").init();
    await q.clear();
    const fresh = await new EvidenceQueue("browser-queue-test").init();
    await fresh.enqueue(
      "/test-only",
      new Blob(["secret evidence"], { type: "video/webm" }),
    );
    return Array.from(new Uint8Array((await fresh.all())[0].data));
  });
  expect(Buffer.from(result).toString()).not.toContain("secret evidence");
  await page.reload();
  const restored = await page.evaluate(async () => {
    const q = await new EvidenceQueue("browser-queue-test").init();
    let bytes;
    await q.flush(async (url, options) => {
      bytes = new TextDecoder().decode(options.body);
    });
    await q.clear();
    return bytes;
  });
  expect(restored).toBe("secret evidence");
});

test("device-side recording chunks upload and reassemble as a playable WebM", async ({
  browser,
  page,
}) => {
  await page.addInitScript(() => {
    navigator.mediaDevices.getDisplayMedia = async () => {
      const canvas = document.createElement("canvas");
      canvas.width = 640;
      canvas.height = 360;
      const ctx = canvas.getContext("2d");
      let n = 0;
      setInterval(() => {
        ctx.fillStyle = n++ % 2 ? "#336699" : "#669933";
        ctx.fillRect(0, 0, 640, 360);
      }, 100);
      const stream = canvas.captureStream(5);
      const track = stream.getVideoTracks()[0];
      const original = track.getSettings.bind(track);
      track.getSettings = () => ({ ...original(), displaySurface: "monitor" });
      return stream;
    };
  });
  const automaticUploads = [];
  page.on("request", (request) => {
    if (/\/media\?kind=(camera|screen|recording)/.test(request.url()))
      automaticUploads.push(request.url());
  });
  await login(page);
  await prepare(page, "E2E assessment");

  await page
    .getByRole("button", { name: "Share entire display", exact: true })
    .click();
  const staff = await browser.newContext();
  const adminPage = await staff.newPage();
  await login(adminPage, "admin");
  const session = await pendingSession(adminPage, "E2E assessment");
  await page.waitForTimeout(6000);
  expect(automaticUploads).toEqual([]);
  await expect(page.locator("#assessment-alert")).toBeHidden();
  await expect(page.locator("#assessment-observations")).toBeHidden();
  expect(
    (await request(page, `/api/sessions/${session.id}/monitoring`))
      .observation_count,
  ).toBe(0);
  await approveInUI(adminPage, session.id);
  await page.locator("#start").click();
  await expect(page.locator("#assessment-observations")).toBeVisible();
  let recording;
  await expect
    .poll(
      async () => {
        const rows = await request(
          adminPage,
          `/api/sessions/${session.id}/evidence`,
        );
        recording = rows.find((e) => e.kind === "recording");
        return !!recording;
      },
      { timeout: 15000 },
    )
    .toBeTruthy();
  await adminPage.locator("#nav-assessments").click();
  await expect(adminPage.locator("#dashboard")).toBeVisible();
  await adminPage.getByRole("button", { name: "Refresh", exact: true }).click();
  await adminPage
    .locator(`#sessions article[data-session-id="${session.id}"]`)
    .getByRole("button", { name: "Review session" })
    .click();
  await adminPage
    .getByRole("button", { name: "Load recording", exact: true })
    .first()
    .click();
  await expect
    .poll(
      () =>
        adminPage
          .locator("#evidence video")
          .first()
          .evaluate((v) => v.readyState),
      { timeout: 10000 },
    )
    .toBeGreaterThanOrEqual(2);
  await staff.close();
});

test("professional workspace remains accessible at desktop and phone widths", async ({
  page,
}) => {
  const AxeBuilder = require("@axe-core/playwright").default;
  async function accessible(view) {
    const results = await new AxeBuilder({ page })
      .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
      .analyze();
    expect(
      results.violations.map((v) => ({
        id: v.id,
        nodes: v.nodes.map((n) => ({
          target: n.target,
          summary: n.failureSummary,
        })),
      })),
      view,
    ).toEqual([]);
  }
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("/");
  await expect(page.locator("#login-panel")).toBeVisible();
  await accessible("desktop login");
  await login(page);
  await accessible("desktop dashboard");
  await page.setViewportSize({ width: 430, height: 900 });
  await accessible("phone dashboard");
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
  await page
    .locator("#exams article")
    .filter({
      has: page.getByRole("heading", { name: "E2E assessment", exact: true }),
    })
    .getByRole("button", { name: "Open assessment" })
    .click();
  await expect(page.locator("#setup-step-1")).toBeVisible();
  await accessible("phone consent");
  await page.locator("#consent-check").check();
  await page
    .getByRole("button", { name: "Accept & continue", exact: true })
    .click();
  await page.locator("#enrollment-name").fill("Test student");
  await page.locator("#enrollment-reference").fill("student@example.test");
  await page.locator("#confirm-enrollment").click();
  await expect(page.locator("#setup-step-2")).toBeVisible();
  await accessible("phone device checks");
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();

  await page.locator("#devices-next").click();
  await expect(page.locator("#setup-step-3")).toBeVisible();
  await accessible("phone identity");
});

test("identity upload shows actual server result and survives a camera permission failure", async ({
  page,
}) => {
  await login(page);
  await page
    .locator("#exams article")
    .filter({
      has: page.getByRole("heading", { name: "E2E assessment", exact: true }),
    })
    .getByRole("button", { name: "Open assessment" })
    .click();
  await page.locator("#consent-check").check();
  await page.locator("#consent").click();
  await expect(page.locator("#setup-step-2")).toBeVisible();
  await page.evaluate(() => {
    navigator.mediaDevices.getUserMedia = async () => {
      throw new DOMException("Denied", "NotAllowedError");
    };
  });
  await page.locator("#camera").click();
  await expect(page.locator("#camera-error")).toContainText(
    "Camera access is blocked",
  );
  await page.route("**/media?kind=id&**", async (route) => {
    await new Promise((r) => setTimeout(r, 600));
    await route.fulfill({
      status: 400,
      contentType: "application/json",
      body: JSON.stringify({ detail: "Image could not be decoded" }),
    });
  });
  await page.locator("#id-file").setInputFiles({
    name: "invalid.jpg",
    mimeType: "image/jpeg",
    buffer: Buffer.from("invalid image"),
  });
  await expect(page.locator("#id-upload-status")).toHaveText("Uploading…");
  await expect(page.locator("#id-upload-error")).toContainText(
    "Image could not be decoded",
  );
  await expect(page.locator("#id-upload-status")).toContainText("Missing");
  await expect(page.locator("#missing-items")).toContainText(
    "Choose an ID image",
  );
  await expect(page.locator("#verify")).toBeDisabled();
  await expect(page.locator("#upload-id")).toBeVisible();
});

test("invalid mobile link explains how to reconnect", async ({ page }) => {
  await page.goto("/mobile#expired-test-token");
  await expect(page.locator("#mobile-error")).toBeVisible();
  await expect(page.locator("#mobile-error-detail")).toContainText(
    "generate a new QR code",
  );
  await expect(page.locator("#phone-start")).toBeDisabled();
});

test("display capture runs inside the click and clears a rejected surface error", async ({
  page,
}) => {
  await page.addInitScript(() => {
    let attempts = 0;
    navigator.mediaDevices.getDisplayMedia = async () => {
      if (!window.inCaptureClick)
        throw new DOMException(
          "getDisplayMedia must be called from a user gesture handler.",
          "InvalidStateError",
        );
      const c = document.createElement("canvas");
      c.width = 640;
      c.height = 360;
      c.getContext("2d").fillRect(0, 0, 640, 360);
      const stream = c.captureStream(5),
        track = stream.getVideoTracks()[0];
      track.getSettings = () => ({
        displaySurface: ++attempts === 1 ? "window" : "monitor",
      });
      return stream;
    };
  });
  await login(page);
  await page
    .locator("#exams article")
    .filter({
      has: page.getByRole("heading", { name: "E2E assessment", exact: true }),
    })
    .getByRole("button", { name: "Open assessment" })
    .click();
  await page.locator("#consent-check").check();
  await page.locator("#consent").click();
  await page.locator("#enrollment-name").fill("Test student");
  await page.locator("#enrollment-reference").fill("student@example.test");
  await page.locator("#confirm-enrollment").click();
  await expect(page.locator("#setup-step-2")).toBeVisible();
  await page.evaluate(() => {
    window.inCaptureClick = true;
    document.getElementById("screen").click();
    window.inCaptureClick = false;
  });
  await expect(page.locator("#screen-error")).toContainText(
    "You shared a tab or window",
  );
  await page.evaluate(() => {
    window.inCaptureClick = true;
    document.getElementById("screen").click();
    window.inCaptureClick = false;
  });
  await expect(page.locator("#screen-status")).toHaveText("Display shared");
  await expect(page.locator("#screen-error")).toBeEmpty();
  await expect(page.locator("#message")).not.toContainText("gesture");
});

test("selfie preview is not uploaded until confirmed and can be retaken", async ({
  page,
}) => {
  await login(page);
  await page
    .locator("#exams article")
    .filter({
      has: page.getByRole("heading", { name: "E2E assessment", exact: true }),
    })
    .getByRole("button", { name: "Open assessment" })
    .click();
  await page.locator("#consent-check").check();
  await page.locator("#consent").click();
  await page.locator("#selfie").click();
  await expect(page.locator("#selfie-preview-panel")).toBeVisible();
  await expect(page.locator("#selfie-upload-status")).toContainText(
    "New preview",
  );
  await page.locator("#retake-selfie").click();
  await expect(page.locator("#selfie-draft-help")).toContainText(
    "Preview only",
  );
  await page.locator("#confirm-selfie").click();
  await expect(page.locator("#selfie-upload-status")).toHaveText(
    "Selfie uploaded successfully",
  );
  await expect(page.locator("#selfie-quality")).toContainText("unavailable");
  await page.reload();
  await page
    .getByRole("button", { name: "Open session", exact: true })
    .first()
    .click();
  await expect(page.locator("#selfie-still-preview")).toBeVisible();
  await expect(page.locator("#selfie-still-preview")).toHaveJSProperty(
    "naturalWidth",
    1280,
  );
});

test("monitoring panel shows sampled faces and honest display permission failures", async ({
  page,
  browser,
}) => {
  await page.addInitScript(() => {
    window.getScreenDetails = async () => {
      throw new DOMException("Permission denied", "NotAllowedError");
    };
  });
  await page.route("**/monitoring", (route) =>
    route.fulfill({
      json: {
        latest: {
          face_sample: {
            received_at: Date.now() / 1000,
            details: {
              status: "assessed",
              face_count: 2,
              quality_ok: false,
              boxes: [
                { x: 0.1, y: 0.1, width: 0.2, height: 0.3 },
                { x: 0.5, y: 0.2, width: 0.2, height: 0.3 },
              ],
            },
          },
        },
        observation_count: 1,
        recent: [
          {
            id: "test",
            kind: "multiple_faces",
            received_at: Date.now() / 1000,
            disposition: "unreviewed",
          },
        ],
        vision_available: true,
        server_time: Date.now() / 1000,
      },
    }),
  );
  await login(page);
  await prepare(page);
  await expect(page.locator("#monitoring-panel")).toBeHidden();
  await page.locator("#confirm-enrollment").click();
  await expect(page.locator("#enrollment-result")).toHaveText(
    "Your details were saved as entered.",
  );
  const staff = await browser.newContext();
  const adminPage = await staff.newPage();
  await login(adminPage, "admin");
  const session = await pendingSession(adminPage, "E2E assessment");
  await approveInUI(adminPage, session.id);
  await page.locator("#start").click();
  await page.locator("#monitoring-panel summary").click();
  await expect(page.locator("#face-live-status")).toContainText(
    "2 faces visible",
  );
  await expect(page.locator("#live-warning")).toContainText(
    "More than one face",
  );
  await page.locator("#check-displays").click();
  await expect(page.locator("#display-inventory")).toContainText(
    "permission was denied",
  );
  await expect(page.locator("#assessment-alert")).toContainText(
    "More than one face",
  );
  await expect(page.locator("#assessment-observations")).toBeVisible();
  await staff.close();
});

test("fullscreen recovery and aggregate input do not automatically terminate the assessment", async ({
  browser,
  page,
}) => {
  await login(page);
  await prepare(page);
  const staff = await browser.newContext();
  const adminPage = await staff.newPage();
  await login(adminPage, "admin");
  const session = await pendingSession(adminPage, "E2E assessment");
  await approveInUI(adminPage, session.id);
  await expect(page.locator("#start")).toBeEnabled();
  await page.locator("#start").click();
  await expect(page.locator("#questions")).toBeVisible();
  await page
    .locator("#answer-q1")
    .fill("Synthetic response for aggregate monitoring.");
  await page.evaluate(async () => {
    if (document.fullscreenElement) await document.exitFullscreen();
  });
  await expect(page.locator("#fullscreen-recovery")).toBeVisible();
  const summaryRequest = page.waitForRequest(
    (r) => r.url().endsWith("/behavior") && r.method() === "POST",
  );
  await page.clock.install();
  await page.clock.fastForward(31000);
  const summary = JSON.parse((await summaryRequest).postData());
  expect(summary.input_events).toBeGreaterThan(0);
  expect(JSON.stringify(summary)).not.toContain("Synthetic response");
  expect(Object.keys(summary).sort()).toEqual(
    [
      "composition_events",
      "event_id",
      "focus_changes",
      "input_events",
      "key_events",
      "paste_events",
      "pointer_events",
      "untrusted_events",
      "window_seconds",
    ].sort(),
  );
  await expect(page.locator("#fullscreen-help")).toContainText(
    "recorded for review",
  );
  expect((await request(page, "/api/sessions/" + session.id)).status).toBe(
    "in_progress",
  );
  await staff.close();
});

test("administrator can configure restored monitoring controls and account authorization", async ({
  page,
}) => {
  await login(page, "admin");
  await page.locator("#nav-manage").click();
  await page
    .locator("details")
    .filter({ has: page.locator("#exam-form") })
    .locator("summary")
    .click();
  const form = page.locator("#exam-form");
  await form.locator("[name=name]").fill("Restored monitoring settings");
  await form.locator("[name=prompts]").fill("Describe the verification test.");
  await form.locator("[name=require_seb]").uncheck();
  await form
    .locator("[name=verification_mode]")
    .selectOption("institution_account");
  await form.locator("[name=camera_interval_seconds]").fill("2");
  await form.locator("[name=screenshot_interval_seconds]").fill("10");
  await form.locator("[name=recording_fps]").fill("4");
  await form.locator("[name=enable_audio_monitoring]").uncheck();
  await form.locator("[name=max_verification_attempts]").fill("0");
  const response = page.waitForResponse(
    (r) => r.url().endsWith("/api/exams") && r.request().method() === "POST",
  );
  await form
    .getByRole("button", { name: "Create assessment", exact: true })
    .click();
  expect((await response).status()).toBe(201);
  const exams = await request(page, "/api/exams");
  const saved = exams.find((e) => e.name === "Restored monitoring settings");
  expect(saved.policy).toMatchObject({
    verification_mode: "institution_account",
    camera_interval_seconds: 2,
    screenshot_interval_seconds: 10,
    recording_fps: 4,
    enable_audio_monitoring: false,
    max_verification_attempts: 0,
  });
});

test("assigned-account mode runs advisory checks automatically without a proctor queue", async ({
  page,
}) => {
  let checks = 0;
  page.on("request", (r) => {
    if (r.url().endsWith("/verification") && r.method() === "POST") checks++;
  });
  await login(page);
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
  await page.locator("#consent-check").check();
  await page.locator("#consent").click();
  await page.locator("#selfie").click();
  await page.locator("#confirm-selfie").click();
  await expect(page.locator("#selfie-upload-status")).toContainText("uploaded");
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
    name: "synthetic-test.jpg",
    mimeType: "image/jpeg",
    buffer: Buffer.from(frame, "base64"),
  });
  await expect.poll(() => checks).toBe(1);
  await expect(page.locator("#comparison-result")).toContainText("unavailable");
  await expect(page.locator("#control-note")).toContainText("assigned account");
  await expect(page.locator("#identity-instructions")).not.toContainText(
    "before you begin",
  );
  await expect(page.locator("#start")).toBeDisabled();
  await expect(page.locator("#missing-items")).toContainText("registered name");
  await page.locator("#confirm-enrollment").click();
  await expect(page.locator("#start")).toBeEnabled();
  await page.locator("#enrollment-name").fill("");
  await page.locator("#enrollment-reference").fill("");
  await expect(page.locator("#start")).toBeDisabled();
  await expect(page.locator("#ready-title")).toContainText(
    "Finish the missing setup checks",
  );
  await page.locator("#verify").click();
  await expect(page.locator("#message")).toContainText("model unavailable");
  await expect(page.locator("#message")).not.toContainText(
    "Image checks completed",
  );
  const me = await request(page, "/api/auth/me");
  await page.locator("#enrollment-name").fill(me.user.name);
  await page.locator("#enrollment-reference").fill(me.user.email);
  await page.locator("#confirm-enrollment").click();
  await expect(page.locator("#start")).toBeEnabled();
  const sid = (await request(page, "/api/sessions")).find(
    (s) =>
      s.status === "preflight" &&
      s.policy.verification_mode === "institution_account",
  ).id;
  await page.reload();
  await page
    .locator(`#sessions article[data-session-id="${sid}"]`)
    .getByRole("button", { name: "Open session" })
    .click();
  await expect(page.locator("#id-file")).toHaveValue("");
  await expect(page.locator("#saved-evidence-note")).toContainText(
    "An empty file picker does not remove a saved upload",
  );
  await expect(page.locator("#id-saved-preview")).toBeVisible();
  await expect(page.locator("#selfie-still-preview")).toBeVisible();
  await expect(page.locator("#start")).toBeEnabled();
  const sessionURL = `**/api/sessions/${sid}`;
  await page.route(sessionURL, async (route) => {
    const response = await route.fetch();
    const body = await response.json();
    await route.fulfill({
      response,
      json: { ...body, identity_evidence: { id: false, selfie: false } },
    });
  });
  await expect(page.locator("#start")).toBeDisabled({ timeout: 10000 });
  await expect(page.locator("#id-upload-status")).toContainText("Missing");
  await expect(page.locator("#id-saved-preview")).toBeHidden();
  await expect(page.locator("#selfie-preview-panel")).toBeHidden();
  await expect(page.locator("#missing-items")).toContainText(
    "Choose an ID image",
  );
  await page.unroute(sessionURL);
  await expect(page.locator("#start")).toBeEnabled({ timeout: 10000 });
  await page.locator("#start").click();
  await expect(page.locator("#questions")).toBeVisible();
  await expect(page.locator("#session-state")).toHaveText("In progress");
  const restrictions = await page.locator("#answer-q1").evaluate((input) => {
    const save = new KeyboardEvent("keydown", {
      key: "s",
      ctrlKey: true,
      bubbles: true,
      cancelable: true,
    });
    const escape = new KeyboardEvent("keydown", {
      key: "Escape",
      bubbles: true,
      cancelable: true,
    });
    const menu = new MouseEvent("contextmenu", {
      bubbles: true,
      cancelable: true,
    });
    return {
      saveAllowed: input.dispatchEvent(save),
      escapeAllowed: input.dispatchEvent(escape),
      contextAllowed: input.dispatchEvent(menu),
    };
  });
  expect(restrictions).toEqual({
    saveAllowed: false,
    escapeAllowed: true,
    contextAllowed: false,
  });
});

test("submission saves final edits despite rate-limited evidence and reports clipboard shortcuts at top", async ({
  page,
  browser,
}) => {
  await login(page);
  await prepare(page);
  const staff = await browser.newContext();
  const adminPage = await staff.newPage();
  await login(adminPage, "admin");
  const session = await pendingSession(adminPage, "E2E assessment");
  await approveInUI(adminPage, session.id);
  await page.locator("#start").click();
  await expect(page.locator("#questions")).toBeVisible();
  for (const modifier of ["Control", "Meta"]) {
    await page.locator("#answer-q1").focus();
    await page.keyboard.press(`${modifier}+c`);
    await expect(page.locator("#assessment-alert")).toContainText(
      "Copy observed",
    );
    await page.keyboard.press(`${modifier}+x`);
    await expect(page.locator("#assessment-alert")).toContainText(
      "Cut observed",
    );
    await page.keyboard.press(`${modifier}+v`);
    await expect(page.locator("#assessment-alert")).toContainText(
      "Paste observed",
    );
  }
  await page.screenshot({ path: "docs/ui/assessment-alerts.png" });
  let mediaRequests = 0;
  await page.route("**/api/sessions/*/media?**", async (route) => {
    mediaRequests++;
    await route.fulfill({
      status: 429,
      headers: { "Retry-After": "60" },
      json: { detail: "Too many requests; try again later" },
    });
  });
  await expect.poll(() => mediaRequests, { timeout: 10000 }).toBeGreaterThan(0);
  const before = mediaRequests;
  await page.waitForTimeout(3500);
  expect(mediaRequests).toBe(before);
  await page.route("**/api/sessions/*/answers", (route) =>
    route.fulfill({
      status: 429,
      json: { detail: "Autosave temporarily unavailable" },
    }),
  );
  await page
    .locator("#answer-q1")
    .fill("Final answer survives a blocked evidence queue.");
  await page.evaluate(() => {
    window.EvidenceQueue.prototype.clearSnapshot = async () => {
      throw new Error("Simulated local cleanup failure");
    };
  });
  page.once("dialog", (dialog) => dialog.accept());
  await page.locator("#submit").click();
  await expect(page.locator("#session-state")).toContainText("submitted", {
    timeout: 10000,
  });
  const saved = await request(page, `/api/sessions/${session.id}`);
  expect(saved.answers.q1).toBe(
    "Final answer survives a blocked evidence queue.",
  );
  await expect(page.locator("#save-state")).toContainText(
    "Submitted successfully",
  );
  await staff.close();
});

test("window blur and tab visibility handlers flag only active exams and alerts stay above questions", async ({
  page,
  browser,
}) => {
  await login(page);
  await prepare(page);
  const staff = await browser.newContext();
  const adminPage = await staff.newPage();
  await login(adminPage, "admin");
  const session = await pendingSession(adminPage, "E2E assessment");
  // Switching away for staff approval during setup must not create a focus observation.
  await page.evaluate(() => {
    window.dispatchEvent(new Event("blur"));
    Object.defineProperty(document, "hidden", {
      configurable: true,
      value: true,
    });
    document.dispatchEvent(new Event("visibilitychange"));
    delete document.hidden;
  });
  const setup = await request(page, `/api/sessions/${session.id}/monitoring`);
  expect(setup.observation_count).toBe(0);
  await expect(page.locator("#assessment-observations")).toBeHidden();
  await approveInUI(adminPage, session.id);
  await page.bringToFront();
  await page.locator("#start").click();
  await expect(page.locator("#questions")).toBeVisible();
  await page.waitForTimeout(1600);
  const lostFocus = page.waitForRequest(
    (request) =>
      request.url().endsWith("/events") &&
      request.postDataJSON()?.kind === "focus_lost",
  );
  // Headless Chrome does not deliver native focus/visibility changes between its test tabs.
  // Exercise both browser event entry points together and check the deduplication.
  await page.evaluate(() => {
    window.dispatchEvent(new Event("blur"));
    Object.defineProperty(document, "hidden", {
      configurable: true,
      value: true,
    });
    document.dispatchEvent(new Event("visibilitychange"));
    delete document.hidden;
  });
  await lostFocus;
  await expect(page.locator("#assessment-alert")).toContainText("lost focus");
  await expect(page.locator("#assessment-observations")).toBeVisible();
  await expect
    .poll(async () =>
      (
        await request(page, `/api/sessions/${session.id}/monitoring`)
      ).recent.some((row) => row.kind === "focus_lost"),
    )
    .toBeTruthy();
  expect(
    await page
      .locator("#assessment-observations")
      .evaluate(
        (panel) =>
          panel.compareDocumentPosition(document.getElementById("questions")) &
          Node.DOCUMENT_POSITION_FOLLOWING,
      ),
  ).toBeTruthy();
  await expect(page.locator("#recent-observations")).toContainText(
    "Assessment window lost focus",
  );
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({
    path: "docs/ui/current-attempt-alerts.png",
    fullPage: true,
  });
  const focusRows = (
    await request(page, `/api/sessions/${session.id}/monitoring`)
  ).recent.filter((row) => row.kind === "focus_lost");
  expect(focusRows).toHaveLength(1);
  await staff.close();
});

test("fifth staff-confirmed violation submits the attempt and shows the reason to the student", async ({
  page,
  browser,
}) => {
  await login(page);
  await prepare(page);
  const staff = await browser.newContext();
  const adminPage = await staff.newPage();
  await login(adminPage, "admin");
  const session = await pendingSession(adminPage, "E2E assessment");
  await approveInUI(adminPage, session.id);
  await page.locator("#start").click();
  await page
    .locator("#answer-q1")
    .fill("Saved answer before the fifth confirmed violation.");
  await expect(page.locator("#save-state")).toContainText("Saved to server");
  const ids = [];
  for (let index = 0; index < 6; index++) {
    const event = await request(
      page,
      `/api/sessions/${session.id}/events`,
      "POST",
      { event_id: crypto.randomUUID(), kind: "focus_lost" },
    );
    ids.push(event.id);
  }
  expect((await request(page, `/api/sessions/${session.id}`)).status).toBe(
    "in_progress",
  );
  const review = {
    disposition: "confirmed_by_reviewer",
    reason:
      "Staff reviewed the evidence and confirmed this exam policy breach.",
  };
  for (const id of ids.slice(0, 4))
    await request(adminPage, `/api/observations/${id}/review`, "POST", review);
  await expect(page.locator("#confirmed-violation-count")).toContainText(
    "4 / 5",
  );
  await expect(page.locator("#session-state")).toContainText("In progress");
  await request(
    adminPage,
    `/api/observations/${ids[4]}/review`,
    "POST",
    review,
  );
  await expect(page.locator("#session-state")).toContainText("submitted", {
    timeout: 10000,
  });
  await expect(page.locator("#save-state")).toContainText(
    "Automatically submitted after five proctor-confirmed violations",
  );
  await expect(page.locator("#answer-q1")).toBeDisabled();
  const result = await request(page, `/api/sessions/${session.id}`);
  expect(result.answers.q1).toBe(
    "Saved answer before the fifth confirmed violation.",
  );
  expect(result.submission_reason).toBe("five_confirmed_violations");
  await staff.close();
});

test("a new attempt has neutral photo prompts and cannot inherit a previous no-face warning", async ({
  page,
}) => {
  await login(page);
  await prepare(page);
  await page.route("**/monitoring", async (route) => {
    const response = await route.fetch();
    const data = await response.json();
    if (data.identity_evidence_ids?.id)
      data.latest.id_quality = {
        details: {
          evidence_id: data.identity_evidence_ids.id,
          status: "assessed",
          face_count: 0,
          quality_ok: false,
        },
        received_at: Date.now() / 1000,
      };
    await route.fulfill({ response, json: data });
  });
  await expect(page.locator("#id-quality")).toContainText(
    "Saved ID image: No clear face found",
    { timeout: 10000 },
  );
  await page.locator("#back").click();
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
  await expect(page.locator("#consent-check")).toBeVisible();
  await expect(page.locator("#consent-check")).not.toBeChecked();
  await page.locator("#consent-check").check();
  await page.locator("#consent").click();
  await expect(page.locator("#id-quality")).toHaveText(
    "Upload an ID image to run photo checks.",
  );
  await expect(page.locator("#selfie-quality")).toHaveText(
    "Capture and confirm a selfie to run photo checks.",
  );
  await expect(page.locator("#id-quality")).not.toHaveClass(/field-error/);
  await page.waitForTimeout(3500);
  await expect(page.locator("#id-quality")).not.toContainText("No clear face");
  await expect(page.locator("#missing-items")).toContainText(
    "Choose an ID image",
  );
});

async function displaySetup(page) {
  await login(page);
  await page
    .locator("#exams article")
    .filter({
      has: page.getByRole("heading", { name: "E2E assessment", exact: true }),
    })
    .getByRole("button", { name: "Open assessment" })
    .click();
  await expect(page.locator("#consent-check")).toBeVisible();
  await page.locator("#consent-check").check();
  await page.locator("#consent").click();
  await expect(page.locator("#setup-step-2")).toBeVisible();
}

test("display picker can be cancelled and retried, with late streams stopped", async ({
  page,
}) => {
  await page.addInitScript(() => {
    window.displayCalls = 0;
    window.displayStreams = [];
    navigator.mediaDevices.getDisplayMedia = () => {
      const canvas = document.createElement("canvas");
      canvas.width = 320;
      canvas.height = 240;
      canvas.getContext("2d").fillRect(0, 0, 320, 240);
      const stream = canvas.captureStream(5);
      stream.getVideoTracks()[0].getSettings = () => ({
        displaySurface: "monitor",
      });
      window.displayStreams.push(stream);
      if (++window.displayCalls === 1)
        return new Promise((resolve) => {
          window.finishOldPicker = () => resolve(stream);
        });
      return Promise.resolve(stream);
    };
  });
  await displaySetup(page);
  await page.route("**/heartbeat", () => {}); // A stalled device update must not hold the picker button.
  await page.locator("#screen").click();
  await expect(page.locator("#screen-progress")).toContainText(
    "Waiting for the browser screen picker",
  );
  await expect(page.locator("#screen")).toBeDisabled();
  await page.evaluate(() => document.getElementById("resume-screen").click());
  expect(await page.evaluate(() => window.displayCalls)).toBe(1);
  await page.locator("#cancel-screen").click();
  await expect(page.locator("#screen")).toBeEnabled();
  await expect(page.locator("#screen-error")).toContainText("cancelled");
  await page.evaluate(() => window.finishOldPicker());
  await expect
    .poll(() =>
      page.evaluate(() => window.displayStreams[0].getTracks()[0].readyState),
    )
    .toBe("ended");
  await page.locator("#screen").click();
  await expect(page.locator("#screen-status")).toHaveText("Display shared");
  await expect(page.locator("#screen-progress")).toContainText(
    "Recording starts only when you begin",
  );
  await expect(page.locator(".display-capture-source")).toHaveCount(1);
  await expect(page.locator("#screen-error")).toBeEmpty();
  await expect(page.locator("#screen")).toBeEnabled();
  await page.screenshot({
    path: "docs/ui/display-sharing-ready.png",
    fullPage: true,
  });
  await page.evaluate(() => {
    const track = window.displayStreams[1].getVideoTracks()[0];
    track.stop();
    track.dispatchEvent(new Event("ended"));
  });
  await expect(page.locator("#screen-status")).toHaveText("Not shared");
  await expect(page.locator("#screen")).toHaveText("Share entire display");
});

test("an unanswered display picker times out with a usable retry button", async ({
  page,
}) => {
  await page.addInitScript(() => {
    navigator.mediaDevices.getDisplayMedia = () => new Promise(() => {});
  });
  await displaySetup(page);
  await page.clock.install();
  await page.locator("#screen").click();
  await expect(page.locator("#cancel-screen")).toBeVisible();
  await page.clock.fastForward(45001);
  await expect(page.locator("#screen")).toBeEnabled();
  await expect(page.locator("#screen-error")).toContainText(
    "No display was selected",
  );
  await expect(page.locator("#cancel-screen")).toBeHidden();
});

test("a stalled display preview releases the stream and re-enables retry", async ({
  page,
}) => {
  await page.addInitScript(() => {
    navigator.mediaDevices.getDisplayMedia = async () => {
      const canvas = document.createElement("canvas");
      canvas.width = 320;
      canvas.height = 240;
      canvas.getContext("2d").fillRect(0, 0, 320, 240);
      const stream = canvas.captureStream(5);
      stream.getVideoTracks()[0].getSettings = () => ({
        displaySurface: "monitor",
      });
      window.previewStream = stream;
      return stream;
    };
    const play = HTMLMediaElement.prototype.play;
    HTMLMediaElement.prototype.play = function () {
      return this.classList.contains("display-capture-source")
        ? new Promise(() => {})
        : play.call(this);
    };
  });
  await displaySetup(page);
  await page.clock.install();
  await page.locator("#screen").click();
  await expect(page.locator("#screen-progress")).toContainText(
    "Connecting the preview",
  );
  await page.clock.fastForward(8001);
  await expect(page.locator("#screen")).toBeEnabled();
  await expect(page.locator("#screen-error")).toContainText(
    "preview did not start",
  );
  expect(
    await page.evaluate(() => window.previewStream.getTracks()[0].readyState),
  ).toBe("ended");
  await expect(page.locator(".display-capture-source")).toHaveCount(0);
});

test("setup keeps saved photos separate from new drafts and groups the next action in one flow", async ({
  page,
}) => {
  await login(page);
  await prepare(page);
  await page.route("**/monitoring", async (route) => {
    const response = await route.fetch();
    const data = await response.json();
    if (data.identity_evidence_ids?.id)
      data.latest.id_quality = {
        details: {
          evidence_id: data.identity_evidence_ids.id,
          status: "assessed",
          face_count: 0,
          quality_ok: false,
        },
        received_at: Date.now() / 1000,
      };
    await route.fulfill({ response, json: data });
  });
  await expect(page.locator("#id-upload-status")).toHaveText(
    "Replace this ID photo",
    { timeout: 10000 },
  );
  await expect(page.locator("#confirm-selfie")).toBeHidden();
  await expect(page.locator("#selfie-draft-help")).toContainText(
    "Saved selfie for this attempt",
  );
  await expect(page.locator("#setup-summary-text")).toContainText(
    "Replace the ID image",
  );
  expect(
    await page
      .locator("#enrollment-card")
      .evaluate(
        (el) =>
          !!(
            el.compareDocumentPosition(
              document.getElementById("setup-step-2"),
            ) & Node.DOCUMENT_POSITION_FOLLOWING
          ),
      ),
  ).toBeTruthy();
  expect(await page.locator("#setup-step-2 .preview-card").count()).toBe(1);
  await page.screenshot({
    path: "docs/ui/organized-setup-saved-photos.png",
    fullPage: true,
  });
  await page.locator("#retake-selfie").click();
  await expect(page.locator("#confirm-selfie")).toBeVisible();
  await expect(page.locator("#selfie-upload-status")).toContainText(
    "New preview",
  );
  await expect(page.locator("#selfie-quality")).toBeHidden();
  await expect(page.locator("#comparison-result")).toBeHidden();
  await page.locator("#confirm-selfie").click();
  await expect(page.locator("#confirm-selfie")).toBeHidden();
  await expect(page.locator("#selfie-draft-help")).toContainText(
    "Saved selfie for this attempt",
  );
});

test("staff history has clear incident names, filters and review notes", async ({
  page,
  browser,
}) => {
  await login(page);
  await prepare(page);
  const staff = await browser.newContext();
  const adminPage = await staff.newPage();
  await login(adminPage, "admin");
  const session = await page.evaluate(() => state.session);
  await approveInUI(adminPage, session.id);
  await page.locator("#check-approval").click();
  await page.locator("#start").click();
  await expect(page.locator("#questions")).toBeVisible();
  await page.evaluate(async () => {
    const canvas = document.createElement("canvas");
    canvas.width = 320;
    canvas.height = 240;
    const ctx = canvas.getContext("2d");
    ctx.fillStyle = "#6251cf";
    ctx.fillRect(0, 0, 320, 240);
    ctx.fillStyle = "white";
    ctx.font = "24px Arial";
    ctx.fillText("Assessment recording", 20, 120);
    const me = await (await fetch("/api/auth/me")).json();
    const headers = { "X-CSRF-Token": me.csrf_token };
    const streamId = crypto.randomUUID();
    await fetch(`/api/sessions/${state.session.id}/events`, {
      method: "POST",
      headers: { ...headers, "Content-Type": "application/json" },
      body: JSON.stringify({
        event_id: crypto.randomUUID(),
        kind: "recording_started",
        stream_id: streamId,
      }),
    });
    const stream = canvas.captureStream(15);
    const recorder = new MediaRecorder(stream, { mimeType: "video/webm" });
    const chunks = [];
    recorder.ondataavailable = (e) => chunks.push(e.data);
    const stopped = new Promise((resolve) => (recorder.onstop = resolve));
    recorder.start();
    const paint = setInterval(() => {
      ctx.fillStyle = "white";
      ctx.fillRect(20, 180, 30, 20);
    }, 80);
    await new Promise((resolve) => setTimeout(resolve, 400));
    window.reviewTestEvent = crypto.randomUUID();
    await fetch(`/api/sessions/${state.session.id}/events`, {
      method: "POST",
      headers: { ...headers, "Content-Type": "application/json" },
      body: JSON.stringify({
        event_id: window.reviewTestEvent,
        kind: "window_geometry_change",
        recovery_status: "pending",
      }),
    });
    await new Promise((resolve) => setTimeout(resolve, 1200));
    clearInterval(paint);
    recorder.stop();
    await stopped;
    stream.getTracks().forEach((track) => track.stop());
    await fetch(
      `/api/sessions/${state.session.id}/media?kind=recording&stream_id=${streamId}&sequence=0`,
      {
        method: "POST",
        headers: { ...headers, "Content-Type": "video/webm" },
        body: new Blob(chunks, { type: "video/webm" }),
      },
    );
    const image = await new Promise((resolve) =>
      canvas.toBlob(resolve, "image/jpeg"),
    );
    await fetch(
      `/api/sessions/${state.session.id}/media?kind=camera&stream_id=${crypto.randomUUID()}&sequence=0`,
      {
        method: "POST",
        headers: { ...headers, "Content-Type": "image/jpeg" },
        body: image,
      },
    );
  });
  const eventId = await page.evaluate(() => window.reviewTestEvent);
  await request(page, `/api/sessions/${session.id}/events`, "POST", {
    event_id: eventId,
    kind: "window_geometry_change",
    recovery_status: "pending",
  });
  await request(page, `/api/sessions/${session.id}/events`, "POST", {
    event_id: eventId,
    kind: "window_geometry_change",
    recovery_status: "restored",
    duration_ms: 2500,
  });
  await adminPage.evaluate((id) => openReview(id), session.id);
  // Staff retain their available assessments on the dashboard.
  await adminPage.locator("#review-back").click();
  await expect(adminPage.locator("#assessments-area")).toBeVisible();
  await expect(
    adminPage.locator("#exams article").filter({
      has: adminPage.getByRole("heading", {
        name: "E2E assessment",
        exact: true,
      }),
    }),
  ).toBeVisible();
  await adminPage.evaluate((id) => openReview(id), session.id);
  await expect(adminPage.locator("#review-identity-images img")).toHaveCount(2);
  await expect(
    adminPage.locator("#review-answers .answer-review-card"),
  ).toHaveCount(1);
  await adminPage.locator("#load-recording").click();
  await expect(adminPage.locator("#recording-status")).toContainText(
    "Full recording ready",
    { timeout: 30000 },
  );
  await expect(adminPage.locator("#review video")).toHaveCount(1);
  await expect(adminPage.locator("#download-recording")).toBeVisible();
  await expect(adminPage.locator(".recording-flag")).toHaveCount(1);
  await adminPage.locator(".recording-flag").click();
  await expect
    .poll(() =>
      adminPage.locator("#review-video").evaluate((video) => !video.paused),
    )
    .toBe(true);
  await adminPage.locator("#review-video").evaluate((video) => video.pause());
  await adminPage.screenshot({
    path: "docs/ui/student-review-report.png",
    fullPage: true,
  });
  await adminPage.locator("#refresh-history").click();
  const incident = adminPage
    .locator(".incident-card")
    .filter({ hasText: "Window resized — fullscreen recovery" });
  await expect(incident).toHaveCount(1);
  await expect(incident).toContainText("Fullscreen restored");
  await expect(incident).toContainText("2.5 seconds");
  await incident
    .getByLabel("Review note")
    .fill(
      "Reviewed the single window resize and its successful fullscreen recovery.",
    );
  await incident
    .getByRole("button", { name: "Confirm violation", exact: true })
    .click();
  await expect(incident.locator(".review-badge")).toHaveText(
    "Confirmed violation",
  );
  await expect(adminPage.locator("#review-state")).toContainText("1 / 5");
  await incident.locator("summary").click();
  await expect(incident.locator(".review-entry")).toContainText(
    "successful fullscreen recovery",
  );
  await adminPage.locator("#history-filter").selectOption("dismissed");
  await expect(adminPage.locator("#observations")).toContainText(
    "No records match",
  );
  await adminPage
    .locator("#history-filter")
    .selectOption("confirmed_by_reviewer");
  await expect(incident).toHaveCount(1);
  await adminPage.locator("#history-search").fill("window resized");
  await expect(incident).toHaveCount(1);
  await adminPage.locator("#history-title").scrollIntoViewIfNeeded();
  await expect(incident.locator(".incident-snapshots img")).toBeVisible();
  await expect
    .poll(() =>
      incident
        .locator(".incident-snapshots img")
        .evaluate((img) => img.naturalWidth),
    )
    .toBeGreaterThan(0);
  await adminPage.screenshot({ path: "docs/ui/admin-incident-history.png" });
  await adminPage.setViewportSize({ width: 390, height: 844 });
  expect(
    await adminPage.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  await adminPage.screenshot({ path: "docs/ui/student-review-mobile.png" });
  await staff.close();
});

test("modern student and staff workspaces keep warnings separate from violations", async ({
  page,
  browser,
}) => {
  const AxeBuilder = require("@axe-core/playwright").default;
  const checkLayout = async (target) => {
    expect(
      await target.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
    const results = await new AxeBuilder({ page: target })
      .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
      .analyze();
    expect(
      results.violations.map((v) => ({
        id: v.id,
        targets: v.nodes.map((n) => n.target),
      })),
    ).toEqual([]);
  };
  await page.setViewportSize({ width: 1440, height: 1000 });
  await login(page);
  await checkLayout(page);
  await page.screenshot({
    path: "docs/ui/modern-student-dashboard.png",
    fullPage: true,
  });
  await page.locator("#session-search").fill("no matching attempt");
  await expect(page.locator("#attempt-results")).toContainText(
    /0|No attempts yet/,
  );
  await page.locator("#session-search").clear();
  await page.setViewportSize({ width: 390, height: 844 });
  await checkLayout(page);
  await page.screenshot({ path: "docs/ui/modern-student-mobile.png" });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await prepare(page);
  const session = await page.evaluate(() => state.session);
  await page.locator('[data-step="2"]').click();
  await page.evaluate(() => window.scrollTo({ top: 0, behavior: "instant" }));
  await page.screenshot({ path: "docs/ui/modern-devices.png", fullPage: true });
  await page.locator('[data-step="5"]').click();
  await page.evaluate(() => window.scrollTo({ top: 0, behavior: "instant" }));
  await page.screenshot({ path: "docs/ui/modern-details.png", fullPage: true });
  await page.locator("#confirm-enrollment").click();
  await page.locator('[data-step="4"]').click();
  const staff = await browser.newContext({
    viewport: { width: 1440, height: 1000 },
  });
  const adminPage = await staff.newPage();
  await login(adminPage, "admin");
  await checkLayout(adminPage);
  await adminPage.screenshot({
    path: "docs/ui/modern-admin-dashboard.png",
    fullPage: true,
  });
  await adminPage.locator("#nav-manage").click();
  await adminPage.getByText("Create an assessment", { exact: true }).click();
  await checkLayout(adminPage);
  await adminPage.screenshot({
    path: "docs/ui/modern-administration.png",
    fullPage: true,
  });
  await adminPage.setViewportSize({ width: 390, height: 844 });
  await checkLayout(adminPage);
  await adminPage.screenshot({
    path: "docs/ui/modern-administration-mobile.png",
  });
  await adminPage.setViewportSize({ width: 1440, height: 1000 });
  await adminPage.locator("#nav-account").click();
  await checkLayout(adminPage);
  await adminPage.screenshot({
    path: "docs/ui/modern-account.png",
    fullPage: true,
  });
  await adminPage.locator("#nav-assessments").click();
  await approveInUI(adminPage, session.id);
  await adminPage.route(
    `**/sessions/${session.id}/observations?*`,
    async (route) => {
      const response = await route.fetch();
      const rows = await response.json();
      rows.unshift({
        id: "warning-fixture",
        kind: "face_image_quality",
        name: "Unclear face image — camera warning",
        source: "model",
        informational: true,
        warning_only: true,
        received_at: Date.now() / 1000,
        disposition: "unreviewed",
        details: { face_count: 1, quality_ok: false },
        images: [],
        review_history: [],
      });
      await route.fulfill({ response, json: rows });
    },
  );
  await adminPage.locator("#refresh-history").click();
  await expect(adminPage.locator("#observations")).not.toContainText(
    "Unclear face image",
  );
  await adminPage.locator("#history-filter").selectOption("warnings");
  await expect(adminPage.locator("#observations")).toContainText(
    "Camera warning · not a violation",
  );
  await expect(
    adminPage
      .locator("#observations")
      .getByRole("button", { name: "Confirm violation" }),
  ).toHaveCount(0);
  await adminPage.screenshot({
    path: "docs/ui/modern-student-report.png",
    fullPage: true,
  });
  await staff.close();
  if (await page.locator("#check-approval").isVisible())
    await page.locator("#check-approval").click();
  await page.locator("#start").click();
  await expect(page.locator("#questions")).toBeVisible();
  await page.route("**/monitoring", async (route) => {
    const response = await route.fetch();
    const data = await response.json();
    data.latest.face_sample = {
      received_at: Date.now() / 1000,
      details: {
        status: "assessed",
        face_count: 1,
        quality_ok: false,
        boxes: [],
      },
    };
    data.recent_warnings = [
      {
        id: "camera-warning",
        name: "Unclear face image — camera warning",
        kind: "face_image_quality",
        received_at: Date.now() / 1000,
      },
    ];
    data.warning_count = 1;
    await route.fulfill({ response, json: data });
  });
  await expect(page.locator("#live-warning")).toContainText(
    "does not count as a violation",
    { timeout: 15000 },
  );
  await expect(page.locator("#live-warning")).toContainText(
    "not an eye-direction check",
  );
  await expect(page.locator("#camera-warning-history")).toBeVisible();
  await expect(page.locator("#confirmed-violation-count")).toContainText(
    "0 / 5",
  );
  await page
    .getByLabel("Explain your reasoning.")
    .fill("A saved answer from the modernized assessment interface.");
  await expect(page.locator("#save-state")).toContainText("Saved to server");
  await page.screenshot({
    path: "docs/ui/modern-active-assessment.png",
    fullPage: false,
  });
  page.once("dialog", (dialog) => dialog.accept());
  await page.locator("#submit").click();
  await expect(page.locator("#completion-receipt")).toBeVisible();
  await page.screenshot({
    path: "docs/ui/modern-completion.png",
    fullPage: true,
  });
});
