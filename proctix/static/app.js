"use strict";
const $ = (id) => document.getElementById(id);
const terminal = new Set(["submitted", "expired", "terminated", "cancelled"]);
const state = {
  user: null,
  csrf: "",
  session: null,
  queue: null,
  camera: null,
  screen: null,
  recorder: null,
  intervals: [],
  revision: 0,
  dirty: false,
  saving: null,
  rtc: null,
  signalAfter: 0,
  objectURLs: [],
  evidenceOffset: 0,
  captureTasks: new Set(),
  stopping: false,
};
class ApiError extends Error {
  constructor(status, message, retryAfter = 0) {
    super(message);
    this.status = status;
    this.retryAfter = retryAfter;
  }
}
function message(text = "", error = false) {
  state.messageVersion = (state.messageVersion || 0) + 1;
  state.messageErrorAction = null;
  $("message").textContent = text;
  $("message").classList.toggle("error", error);
  clearTimeout(state.messageTimeout);
  if (text)
    state.messageTimeout = setTimeout(
      () => {
        $("message").textContent = "";
        $("message").classList.remove("error");
      },
      error ? 8000 : 4500,
    );
}
async function api(url, options = {}) {
  const headers = new Headers(options.headers || {});
  if (
    options.body &&
    !(options.body instanceof ArrayBuffer) &&
    !(options.body instanceof Blob) &&
    typeof options.body !== "string"
  ) {
    headers.set("Content-Type", "application/json");
    options.body = JSON.stringify(options.body);
  }
  if (options.method && !["GET", "HEAD"].includes(options.method))
    headers.set("X-CSRF-Token", state.csrf);
  const r = await fetch(url, {
    ...options,
    headers,
    credentials: "same-origin",
    signal: options.signal || AbortSignal.timeout(20000),
  });
  if (!r.ok) {
    let d;
    try {
      d = await r.json();
    } catch {
      d = { detail: "Request failed" };
    }
    const detail = Array.isArray(d.detail)
      ? d.detail.map((x) => x.msg).join("; ")
      : d.detail;
    throw new ApiError(
      r.status,
      detail || "Request failed",
      Number(r.headers.get("Retry-After")) || 0,
    );
  }
  if (r.status === 204) return null;
  return r.headers.get("content-type")?.includes("application/json")
    ? r.json()
    : r.blob();
}
function bind(id, fn) {
  let running = false;
  $(id).addEventListener("click", () => {
    if (
      ["screen", "resume-screen", "resume-audio", "start", "submit"].includes(
        id,
      )
    ) {
      if (running) return;
      running = true;
      // Keep getDisplayMedia inside the original click stack.
      guard(fn).finally(() => {
        running = false;
      });
    } else {
      state.action = (state.action || Promise.resolve()).then(() => guard(fn));
    }
  });
}
async function guard(fn) {
  const version = state.messageVersion;
  try {
    await fn();
    if (state.messageErrorAction === fn && state.messageVersion === version)
      message();
  } catch (e) {
    message(e.message, true);
    state.messageErrorAction = fn;
    console.error(e);
  }
}
function node(tag, text, className) {
  const n = document.createElement(tag);
  if (text !== undefined) n.textContent = text;
  if (className) n.className = className;
  return n;
}
function button(text, fn, secondary = false) {
  const b = node("button", text, secondary ? "secondary" : "");
  b.addEventListener("click", async () => {
    if (b.disabled) return;
    b.disabled = true;
    try {
      await guard(fn);
    } finally {
      b.disabled = false;
    }
  });
  return b;
}
function hideViews() {
  for (const id of [
    "login-panel",
    "dashboard",
    "admin-tools",
    "student-session",
    "review",
    "mobile-panel",
  ])
    $(id).hidden = true;
}
function addInterval(fn, ms) {
  const id = setInterval(() => guard(fn), ms);
  state.intervals.push(id);
  return id;
}
function clearIntervals() {
  state.intervals.forEach(clearInterval);
  state.intervals = [];
}
function revokeURLs() {
  for (const url of state.objectURLs) URL.revokeObjectURL(url);
  state.objectURLs = [];
}
function blobURL(blob) {
  const url = URL.createObjectURL(blob);
  state.objectURLs.push(url);
  return url;
}
function svg(name) {
  const element = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  element.setAttribute("class", "icon");
  element.setAttribute("aria-hidden", "true");
  const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
  use.setAttribute("href", "#icon-" + name);
  element.append(use);
  return element;
}
function pill(text, tone = "neutral") {
  return node("span", text, "status-pill " + tone);
}
function setPill(id, text, tone = "neutral") {
  const element = $(id);
  element.textContent = text;
  element.className = "status-pill " + tone;
}
function humanStatus(status) {
  return (
    {
      preflight: "Setup in progress",
      in_progress: "In progress",
      submitted: "Assessment submitted",
      expired: "Time expired",
      terminated: "Ended by staff",
      cancelled: "Cancelled",
    }[status] || status
  );
}
function statusTone(status) {
  return (
    {
      preflight: "pending",
      in_progress: "info",
      submitted: "positive",
      expired: "neutral",
      terminated: "negative",
      cancelled: "neutral",
    }[status] || "neutral"
  );
}
function setShell(view, label) {
  document.body.dataset.view = view;
  $("page-location").textContent = label;
  for (const element of document.querySelectorAll(
    "[data-nav],.workspace-nav .nav-item",
  )) {
    const destination = element.dataset.nav || element.id.replace("nav-", "");
    const active =
      destination === view ||
      (destination === "assessments" &&
        ["dashboard", "session", "review"].includes(view));
    element.classList.toggle("active", active);
    if (active) element.setAttribute("aria-current", "page");
    else element.removeAttribute("aria-current");
  }
}
function renderProfile() {
  if (!state.user) return;
  const name = state.user.name;
  const role =
    { student: "Candidate", admin: "Administrator", proctor: "Proctor" }[
      state.user.role
    ] || state.user.role;
  $("identity").replaceChildren(
    document.createTextNode(name),
    node("small", role),
  );
  $("profile-avatar").textContent = name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((x) => x[0])
    .join("")
    .toUpperCase();
  const organization = state.organization?.name || "Your organization";
  $("organization-name").textContent = organization;
  $("organization-kind").textContent = "Assessment workspace";
  $("nav-manage").hidden = state.user.role !== "admin";
  for (const element of document.querySelectorAll('[data-nav="manage"]'))
    element.hidden = state.user.role !== "admin";
  $("today-label").textContent = new Date().toLocaleDateString(undefined, {
    weekday: "short",
    month: "long",
    day: "numeric",
  });
}
function updateConnection() {
  const el = $("connection-status");
  const online = navigator.onLine;
  el.classList.toggle("offline", !online);
  el.replaceChildren(
    node("i"),
    document.createTextNode(online ? "Connected" : "Offline"),
  );
}
function filterAttempts() {
  const search = $("session-search").value.trim().toLowerCase();
  const status = $("session-status-filter").value;
  const rows = [...$("sessions").querySelectorAll("article[data-session-id]")];
  let visible = 0;
  for (const row of rows) {
    row.hidden = !(
      row.dataset.search.includes(search) &&
      (status === "all" || row.dataset.status === status)
    );
    if (!row.hidden) visible++;
  }
  $("attempt-results").textContent = rows.length
    ? `${visible} of ${rows.length} attempts shown`
    : "No attempts yet";
}
$("session-search").addEventListener("input", filterAttempts);
$("session-status-filter").addEventListener("change", filterAttempts);
async function dashboard(section = "assessments") {
  clearIntervals();
  await stopCapture();
  revokeURLs();
  hideViews();
  message();
  state.session = null;
  $("dashboard").hidden = false;
  $("account-panel").hidden = true;
  const staff = state.user.role !== "student";
  $("dashboard-title").textContent =
    section === "history"
      ? "Session history"
      : staff
        ? "Student reviews"
        : "Your assessments";
  $("dashboard-eyebrow").textContent = staff
    ? "ORGANIZATION WORKSPACE"
    : "YOUR WORKSPACE";
  $("dashboard-description").textContent =
    section === "history"
      ? "Review attempts, saved responses, and assessment status."
      : staff
        ? "Review student identity, answers, recordings and assessment incidents."
        : "Prepare, take your next assessment, and keep track of your progress.";
  setShell(
    section === "assessments" ? "dashboard" : section,
    section === "history" ? "Session history" : "Assessments",
  );
  renderProfile();
  window.scrollTo(0, 0);
  const [exams, sessions] = await Promise.all([
    api("/api/exams"),
    api("/api/sessions"),
  ]);
  state.exams = exams;
  $("assessment-list-description").textContent = staff
    ? "Your organization’s assessments. Open a student attempt below to review."
    : "Choose an assessment to view the requirements and begin setup.";
  $("session-search").placeholder = staff
    ? "Search by student or assessment"
    : "Search by assessment";
  $("sessions-heading").textContent = staff
    ? "Student attempts"
    : "Your attempts";
  $("metric-assessment-label").textContent = staff
    ? "Assessments"
    : "Assigned assessments";
  $("session-search").value = "";
  $("session-status-filter").value = "all";
  const visibleExams = exams.filter(
    (exam) =>
      !(
        staff &&
        !exam.active &&
        exam.name === "Proctix device and review demonstration"
      ),
  );
  $("metric-assessments").textContent = visibleExams.length;
  $("metric-active").textContent = sessions.filter(
    (x) => x.status === "in_progress",
  ).length;
  $("metric-completed").textContent = sessions.filter(
    (x) => x.status === "submitted",
  ).length;
  $("assessment-count").textContent =
    visibleExams.length +
    " " +
    (visibleExams.length === 1 ? "assessment" : "assessments");
  $("assessments-area").hidden = section === "history";
  $("exams").replaceChildren();
  $("sessions").replaceChildren();
  for (const exam of visibleExams) {
    const card = node("article", undefined, "card assessment-card");
    const top = node("div", undefined, "assessment-card-top");
    const mark = node("span", undefined, "section-icon lavender");
    mark.append(svg(exam.kind === "interview" ? "user" : "file"));
    top.append(mark, node("span", exam.kind, "type-tag"));
    card.append(
      top,
      node("h2", exam.name),
      node(
        "p",
        exam.description ||
          "Complete setup to review the requirements for this assessment.",
        "assessment-description",
      ),
    );
    const meta = node("div", undefined, "assessment-meta");
    const time = node("span");
    time.append(
      svg("clock"),
      document.createTextNode(exam.duration_minutes + " minutes"),
    );
    const mode = node("span");
    mode.append(
      svg(exam.policy.require_seb ? "shield" : "user"),
      document.createTextNode(
        exam.policy.require_seb ? "Lockdown required" : "Proctored assessment",
      ),
    );
    meta.append(time, mode);
    card.append(meta);
    const bottom = node("div", undefined, "assessment-card-bottom");
    bottom.append(
      node(
        "span",
        exam.active
          ? "Available to " + (staff ? "assigned candidates" : "begin")
          : "Inactive",
      ),
    );
    if (!staff) {
      const open = button("Open assessment", async () =>
        openSession(
          await api("/api/sessions", {
            method: "POST",
            body: { exam_id: exam.id, fresh_start: true },
          }),
        ),
      );
      open.className = "primary";
      bottom.append(open);
    } else
      bottom.append(
        pill(
          exam.active ? "Active" : "Inactive",
          exam.active ? "positive" : "neutral",
        ),
      );
    card.append(bottom);
    $("exams").append(card);
  }
  if (!visibleExams.length) {
    const empty = node("div", undefined, "empty-state");
    empty.append(
      svg("file"),
      node("h3", staff ? "No assessments yet" : "You're all caught up"),
      node(
        "p",
        staff
          ? "Create an assessment in Administration to get started."
          : "Assessments assigned by your organizer will appear here.",
      ),
    );
    $("exams").append(empty);
  }
  for (const session of sessions) {
    const card = node("article", undefined, "card session-row");
    const exam = exams.find((e) => e.id === session.exam_id);
    const title = node("div");
    title.append(
      node(
        "strong",
        staff
          ? session.student_name || "Student"
          : exam?.name || "Assessment session",
      ),
      node(
        "small",
        new Date(session.created_at * 1000).toLocaleString(undefined, {
          month: "short",
          day: "numeric",
          hour: "2-digit",
          minute: "2-digit",
        }) +
          (staff
            ? ` · ${exam?.name || "Assessment"}`
            : " · " + session.id.slice(0, 8)),
      ),
    );
    title.append(
      node(
        "p",
        staff
          ? `Student number: ${session.student_number || "Not provided"}`
          : `${session.student_name || session.user_id} · Student ID: ${session.student_number || session.student_id || session.user_id}`,
      ),
    );
    title.dataset.sessionId = session.id;
    title.classList.add("session-person");
    const avatar = node(
      "span",
      (session.student_name || "Student")
        .split(/\s+/)
        .filter(Boolean)
        .slice(0, 2)
        .map((part) => part[0])
        .join("")
        .toUpperCase(),
      "attempt-avatar",
    );
    avatar.setAttribute("aria-hidden", "true");
    title.prepend(avatar);
    card.dataset.status = session.status;
    card.dataset.search =
      `${session.student_name || ""} ${session.student_number || ""} ${exam?.name || ""}`.toLowerCase();
    const status = node("div");
    status.append(
      pill(humanStatus(session.status), statusTone(session.status)),
      node(
        "p",
        session.policy.verification_mode === "institution_account"
          ? "Assigned-account admission · evidence review is separate"
          : {
              pending: "Identity not verified",
              pending_review: "Identity review requested",
              approved: "Identity approved",
              rejected: "Identity needs attention",
            }[session.verification] || "Identity review",
      ),
    );
    const action = button(staff ? "Review session" : "Open session", () =>
      staff ? openReview(session.id) : loadSession(session.id),
    );
    action.className = "secondary";
    card.append(title, status, action);
    card.dataset.sessionId = session.id;
    $("sessions").append(card);
  }
  if (!sessions.length) {
    const empty = node("div", undefined, "empty-state");
    empty.append(
      svg("clock"),
      node("h3", "No sessions yet"),
      node(
        "p",
        "Your assessment activity will appear here once you get started.",
      ),
    );
    $("sessions").append(empty);
  }
  if (state.user.role === "admin") {
    const users = await api("/api/users");
    $("enrollment-user").replaceChildren(
      ...users
        .filter((u) => u.role === "student" && u.active)
        .map((u) => {
          const option = node(
            "option",
            `${u.name} · ${u.student_number || u.email}`,
          );
          option.value = u.id;
          return option;
        }),
    );
    $("assignment-exam").replaceChildren();
    $("assignment-user").replaceChildren();
    for (const exam of exams) {
      const option = node("option", exam.name);
      option.value = exam.id;
      $("assignment-exam").append(option);
    }
    for (const user of users.filter((x) => x.role === "student" && x.active)) {
      const option = node("option", user.name + " (" + user.email + ")");
      option.value = user.id;
      $("assignment-user").append(option);
    }
  }
  filterAttempts();
}
async function navigateWorkspace(destination) {
  if (
    state.session?.status === "in_progress" &&
    !confirm(
      "Leaving this page pauses device capture. Your assessment deadline will continue. Return to the workspace?",
    )
  )
    return;
  await dashboard(destination === "history" ? "history" : "assessments");
  if (destination === "manage") {
    if (state.user.role !== "admin") return;
    $("dashboard").hidden = true;
    $("admin-tools").hidden = false;
    setShell("manage", "Administration");
  }
  if (destination === "account") {
    $("dashboard").hidden = true;
    $("account-panel").hidden = false;
    $("account-panel").open = true;
    setShell("account", "Account settings");
  }
}
function selectSetupStep(step) {
  if (!state.session || state.session.status !== "preflight") return;
  if (step > 1 && !state.session.consent_at) {
    message("Accept the assessment notice before continuing.", true);
    return;
  }
  state.preflightStep = step;
  renderSetup();
  const target = step === 5 ? $("enrollment-card") : $("setup-step-" + step);
  target.scrollIntoView({ behavior: "smooth", block: "start" });
  target.setAttribute("tabindex", "-1");
  target.focus({ preventScroll: true });
  message();
}
function liveDeviceState() {
  return {
    camera:
      !!state.camera?.getVideoTracks().some((t) => t.readyState === "live") &&
      $("camera-preview").videoWidth > 0 &&
      $("camera-preview").readyState >= 2,
    audio: !!state.camera
      ?.getAudioTracks()
      .some((t) => t.readyState === "live"),
    screen:
      state.screenReady !== false &&
      !!state.screen?.getVideoTracks().some((t) => t.readyState === "live"),
  };
}
function devicesReady() {
  const live = liveDeviceState();
  return ["camera", "audio", "screen"].every(
    (k) => !state.session.policy["require_" + k] || live[k],
  );
}
function enrollmentReady() {
  return !state.enrollmentEdited && !!state.session?.enrollment_confirmed;
}

function renderSetup() {
  const session = state.session;
  if (!session) return;
  $("consent").disabled = false;
  $("consent").replaceChildren(
    document.createTextNode(
      session.consent_at ? "Continue to device check" : "Accept & continue",
    ),
    svg("arrow"),
  );
  const preflight = session.status === "preflight";
  $("preflight-steps").hidden = !preflight;
  const accountAuthorized =
    session.policy.verification_mode === "institution_account";
  $("identity-instructions").textContent = accountAuthorized
    ? "Add your ID photo and take a quick selfie."
    : "Add your ID photo and a selfie for your proctor to review.";
  $("control-note").textContent = accountAuthorized
    ? "Complete setup, then select Begin assessment."
    : "Your assessment starts only after the required checks and identity review are complete.";
  $("verify").textContent = accountAuthorized
    ? "Run image checks again"
    : "Request identity review";
  const reviewed = ["pending_review", "approved", "rejected"].includes(
    session.verification,
  );
  const step = state.preflightStep || (session.consent_at ? 5 : 1);
  $("student-session").classList.toggle(
    "identity-photo-step",
    preflight && step === 3,
  );
  $("student-session").classList.toggle(
    "device-setup-step",
    preflight && step === 2,
  );
  $("preflight-steps").hidden = !preflight;
  for (const number of [1, 2, 3, 4])
    $("setup-step-" + number).hidden = step !== number;
  $("enrollment-card").hidden = step !== 5;
  for (const button of document.querySelectorAll("[data-step]")) {
    button.classList.toggle("current", Number(button.dataset.step) === step);
    if (Number(button.dataset.step) === step)
      button.setAttribute("aria-current", "step");
    else button.removeAttribute("aria-current");
  }
  $("setup-summary").hidden = !preflight || step !== 4;
  const uploaded = state.identityEvidence || {};
  const readyPhotos =
    Number(!!uploaded.id) + Number(!!uploaded.selfie && !state.selfieDraft);
  $("photo-ready-count").textContent = `${readyPhotos} / 2`;
  $("id-photo-card").dataset.complete = String(!!uploaded.id);
  $("selfie-photo-card").dataset.complete = String(
    !!uploaded.selfie && !state.selfieDraft,
  );
  $("photo-comparison-panel").hidden = !(uploaded.id && uploaded.selfie);
  $("selfie-live-stage").hidden = !!uploaded.selfie || !!state.selfieDraft;
  const livePreview = $("identity-live-preview");
  if (livePreview.srcObject !== state.camera)
    livePreview.srcObject = state.camera;
  $("selfie-camera-placeholder").hidden = !!state.camera?.active;
  $("verify").hidden = !(uploaded.id && uploaded.selfie);
  $("verify-help").hidden = !(uploaded.id && uploaded.selfie);
  const presentation = comparisonPresentation(state.photoComparison);
  $("comparison-title").textContent = state.comparing
    ? "Comparing photos…"
    : state.selfieDraft
      ? "Confirm your selfie"
      : uploaded.id && uploaded.selfie
        ? presentation.title
        : "Add your ID and selfie";
  $("comparison-result").textContent = state.comparing
    ? "Please wait."
    : state.selfieDraft
      ? "Select Use this selfie to run the comparison."
      : uploaded.id && uploaded.selfie
        ? presentation.message
        : "Both photos are needed for comparison.";
  $("final-face-result").textContent = $("comparison-title").textContent;
  $("final-face-note").textContent =
    state.photoComparison?.status === "compared"
      ? state.photoComparison.quality_warning
        ? "Estimated result · clearer photos are recommended."
        : "Estimated result · not proof of identity or ID authenticity."
      : $("comparison-result").textContent;
  $("id-saved-preview").hidden = !uploaded.id;
  $("selfie-preview-panel").hidden = !uploaded.selfie && !state.selfieDraft;
  $("confirm-selfie").hidden = !state.selfieDraft;
  $("selfie").hidden = !!uploaded.selfie || !!state.selfieDraft;
  $("selfie-instructions").hidden = !!uploaded.selfie;
  $("selfie-draft-help").hidden = !state.selfieDraft;
  $("selfie-draft-help").textContent = state.selfieDraft
    ? "Preview only. Select Use this selfie to save and check it."
    : "Saved selfie for this attempt. Select Retake selfie to replace it.";
  $("selfie-quality").hidden =
    !uploaded.selfie ||
    !!state.selfieDraft ||
    !!(
      state.identityQuality?.selfie?.comparison_usable ??
      state.identityQuality?.selfie?.quality_ok
    );
  $("id-quality").hidden =
    !uploaded.id ||
    !!(
      state.identityQuality?.id?.comparison_usable ??
      state.identityQuality?.id?.quality_ok
    );
  $("comparison-result").hidden = !!state.selfieDraft || !!state.idUploading;
  $("comparison-details").hidden =
    state.photoComparison?.status !== "compared" ||
    !!state.selfieDraft ||
    !!state.idUploading;
  $("comparison-metrics").textContent =
    state.photoComparison?.status === "compared"
      ? `${state.photoComparison.model || "Local model"} · similarity ${state.photoComparison.cosine_similarity}. This is not a probability, proof of authenticity, or a liveness check.`
      : "";
  const guidedLimit = accountAuthorized && session.verification_attempts >= 5;
  $("photo-retry").hidden =
    !accountAuthorized ||
    (!state.photoComparison && !guidedLimit) ||
    (state.photoComparison?.status === "compared" &&
      !photoNeedsRetry(state.photoComparison)) ||
    !!state.selfieDraft ||
    !!state.idUploading;
  $("photo-retry-message").textContent = photoRetryMessage(
    state.photoComparison || (guidedLimit ? {} : null),
    session.verification_attempts,
  );
  $("photos-next").textContent =
    accountAuthorized && photoNeedsRetry(state.photoComparison)
      ? "Use account verification instead"
      : "Next: final check";
  $("alternative-verification-note").hidden =
    !accountAuthorized || !photoNeedsRetry(state.photoComparison);
  $("retry-id").disabled = !!state.idUploading;
  $("retry-selfie").disabled = !!state.comparing;

  $("saved-evidence-note").hidden = true;
  $("saved-evidence-note").textContent =
    `Saved to this attempt: ${[uploaded.id ? "ID photo" : "", uploaded.selfie ? "selfie" : ""].filter(Boolean).join(" and ")}. Replace either image below.`;
  $("id-photo-card")
    .querySelector(".upload-zone strong")
    ?.replaceChildren(
      document.createTextNode(
        uploaded.id ? "Replace your official ID" : "Upload your official ID",
      ),
    );
  const idCheck = state.identityQuality?.id;
  const idNeedsReplacement =
    !!uploaded.id &&
    idCheck?.status === "assessed" &&
    !(idCheck.comparison_usable ?? idCheck.quality_ok);
  setPill(
    "id-upload-status",
    state.idUploading
      ? "Uploading…"
      : idNeedsReplacement
        ? "Replace this ID photo"
        : uploaded.id
          ? "Saved for this attempt"
          : "Add your ID photo",
    state.idUploading ? "pending" : idNeedsReplacement ? "pending" : "neutral",
  );
  setPill(
    "selfie-upload-status",
    state.selfieDraft
      ? "New preview · not saved"
      : uploaded.selfie
        ? "Selfie uploaded successfully"
        : "Take your selfie",
    state.selfieDraft ? "pending" : uploaded.selfie ? "positive" : "neutral",
  );
  const needed = [];
  if (!session.consent_at) needed.push("Accept the assessment notice.");
  else {
    const live = liveDeviceState();
    if (accountAuthorized && !enrollmentReady())
      needed.push("Select Confirm & next to continue.");
    if (state.selfieDraft)
      needed.push("Confirm your new selfie preview or retake it.");
    if (state.comparing)
      needed.push("Wait for the current image checks to finish.");
    if (session.policy.require_camera && !live.camera)
      needed.push("Connect your camera and wait for a live picture.");
    if (session.policy.require_audio && !live.audio)
      needed.push("Allow microphone access.");
    if (session.policy.require_screen && !live.screen)
      needed.push("Share your entire display.");
    if (state.idUploading && uploaded.id)
      needed.push("Wait for the replacement ID image to finish uploading.");
    if ($("id-upload-error").textContent)
      needed.push(
        "Resolve the ID upload error by choosing a valid image and retrying.",
      );
    if (!uploaded.id)
      needed.push(
        state.idUploading
          ? "Wait for your ID image to finish uploading."
          : "Choose an ID image. It uploads automatically.",
      );
    if (!uploaded.selfie) needed.push("Take a camera selfie.");
    if (session.verification !== "approved")
      for (const kind of ["id", "selfie"]) {
        const quality = state.identityQuality?.[kind];
        if (quality?.status === "assessed" && quality.face_count === 0)
          needed.push(
            kind === "id"
              ? "Replace the ID image with the FRONT showing its portrait, or contact your organizer for an alternative photo check."
              : "Retake your selfie with your face visible, or contact your organizer for an alternative photo check.",
          );
      }
    if (session.policy.require_mobile && !session.mobile_connected)
      needed.push("Pair and activate your phone camera.");
    if (uploaded.id && uploaded.selfie && !reviewed && !accountAuthorized)
      needed.push("Select Request identity review.");
    if (session.verification === "pending_review" && !accountAuthorized)
      needed.push("Wait for your proctor to approve your identity.");
    if (session.verification === "rejected")
      needed.push(
        "Your proctor rejected the identity check. Contact your organizer or submit new images.",
      );
  }
  $("missing-items").replaceChildren(...needed.map((text) => node("li", text)));
  $("setup-summary-text").textContent =
    needed[0] || "All required checks are complete. You can begin.";
  $("setup-next-action").hidden = !needed.length;
  $("remaining-checks").hidden = needed.length <= 1;
  $("setup-next-action").textContent = needed.length
    ? "Go to the next required check"
    : "Review and begin";
  $("photos-next").disabled =
    !uploaded.id ||
    !uploaded.selfie ||
    !!state.selfieDraft ||
    !!state.idUploading ||
    !!state.comparing;
  $("id-file").disabled = !!state.idUploading;
  $("upload-id").hidden = !$("id-upload-error").textContent;
  $("verify").disabled =
    guidedLimit ||
    !!state.selfieDraft ||
    state.comparing ||
    state.idUploading ||
    !uploaded.id ||
    !uploaded.selfie ||
    (!accountAuthorized &&
      (session.verification === "pending_review" ||
        session.verification === "approved"));
  const checkHelp = guidedLimit
    ? "5 of 5 checks used. The retry limit has been reached for this attempt."
    : state.comparing
      ? "Image checks are running. Please wait."
      : state.idUploading
        ? "Wait for the ID upload to finish."
        : state.selfieDraft
          ? "Select Use this selfie before running checks."
          : !uploaded.id || !uploaded.selfie
            ? "Save both your ID photo and selfie first."
            : !accountAuthorized && session.verification === "pending_review"
              ? "Your identity review is pending."
              : !accountAuthorized && session.verification === "approved"
                ? "Identity review is complete."
                : accountAuthorized
                  ? `${session.verification_attempts || 0} of 5 image checks used.`
                  : "";
  $("verify-help").textContent = checkHelp;
  $("restart-setup").hidden = session.status !== "preflight";
  $("restart-setup").disabled = !!state.restartingSetup;
  $("verify").setAttribute("aria-describedby", "verify-help");
  $("verify").title = $("verify").disabled ? checkHelp : "";
  if (guidedLimit) $("verify").textContent = "Image check limit reached";
  $("verification-state").textContent =
    accountAuthorized && uploaded.id && uploaded.selfie
      ? state.selfieDraft
        ? "Confirm the new selfie preview before continuing."
        : idNeedsReplacement
          ? "Replace the saved ID photo using its portrait side."
          : "Both photos are saved."
      : uploaded.id && uploaded.selfie
        ? reviewed
          ? "Both images are uploaded. Your proctor will review them."
          : "Both images are uploaded. Select Request identity review."
        : !uploaded.id && uploaded.selfie
          ? "Your selfie is saved. Choose an ID image to finish."
          : uploaded.id
            ? "Your ID image is saved. Take a selfie to finish."
            : "Choose your ID image and take a selfie.";
  $("verification-state").hidden = !!uploaded.id && !!uploaded.selfie;
  const approved =
    session.verification === "approved" ||
    (accountAuthorized && uploaded.id && uploaded.selfie);
  const rejected = session.verification === "rejected";
  $("ready-title").textContent = needed.length
    ? "Finish the missing setup checks"
    : approved
      ? accountAuthorized
        ? "Review before you begin"
        : "You're ready to begin."
      : rejected
        ? "Your identity needs another look"
        : "Waiting for your proctor";
  $("ready-description").textContent = accountAuthorized
    ? needed.length
      ? "Complete the remaining checks to continue."
      : "Review the actual status of your photos and devices below. Your timer starts when you select Begin assessment."
    : approved
      ? "Your identity has been approved. Keep your required devices connected, then begin when you're ready."
      : rejected
        ? "Your proctor couldn't approve these documents. Review your identity details or contact your organizer for help."
        : session.verification === "pending_review"
          ? "Your photos were sent for staff review. This page updates when a decision is recorded."
          : "Request staff review of your photos before beginning.";
  $("ready-symbol").classList.toggle("approved", approved);
  $("ready-symbol").replaceChildren(
    svg(session.verification === "approved" ? "check" : "clock"),
  );
  const savedId = !!uploaded.id && !!state.identityEvidenceIds?.id;
  const savedSelfie = !!uploaded.selfie && !!state.identityEvidenceIds?.selfie;
  const details = session.submitted_details || {};
  const hasEnteredDetails = !!(
    details.name?.trim() || details.reference?.trim()
  );
  const checks = [
    {
      key: "details",
      label: "Your details",
      status: enrollmentReady()
        ? hasEnteredDetails
          ? "Saved as entered"
          : "Skipped"
        : "Not saved",
      tone: "neutral",
      note: "Self-reported details; not an identity verification.",
    },
    {
      key: "notice",
      label: "Assessment notice",
      status: session.consent_at ? "Accepted" : "Not accepted",
      tone: session.consent_at ? "done" : "pending",
    },
    {
      key: "id",
      label: "ID photo",
      status: savedId ? "Saved" : "Not uploaded",
      tone: savedId ? "done" : "pending",
    },
    {
      key: "selfie",
      label: "Selfie",
      status: state.selfieDraft
        ? "Preview only — not saved"
        : savedSelfie
          ? "Saved"
          : "Not uploaded",
      tone: savedSelfie && !state.selfieDraft ? "done" : "pending",
    },
  ];
  const live = liveDeviceState();
  for (const [kind, label] of [
    ["camera", "Camera"],
    ["audio", "Microphone"],
    ["screen", "Display sharing"],
  ]) {
    const required = !!session.policy["require_" + kind];
    const tracks =
      kind === "audio"
        ? state.camera?.getAudioTracks()
        : kind === "camera"
          ? state.camera?.getVideoTracks()
          : state.screen?.getVideoTracks();
    const active =
      !!live[kind] && !!tracks?.some((track) => track.enabled && !track.muted);
    const entireDisplay =
      kind !== "screen" ||
      state.screen?.getVideoTracks()[0]?.getSettings().displaySurface ===
        "monitor";
    const complete = active && entireDisplay;
    checks.push({
      key: kind,
      label,
      status: !required
        ? active
          ? "Connected · not required"
          : "Not required"
        : !active
          ? "Not connected"
          : !entireDisplay
            ? "Entire display not shared"
            : "Connected",
      tone: !required ? "neutral" : complete ? "done" : "pending",
    });
  }
  if (session.policy.require_mobile)
    checks.push({
      key: "mobile",
      label: "Secondary camera",
      status: session.mobile_connected ? "Connected" : "Not connected",
      tone: session.mobile_connected ? "done" : "pending",
    });
  checks.push({
    key: "staff-review",
    label: "Staff identity review",
    status:
      session.verification === "approved"
        ? "Approved by staff"
        : accountAuthorized
          ? "Not required for this assessment"
          : session.verification === "rejected"
            ? "Rejected by staff"
            : session.verification === "pending_review"
              ? "Awaiting staff review"
              : "Not requested",
    tone:
      session.verification === "approved"
        ? "done"
        : accountAuthorized
          ? "neutral"
          : "pending",
  });
  $("readiness-list").replaceChildren();
  for (const check of checks) {
    const row = node("li", undefined, `readiness-row ${check.tone}`);
    row.dataset.check = check.key;
    const label = node("div", undefined, "readiness-label");
    label.append(node("strong", check.label));
    if (check.note) label.append(node("small", check.note));
    row.append(
      node(
        "span",
        check.tone === "done" ? "✓" : check.tone === "pending" ? "!" : "—",
        "check-indicator",
      ),
      label,
      node("span", check.status, `readiness-status ${check.tone}`),
    );
    $("readiness-list").append(row);
  }
  const photoComplete =
    session.verification === "approved" ||
    !["id", "selfie"].some(
      (kind) =>
        state.identityQuality?.[kind]?.status === "assessed" &&
        state.identityQuality[kind].face_count === 0,
    );
  const ready =
    photoComplete &&
    approved &&
    devicesReady() &&
    (!session.policy.require_mobile || session.mobile_connected);
  $("start").disabled = !ready || needed.length > 0 || !!state.selfieDraft;
  $("readiness-list").hidden = false;
  $("ready-symbol").classList.toggle("approved", ready && needed.length === 0);
  $("start").hidden = !approved;
  $("check-approval").hidden = accountAuthorized || approved;
  $("approval-status").hidden = accountAuthorized;
  $("approval-status").textContent = state.approvalCheckedAt
    ? "Last checked: " +
      new Date(state.approvalCheckedAt).toLocaleTimeString() +
      ". Checks automatically every 5 seconds."
    : "Checks automatically every 5 seconds.";
  const pairingAvailable = state.capabilities?.phone_pairing !== false;
  $("pair").disabled = !pairingAvailable;
  $("phone-local-help").hidden = pairingAvailable;
  const demo = state.capabilities?.phone_demo_url;
  $("phone-demo-link").hidden = !demo;
  if (demo) $("phone-demo-link").href = demo;

  $("start-help").textContent = !approved
    ? "Your timer will stay paused until you begin."
    : !devicesReady()
      ? "Reconnect your required devices in Device check."
      : session.policy.require_mobile && !session.mobile_connected
        ? "Activate your confirmed secondary camera before starting."
        : "Your timer starts when you select Begin assessment.";
  const cameraRequired = !!session.policy.require_camera;
  const audioRequired = !!session.policy.require_audio;
  $("camera-requirement").textContent =
    cameraRequired && audioRequired
      ? "Required · keep your camera and microphone connected."
      : cameraRequired
        ? "Camera required · live microphone monitoring is not required."
        : audioRequired
          ? "Microphone required · the camera is used for your selfie."
          : "For your selfie · live camera and microphone monitoring are not required.";
  $("screen-requirement").textContent = session.policy.require_screen
    ? "Required · share your entire display, rather than a tab or window."
    : "Not required for this assessment.";
  const currentDevices = liveDeviceState();
  const cameraGroupRequired = cameraRequired || audioRequired;
  const cameraGroupReady =
    (!cameraRequired || currentDevices.camera) &&
    (!audioRequired || currentDevices.audio);
  const requiredGroups =
    Number(cameraGroupRequired) + Number(!!session.policy.require_screen);
  const readyGroups =
    Number(cameraGroupRequired && cameraGroupReady) +
    Number(!!session.policy.require_screen && currentDevices.screen);
  $("device-ready-count").textContent = requiredGroups
    ? `${readyGroups} / ${requiredGroups}`
    : "Preview";
  $("device-ready-label").textContent = requiredGroups
    ? "required connections"
    : "no live devices required";
  $("devices-next").disabled = !devicesReady();
  $("camera-device-card").dataset.connected = String(!!currentDevices.camera);
  $("display-device-card").dataset.connected = String(!!currentDevices.screen);
  $("display-device-placeholder").hidden = !!currentDevices.screen;
  $("mobile-requirement").textContent = session.policy.require_mobile
    ? "Required"
    : "Optional";
}
async function loadSession(id) {
  await openSession(await api("/api/sessions/" + id));
}
async function openSession(session) {
  clearIntervals();
  await stopCapture();
  hideViews();
  state.session = session;
  message();
  for (const id of [
    "id-upload-error",
    "selfie-error",
    "camera-error",
    "screen-error",
  ])
    $(id).textContent = "";
  $("id-file").value = "";
  $("screen-progress").textContent = "";
  for (const id of ["screen", "resume-screen"]) {
    $(id).disabled = false;
    $(id).textContent =
      id === "screen"
        ? "Share entire display"
        : "Re-enable entire display capture";
  }
  state.selfieDraft = null;
  state.photoComparison = null;
  state.documentChecks = null;
  state.documentBackChecks = null;
  state.idUploading = false;
  state.identityUploadPromise = null;
  state.comparing = false;
  state.identityEpoch = 0;
  state.lastCompareEpoch = null;
  state.enrollmentEdited = false;
  $("selfie-preview-panel").hidden = true;
  $("comparison-result").textContent = "";
  $("demo-details-label").hidden = !state.capabilities?.demo_details;
  const demoProfile = session.enrollment || state.user;
  const demoReference =
    demoProfile.student_number || demoProfile.email || state.user.email;
  $("demo-details").replaceChildren(
    new Option("Choose sample details…", ""),
    new Option(`${demoProfile.name} · ${demoReference}`, "sample"),
  );
  $("enrollment-name").value = session.submitted_details?.name || "";
  $("enrollment-reference").value = session.submitted_details?.reference || "";
  state.enrollmentEdited = !session.enrollment_confirmed;
  $("enrollment-result").textContent = session.enrollment_confirmed
    ? "Your details were saved as entered."
    : "Enter any name and student number or email, then select Confirm & next.";
  $("id-saved-preview").hidden = !session.identity_evidence?.id;
  if (session.identity_evidence?.id)
    $("id-saved-preview").src =
      `/api/sessions/${session.id}/identity-images/id`;
  if (session.identity_evidence?.selfie) {
    $("selfie-still-preview").src =
      `/api/sessions/${session.id}/identity-images/selfie`;
    $("selfie-preview-panel").hidden = false;
    $("confirm-selfie").hidden = true;
    $("selfie-draft-help").textContent =
      "Your saved selfie. Retake it if needed.";
  }
  state.revision = session.answer_revision;
  state.dirty = false;
  state.stopping = false;
  state.identityEvidence = session.identity_evidence || {};
  state.identityEvidenceIds = session.identity_evidence_ids || {};
  state.identityQuality = {};
  for (const [kind, prompt] of [
    ["id", "Upload an ID image to run photo checks."],
    ["selfie", "Capture and confirm a selfie to run photo checks."],
  ]) {
    const element = $(kind + "-quality");
    element.classList.remove("field-error");
    element.textContent = state.identityEvidence[kind]
      ? "Loading checks for your saved image…"
      : prompt;
  }
  state.preflightStep = session.consent_at ? 5 : 1;
  setShell("session", "Assessment setup");
  const exam = state.exams?.find((x) => x.id === session.exam_id);
  $("assessment-context").textContent =
    exam?.name || "Complete the checks below before your assessment begins.";
  $("mobile-setup").open = !!session.policy.require_mobile;
  $("account-panel").hidden = true;

  window.scrollTo(0, 0);
  $("pair-info").hidden = true;
  state.queue = await new EvidenceQueue(
    "student:" + state.user.id + ":" + session.id,
  ).init();
  state.queue.onstatus = (n, bytes) => {
    if (state.experience) {
      state.experience.stats.pending = n;
      state.experience.stats.pendingBytes = bytes;
      state.experience.adapt();
    }
    $("sync-indicator").classList.toggle("pending", n > 0);
    $("queue-state").textContent = n
      ? `${n} encrypted evidence item(s) awaiting upload (${(bytes / 1048576).toFixed(1)} MB). Keep this page open.`
      : "All evidence is synchronized.";
  };
  state.gaze = new GazeController({
    state: () => state,
    event: (...args) => guard(() => event(...args)),
  });
  state.experience = new MonitoringExperience({
    state: () => state,
    api,
    identityUpdated: renderSetup,
    submit: () => submitAssessment(true),
    event: (...args) => guard(() => event(...args)),
    behavior: async (data) => {
      await state.queue.enqueue(
        `/api/sessions/${state.session.id}/behavior`,
        new Blob([JSON.stringify(data)], { type: "application/json" }),
      );
      await flushQueue();
    },
    adapt: adaptCapture,
  });
  await state.queue.report();
  $("notice").textContent =
    session.policy.notice +
    (session.policy.enable_face_comparison
      ? " During the assessment, clear live camera samples are also compared with your saved pre-exam selfie for reviewable consistency observations. This does not establish liveness or misconduct."
      : "");
  $("notice").textContent +=
    " Local eye/head-direction checks start automatically with the exam; no calibration is required. Sustained looking-away estimates may be recorded for review. Optional calibration enables screen-position estimates. These checks do not establish misconduct.";
  if (session.policy.require_fullscreen)
    $("notice").textContent +=
      " Leaving fullscreen or resizing the assessment window gives you 5 seconds to return. If fullscreen is not restored, your assessment is automatically submitted with your current answers.";
  $("retention-notice").textContent =
    `Evidence and saved answers are scheduled for deletion ${session.policy.retention_days} ${session.policy.retention_days === 1 ? "day" : "days"} after this attempt ends.`;
  $("consent-check").checked = Boolean(session.consent_at);
  $("consent-check").disabled = Boolean(session.consent_at);
  $("consent").disabled = Boolean(session.consent_at);
  renderSession();
  renderQuestions(session);
  $("student-session").hidden = false;
  const pending = await state.queue.readSnapshot();
  if (pending && !terminal.has(session.status)) {
    if (pending.baseRevision === session.answer_revision) {
      fillAnswers(pending.answers);
      state.dirty = true;
      message(
        "Recovered encrypted unsaved answers. Re-enable capture and synchronize before submitting.",
      );
    } else
      message(
        "An older local answer copy conflicts with saved server answers. Contact your proctor before editing.",
        true,
      );
  }
  state.serverOffset = session.server_time * 1000 - Date.now();
  updateTimer();
  addInterval(updateTimer, 1000);
  addInterval(async () => {
    if (state.submitting || state.session?.id !== session.id) return;
    const currentSession = state.session;
    const evidenceEpoch = state.identityEpoch;
    const row = await api("/api/sessions/" + session.id);
    if (
      state.session !== currentSession ||
      state.session?.id !== session.id ||
      state.identityEpoch !== evidenceEpoch ||
      state.submitting ||
      (terminal.has(state.session.status) && !terminal.has(row.status))
    )
      return;
    state.session = row;
    state.approvalCheckedAt = Date.now();
    if (
      row.identity_evidence &&
      evidenceEpoch === state.identityEpoch &&
      !state.idUploading
    ) {
      state.identityEvidenceIds = row.identity_evidence_ids || {};
      state.identityEvidence = {
        id: !!row.identity_evidence.id,
        selfie: !!row.identity_evidence.selfie,
      };
    }
    state.serverOffset = row.server_time * 1000 - Date.now();
    renderSession();
    if (terminal.has(row.status)) {
      await stopCapture();
      clearIntervals();
    }
  }, 5000);
  addInterval(async () => {
    if (!terminal.has(state.session.status)) {
      await heartbeat();
      await flushQueue();
      if (state.dirty) await saveAnswers();
    }
  }, 10000);
}
function organizeSetup(session) {
  const setup = session.status === "preflight";
  const workspace = document.querySelector(".session-workspace");
  workspace.classList.toggle("setup-layout", setup);
  const observations = $("assessment-observations");
  const aside = document.querySelector(".session-aside");
  if (observations.parentElement !== aside) aside.prepend(observations);
  const form = $("preflight");
  if ($("enrollment-card").nextElementSibling !== $("setup-step-2"))
    form.insertBefore($("enrollment-card"), $("setup-step-2"));
  const target = setup
    ? $("setup-device-previews")
    : document.querySelector(".session-aside");
  const cameraCard = document.querySelector(".preview-card");
  const cameraTarget = setup ? $("camera-device-preview") : cameraCard;
  if ($("camera-frame").parentElement !== cameraTarget) {
    if (setup) cameraTarget.append($("camera-frame"));
    else cameraCard.insertBefore($("camera-frame"), $("face-live-status"));
  }
  const displayTarget = setup
    ? $("display-device-preview")
    : document.querySelector(".session-aside");
  if ($("display-preview").parentElement !== displayTarget)
    displayTarget.append($("display-preview"));
  cameraCard.querySelector("h2").textContent = setup
    ? "Camera settings"
    : "Camera preview";
  for (const element of [cameraCard, $("mobile-setup")]) {
    if (element.parentElement !== target) target.append(element);
  }
  if (setup && session.policy.require_mobile)
    $("advanced-device-settings").open = true;
  if (state.camera && $("camera-preview").paused)
    void $("camera-preview")
      .play()
      .catch(() => {});
  if (state.screenVideo?.paused && state.screen)
    void state.screenVideo.play().catch(() => {});
}
$("setup-next-action").addEventListener("click", () => {
  const session = state.session;
  if (!session) return;
  let target;
  const live = liveDeviceState();
  if (!session.consent_at) target = $("consent-check");
  else if (
    session.policy.verification_mode === "institution_account" &&
    !enrollmentReady()
  )
    target = $("enrollment-name");
  else if (
    (session.policy.require_camera && !live.camera) ||
    (session.policy.require_audio && !live.audio)
  )
    target = $("camera");
  else if (session.policy.require_screen && !live.screen) target = $("screen");
  else if (
    !state.identityEvidence?.id ||
    (state.identityQuality?.id?.status === "assessed" &&
      !state.identityQuality.id.quality_ok)
  ) {
    selectSetupStep(3);
    $("id-file").click();
    return;
  } else if (state.selfieDraft) target = $("confirm-selfie");
  else if (!state.identityEvidence?.selfie) target = $("selfie");
  else if (
    state.identityQuality?.selfie?.status === "assessed" &&
    state.identityQuality.selfie.face_count === 0
  )
    target = $("retake-selfie");
  else if (session.policy.require_mobile && !session.mobile_connected) {
    $("mobile-setup").open = true;
    target = $("pair");
  } else target = $("setup-step-3");
  const section = target.closest("#enrollment-card, [id^=setup-step-]");
  if (section)
    selectSetupStep(
      section.id === "enrollment-card"
        ? 5
        : Number(section.id.split("-").pop()),
    );
  target.scrollIntoView({ behavior: "smooth", block: "center" });
  target.focus({ preventScroll: true });
});
function renderSession() {
  const session = state.session;
  const complete = terminal.has(session.status);
  organizeSetup(session);
  document.querySelector(".time-card").hidden = complete;
  document.querySelector(".session-aside").hidden = complete;
  document
    .querySelector(".session-workspace")
    .classList.toggle("completed-layout", complete);
  document.querySelector(".submission-bar").hidden = complete;
  $("completion-receipt").hidden = !complete;
  if (complete) {
    $("completion-title").textContent =
      session.status === "submitted"
        ? "Submission confirmed"
        : `Assessment ${humanStatus(session.status).toLowerCase()}`;
    $("completion-message").textContent =
      session.submission_reason === "fullscreen_timeout"
        ? "Your assessment was automatically submitted because fullscreen was not restored within 5 seconds. Your final answers are saved."
        : session.submission_reason === "five_confirmed_violations"
          ? "Your assessment was automatically submitted after five confirmed violations. Your server-saved answers are available to your organizer."
          : session.status === "submitted"
            ? "Your answers are saved. This attempt is complete and cannot be edited."
            : "This attempt has ended. Any server-saved responses remain available to your organizer.";
    $("completion-reference").textContent =
      `Attempt ${session.id}${session.ended_at ? " · " + new Date(session.ended_at * 1000).toLocaleString() : ""}`;
    $("completion-answers").replaceChildren(
      ...(session.questions || []).flatMap((q) => [
        node("dt", q.prompt),
        node("dd", session.answers?.[q.id] || "No saved response"),
      ]),
    );
    $("completion-responses").hidden = !session.questions?.length;
  }
  $("assessment-alert").hidden = session.status !== "in_progress";
  $("assessment-observations").hidden = session.status !== "in_progress";
  setPill(
    "session-state",
    humanStatus(session.status),
    statusTone(session.status),
  );
  $("verification-state").textContent =
    {
      pending: "Upload both images to request an identity review.",
      pending_review: "Your identity documents have been sent to your proctor.",
      approved: "Your identity has been approved.",
      rejected:
        "Please review your identity details or contact your organizer.",
    }[session.verification] || "Identity review required.";
  if (
    session.policy.verification_mode === "institution_account" &&
    session.verification !== "approved"
  )
    $("verification-state").textContent =
      "Account-based access · identity not verified. Image comparison is advisory.";
  $("session-title").textContent =
    session.status === "preflight"
      ? "Assessment setup"
      : terminal.has(session.status)
        ? "Assessment complete."
        : "Assessment in progress";
  $("page-location").textContent =
    session.status === "preflight"
      ? "Assessment setup"
      : terminal.has(session.status)
        ? "Session summary"
        : "Assessment in progress";
  $("session-eyebrow").textContent =
    session.status === "preflight"
      ? "ASSESSMENT SETUP"
      : terminal.has(session.status)
        ? "SESSION SUMMARY"
        : "ASSESSMENT IN PROGRESS";
  $("timer-label").textContent = session.started_at
    ? "TIME REMAINING"
    : "TIME ALLOWED";
  $("timer-hint").textContent = session.started_at
    ? "Based on your assessment deadline"
    : "Your timer hasn't started yet";
  $("preflight").hidden = session.status !== "preflight";
  $("capture-recovery").hidden = session.status !== "in_progress";
  $("questions").hidden = session.status !== "in_progress";
  $("submit").hidden = session.status !== "in_progress";
  if (terminal.has(session.status)) {
    for (const input of $("questions").querySelectorAll(
      "input, textarea, select",
    ))
      input.disabled = true;
    if (session.submission_reason === "five_confirmed_violations")
      $("save-state").textContent =
        "Automatically submitted after five proctor-confirmed violations. Your server-saved answers were submitted. Any unsynchronized local draft is retained for organizer follow-up.";
  }
  $("mobile-state").textContent = session.mobile_connected
    ? session.status === "preflight"
      ? "Phone camera ready. Capture starts when you begin the exam."
      : "Confirmed mobile camera is uploading."
    : state.capabilities?.phone_pairing === false
      ? "Phone pairing is unavailable on this local address."
      : "Pair your phone to add a second camera view.";
  $("mobile-frame").classList.toggle(
    "is-live",
    !!$("mobile-preview").srcObject && !!session.mobile_connected,
  );
  if (terminal.has(session.status)) {
    $("time-warning").hidden = true;
    $("time-warning").textContent = "";
    $("fullscreen-recovery").hidden = true;
    $("fullscreen-help").textContent = "";
    $("assessment-alert").hidden = true;
    $("assessment-observations").hidden = true;
    $("save-state").textContent =
      session.submission_reason === "five_confirmed_violations"
        ? "Automatically submitted after five proctor-confirmed violations. Your server-saved answers were submitted. Any unsynchronized local draft is retained for organizer follow-up."
        : session.submission_reason === "fullscreen_timeout"
          ? "Automatically submitted because fullscreen was not restored within 5 seconds. Your final answers are saved."
          : `Attempt ${session.status}. Your saved responses are available to your organizer.`;
    for (const element of $("questions").querySelectorAll("input,textarea"))
      element.disabled = true;
  }
  updateCaptureState();
  renderSetup();
}
function renderQuestions(s) {
  $("questions").replaceChildren(node("h2", "Your responses"));
  let questionNumber = 0;
  for (const q of s.questions || []) {
    const div = node("div", undefined, "question");
    div.append(
      node(
        "p",
        `QUESTION ${String(++questionNumber).padStart(2, "0")}`,
        "question-number",
      ),
    );
    const label = node("label", q.prompt);
    label.htmlFor = "answer-" + q.id;
    div.append(label);
    if (q.kind === "choice") {
      for (const option of q.options) {
        const l = node("label", undefined, "choice");
        const input = document.createElement("input");
        input.type = "radio";
        input.name = q.id;
        input.value = option;
        input.dataset.question = q.id;
        l.append(input, document.createTextNode(option));
        div.append(l);
      }
    } else {
      const input = document.createElement("textarea");
      input.id = "answer-" + q.id;
      input.dataset.question = q.id;
      input.rows = 5;
      input.maxLength = 20000;
      div.append(input);
    }
    $("questions").append(div);
  }
  fillAnswers(s.answers || {});
  for (const el of $("questions").querySelectorAll("input,textarea"))
    el.addEventListener("input", () => {
      state.dirty = true;
      $("save-state").textContent = "Saving…";
      clearTimeout(state.saveTimeout);
      state.saveTimeout = setTimeout(() => guard(saveAnswers), 800);
      guard(() =>
        state.queue.snapshot({
          baseRevision: state.revision,
          answers: collectAnswers(),
        }),
      );
    });
}
function fillAnswers(answers) {
  for (const el of $("questions").querySelectorAll("[data-question]")) {
    if (el.type === "radio")
      el.checked = answers[el.dataset.question] === el.value;
    else el.value = answers[el.dataset.question] || "";
  }
}
function collectAnswers() {
  const answers = {};
  for (const el of $("questions").querySelectorAll("[data-question]")) {
    if (el.type !== "radio" || el.checked)
      answers[el.dataset.question] = el.value;
  }
  return answers;
}
async function saveAnswers() {
  if (state.saving) return state.saving;
  if (
    state.submitting ||
    !state.dirty ||
    state.session.status !== "in_progress"
  )
    return;
  const answers = collectAnswers();
  const expected = state.revision;
  state.saving = (async () => {
    await state.queue.snapshot({ baseRevision: expected, answers });
    try {
      const result = await api(`/api/sessions/${state.session.id}/answers`, {
        method: "PUT",
        body: { revision: expected + 1, answers },
      });
      state.revision = result.revision;
      if (JSON.stringify(collectAnswers()) === JSON.stringify(answers)) {
        state.dirty = false;
        await state.queue.clearSnapshot();
        $("save-state").textContent =
          `Saved to server · revision ${state.revision}`;
      } else
        await state.queue.snapshot({
          baseRevision: state.revision,
          answers: collectAnswers(),
        });
    } catch (e) {
      $("save-state").textContent =
        "Answers are encrypted locally and awaiting synchronization.";
      throw e;
    }
  })();
  try {
    await state.saving;
  } finally {
    state.saving = null;
  }
}
function updateTimer() {
  const s = state.session;
  if (s && terminal.has(s.status)) {
    $("timer").textContent = "Ended";
    return;
  }
  if (!s?.deadline_at) {
    $("timer").textContent = s?.duration_seconds
      ? Math.round(s.duration_seconds / 60) + " min"
      : "Not started";
    return;
  }
  const seconds = Math.max(
    0,
    Math.ceil(
      (s.deadline_at * 1000 - (Date.now() + (state.serverOffset || 0))) / 1000,
    ),
  );
  $("timer").textContent = `${Math.floor(seconds / 3600)
    .toString()
    .padStart(2, "0")}:${Math.floor((seconds % 3600) / 60)
    .toString()
    .padStart(2, "0")}:${(seconds % 60).toString().padStart(2, "0")}`;
  if (seconds === 0 && s.status === "in_progress") {
    $("save-state").textContent =
      "Time is up. The server will close this attempt using the last saved answers.";
  }
}
async function flushQueue() {
  if (state.queue)
    await state.queue.flush(
      async (url, options) => {
        const started = performance.now();
        const result = await api(url, options);
        const kind = new URL(url, location.origin).searchParams.get("kind");
        if (
          state.session &&
          url.startsWith(`/api/sessions/${state.session.id}/`) &&
          result.received_at
        ) {
          if (["screen", "recording"].includes(kind))
            state.session.last_screen_at = result.received_at;
          if (kind === "camera")
            state.session.last_camera_at = result.received_at;
        }
        state.experience?.uploaded(
          url,
          options.body?.byteLength || options.body?.size || 0,
          performance.now() - started,
          result,
        );
        return result;
      },
      (item) => {
        const phase = state.session?.status || state.mobileStatus;
        if (!["in_progress", "submitted"].includes(phase)) return false;
        const began = state.session?.started_at || state.mobileStartedAt;
        // Keep legacy setup captures local; never relabel them as exam evidence.
        if (began && item.created < began * 1000 - (state.serverOffset || 0))
          return false;
        return (
          !terminal.has(phase) ||
          new URL(item.url, location.origin).pathname.endsWith("/media")
        );
      },
    );
}
function tracked(promise) {
  state.captureTasks.add(promise);
  promise
    .catch((e) => {
      if (!terminal.has(state.session?.status))
        message(
          e.status === 429
            ? `Evidence upload paused for ${e.retryAfter || 5} seconds. Capture continues locally; answer submission is available.`
            : "Evidence upload delayed: " + e.message,
          true,
        );
    })
    .finally(() => state.captureTasks.delete(promise));
  return promise;
}
function captureImage(...args) {
  return captureImageFrame(...args);
}
async function captureImageFrame(video, kind, mobile = false) {
  if (
    (!mobile && state.session?.status !== "in_progress") ||
    (mobile && state.mobileStatus !== "in_progress")
  )
    return;
  if (state.stopping)
    throw new Error(
      "Capture is stopping; re-enable the device before taking a frame.",
    );
  await waitForVideo(video, 10000);
  const canvas = document.createElement("canvas");
  const width = Math.min(
    video.videoWidth,
    state.experience?.profile.width || 1280,
  );
  canvas.width = width;
  canvas.height = Math.round((video.videoHeight * width) / video.videoWidth);
  canvas.getContext("2d").drawImage(video, 0, 0, canvas.width, canvas.height);
  const blob = await new Promise((r) => canvas.toBlob(r, "image/jpeg", 0.8));
  if (!blob) throw new Error("Camera frame unavailable");
  const url = mobile
    ? `/api/mobile/media?kind=mobile&stream_id=${crypto.randomUUID()}&sequence=0`
    : `/api/sessions/${state.session.id}/media?kind=${kind}&stream_id=${crypto.randomUUID()}&sequence=0`;
  await state.queue.enqueue(url, blob);
  await flushQueue();
}
function cameraError(error) {
  const descriptions = {
    NotAllowedError:
      "Camera access is blocked. Allow camera access for this site in your browser, then try again.",
    NotFoundError: "No camera was found. Connect a camera, then try again.",
    NotReadableError:
      "The camera is busy or unavailable. Close other apps using it, then try again.",
    OverconstrainedError:
      "This camera cannot use the requested settings. Try a different camera.",
  };
  return (
    descriptions[error.name] || error.message || "Could not start the camera."
  );
}
async function waitForVideo(video, timeout = 10000) {
  const started = Date.now();
  while (Date.now() - started < timeout) {
    if (video.videoWidth > 0 && video.videoHeight > 0 && video.readyState >= 2)
      return;
    if (!video.srcObject?.getVideoTracks().some((t) => t.readyState === "live"))
      throw new Error(
        "Camera disconnected. Select Enable camera and microphone to reconnect.",
      );
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error(
    "Camera is connected but no picture is arriving. Keep this page open, check camera permissions, then try taking the selfie again.",
  );
}
function sampleVideo(video, kind, mobile = false) {
  // Browsers pause hidden/background videos. Skip a background sample rather than showing a false user error.
  if (
    state.stopping ||
    document.hidden ||
    !video?.videoWidth ||
    video.readyState < 2
  )
    return;
  state.samplesInFlight ||= new Set();
  if (state.samplesInFlight.has(kind)) return;
  state.samplesInFlight.add(kind);
  return tracked(
    captureImageFrame(video, kind, mobile).finally(() =>
      state.samplesInFlight.delete(kind),
    ),
  );
}
function startCameraMonitoring() {
  if (state.session?.status !== "in_progress" || !state.camera) return;
  const video = $("camera-preview");
  if (!state.cameraSampling) {
    state.cameraSampling = true;
    let lastSample = Date.now();
    state.cameraSampleInterval = addInterval(() => {
      const seconds = state.session.policy.camera_interval_seconds || 3;
      const interval = state.session.policy.adaptive_capture
        ? state.experience?.profile.cameraSeconds || seconds
        : seconds;
      if (Date.now() - lastSample >= interval * 1000) {
        lastSample = Date.now();
        return sampleVideo(video, "camera");
      }
    }, 1000);
    if (state.session.policy.enable_audio_monitoring !== false)
      startAudioMeter(state.camera);
  }
  sampleVideo(video, "camera");
}
function startScreenMonitoring() {
  if (
    state.session?.status !== "in_progress" ||
    !state.screen ||
    state.screenSampling
  )
    return;
  state.screenSampling = true;
  if (state.session.policy.enable_recording !== false)
    state.recorder = startRecorder(state.screen, false);
  if (state.session.policy.enable_screenshots !== false) {
    sampleVideo(state.screenVideo, "screen");
    let lastSample = Date.now();
    state.screenSampleInterval = addInterval(() => {
      const seconds = state.session.policy.screenshot_interval_seconds || 5;
      const interval = state.session.policy.adaptive_capture
        ? state.experience?.profile.screenSeconds || seconds
        : seconds;
      if (Date.now() - lastSample >= interval * 1000) {
        lastSample = Date.now();
        return sampleVideo(state.screenVideo, "screen");
      }
    }, 1000);
  }
}
async function enableCamera() {
  state.stopping = false;
  if (!state.session?.consent_at)
    throw new Error(
      "Accept the assessment notice before enabling your camera.",
    );
  $("camera-error").textContent = "";
  state.cameraConnecting = true;
  try {
    if (
      !state.camera?.getVideoTracks().some((t) => t.readyState === "live") ||
      (state.session.policy.require_audio &&
        !state.camera?.getAudioTracks().some((t) => t.readyState === "live"))
    ) {
      clearInterval(state.cameraSampleInterval);
      state.cameraSampling = false;
      if (state.audioContext) {
        await state.audioContext.close();
        state.audioContext = null;
      }
      state.camera?.getTracks().forEach((t) => t.stop());
      if (!navigator.mediaDevices?.getUserMedia)
        throw new Error(
          "Camera access isn't available in this browser. Open this page in a regular browser over HTTPS or localhost.",
        );
      state.camera = await navigator.mediaDevices.getUserMedia({
        video: { width: { ideal: 1280 }, height: { ideal: 720 } },
        audio: state.session.policy.require_audio,
      });
      for (const track of state.camera.getTracks())
        track.addEventListener("ended", () => {
          updateCaptureState();
          guard(() =>
            event(track.kind === "audio" ? "audio_stopped" : "camera_stopped"),
          );
        });
    }
    // Show the element BEFORE play/capture: WebKit may not deliver frames to display:none video.
    $("camera-frame").classList.add("is-live");
    const video = $("camera-preview");
    video.srcObject = state.camera;
    video.muted = true;
    video.playsInline = true;
    await video.play();
    await waitForVideo(video);
    startCameraMonitoring();
    await state.audioContext?.resume().catch(() => {});
    await heartbeat();
    updateCaptureState();
  } catch (error) {
    const detail = cameraError(error);
    $("camera-error").textContent = detail;
    updateCaptureState();
    throw new Error(detail);
  } finally {
    state.cameraConnecting = false;
    updateCaptureState();
  }
}
function withTimeout(promise, milliseconds, text) {
  let timer;
  return Promise.race([
    promise,
    new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error(text)), milliseconds);
    }),
  ]).finally(() => clearTimeout(timer));
}
async function enableScreen() {
  if (!state.session?.consent_at) {
    $("screen-error").textContent =
      "Accept the assessment notice before sharing your display.";
    return;
  }
  if (state.displayRequest) return;
  if (
    state.screen
      ?.getVideoTracks()
      .some((track) => track.readyState === "live") &&
    state.screenReady
  ) {
    $("screen-progress").textContent = "Your entire display is already shared.";
    return;
  }
  const sessionId = state.session.id;
  const request = { cancelled: false };
  state.displayRequest = request;
  state.stopping = false;
  state.screenReady = false;
  $("screen-error").textContent = "";
  $("screen-progress").textContent =
    "Choose Entire screen, then Share in your browser’s picker. If no picker opens, cancel and open this assessment in Chrome or Edge. On macOS, check Screen & System Audio Recording permission for the browser or Codex app.";
  $("cancel-screen").hidden = false;
  for (const id of ["screen", "resume-screen"]) {
    $(id).disabled = true;
    $(id).textContent = "Waiting for screen selection…";
  }
  let stream, video;
  const cancelled = new Promise((_, reject) => {
    request.cancel = () => {
      request.cancelled = true;
      reject(
        new Error(
          "Display request cancelled. Close the browser picker if it is still open, then try again.",
        ),
      );
    };
  });
  try {
    if (!navigator.mediaDevices?.getDisplayMedia)
      throw new Error(
        "This browser cannot share a display. Open the assessment in a regular Chrome or Edge browser.",
      );
    // Invoke in the original user click stack, before any await.
    const selection = navigator.mediaDevices
      .getDisplayMedia({ video: { displaySurface: "monitor" }, audio: false })
      .then((selected) => {
        if (request.cancelled || state.session?.id !== sessionId) {
          selected.getTracks().forEach((track) => track.stop());
          throw new Error("This display request is no longer active.");
        }
        return selected;
      });
    stream = await Promise.race([
      withTimeout(
        selection,
        20000,
        "No display was selected. Close the browser picker and try again. If no picker appears, check screen-recording permission for your browser or open this link in Chrome or Edge.",
      ),
      cancelled,
    ]);
    const track = stream.getVideoTracks()[0];
    const surface = track?.getSettings().displaySurface;
    if (surface !== "monitor")
      throw new Error(
        surface
          ? "You shared a tab or window. Select Share entire display again, then choose Entire screen in the browser picker."
          : "This browser did not report whether the entire display was shared. Try Chrome or Edge and choose Entire screen.",
      );
    $("screen-progress").textContent =
      "Entire display selected. Connecting the preview…";
    video = document.createElement("video");
    video.className = "display-capture-source";
    video.setAttribute("aria-label", "Local shared-display preview");
    video.srcObject = stream;
    video.muted = true;
    video.playsInline = true;
    $("display-preview").hidden = false;
    $("display-preview").append(video); // A visible, attached preview helps WebKit deliver frames reliably.
    await Promise.race([
      withTimeout(
        video.play(),
        8000,
        "Your display was selected but its preview did not start. Try again; check screen-recording permission for this browser if it persists.",
      ),
      cancelled,
    ]);
    if (
      request.cancelled ||
      state.session?.id !== sessionId ||
      track.readyState !== "live"
    )
      throw new Error(
        "Display sharing stopped before it connected. Try again.",
      );
    state.screen = stream;
    state.screenVideo?.remove();
    state.screenVideo = video;
    state.screenReady = true;
    state.screenSampling = false;
    track.addEventListener("ended", () => {
      if (state.screen !== stream) return;
      clearInterval(state.screenSampleInterval);
      state.screen = null;
      state.screenReady = false;
      state.screenSampling = false;
      video.remove();
      $("display-preview").hidden = true;
      for (const id of ["screen", "resume-screen"]) {
        $(id).disabled = false;
        $(id).textContent =
          id === "screen"
            ? "Share entire display"
            : "Re-enable entire display capture";
      }
      $("screen-progress").textContent =
        "Display sharing stopped. Select Share entire display to reconnect.";
      updateCaptureState();
      void event("screen_stopped");
    });
    updateCaptureState();
    $("screen-progress").textContent =
      state.session.status === "preflight"
        ? "Entire display connected. Recording starts only when you begin the assessment."
        : "Entire display connected. Assessment capture is active.";
    startScreenMonitoring();
    // Selection must not wait for constraints, uploads, or a slow server heartbeat.
    void withTimeout(
      adaptCapture(
        state.experience?.profile ||
          captureProfile(null, 0, state.session.policy),
      ),
      4000,
      "Display quality adjustment timed out.",
    ).catch(console.warn);
    void heartbeat().catch((error) => {
      if (state.session?.id === sessionId && state.screen === stream)
        $("screen-progress").textContent =
          "Entire display connected. Device synchronization is delayed; retry Begin when connectivity returns. " +
          error.message;
    });
  } catch (error) {
    request.cancelled = true;
    stream?.getTracks().forEach((track) => track.stop());
    video?.remove();
    if (state.session?.id !== sessionId) return;
    $("display-preview").hidden = true;
    if (state.screen === stream) {
      state.screen = null;
      state.screenReady = false;
    }
    $("screen-progress").textContent =
      "Display is not connected. You can try again.";
    $("screen-error").textContent =
      error.name === "NotAllowedError"
        ? "Display sharing was cancelled or blocked. Try again and allow capture. On macOS, allow screen recording for the browser or Codex app in System Settings, then reopen the app if macOS requests it."
        : error.name === "NotReadableError"
          ? "The system could not capture this display. Check screen-recording permission, then try again in your regular browser."
          : error.name === "InvalidStateError"
            ? "Click Share entire display directly. Close any old picker first, or open this assessment in a regular browser."
            : error.message;
    updateCaptureState();
  } finally {
    if (state.displayRequest === request) {
      state.displayRequest = null;
      $("cancel-screen").hidden = true;
      for (const id of ["screen", "resume-screen"]) {
        $(id).disabled = false;
        $(id).textContent = state.screenReady
          ? "Entire display connected"
          : id === "screen"
            ? "Share entire display"
            : "Re-enable entire display capture";
      }
    }
  }
}
$("cancel-screen").addEventListener("click", () =>
  state.displayRequest?.cancel(),
);
async function adaptCapture(profile) {
  if (
    !state.screen ||
    state.stopping ||
    !state.session?.policy.adaptive_capture
  )
    return;
  if (state.adapting) return;
  state.adapting = true;
  try {
    const stream = state.screen;
    const track = stream.getVideoTracks()[0];
    await withTimeout(
      track.applyConstraints({
        frameRate: {
          ideal: Math.min(profile.fps, state.session.policy.recording_fps || 5),
          max: state.session.policy.recording_fps || 5,
        },
        width: { ideal: profile.width },
      }),
      3000,
      "Display quality adjustment timed out.",
    ).catch(() => {});
    if (state.stopping || state.screen !== stream) return;
    const target = Math.min(
      profile.bitrate,
      state.session.policy.recording_bitrate || 450000,
    );
    if (
      state.recorder?.state === "recording" &&
      state.recorder.videoBitsPerSecond !== target
    ) {
      await stopRecorder(state.recorder);
      if (!state.stopping) state.recorder = startRecorder(state.screen, false);
    }
  } catch (error) {
    message("Capture quality could not change: " + error.message, true);
  } finally {
    state.adapting = false;
  }
}
function startRecorder(stream, mobile) {
  if (!window.MediaRecorder)
    throw new Error("Recording is not supported by this browser.");
  const mime = ["video/webm;codecs=vp8", "video/webm", "video/mp4"].find((x) =>
    MediaRecorder.isTypeSupported(x),
  );
  if (!mime) throw new Error("No supported recording format.");
  if (
    (!mobile && state.session?.status !== "in_progress") ||
    (mobile && state.mobileStatus !== "in_progress")
  )
    return null;
  const recorder = new MediaRecorder(stream, {
    mimeType: mime,
    videoBitsPerSecond: mobile
      ? 250000
      : Math.min(
          state.session.policy.recording_bitrate || 450000,
          state.experience?.profile.bitrate || 450000,
        ),
  });
  const streamId = crypto.randomUUID();
  let sequence = 0;
  let serial = Promise.resolve();
  recorder.ondataavailable = (e) => {
    if (!e.data.size) return;
    const n = sequence++;
    const url = mobile
      ? `/api/mobile/media?kind=mobile_recording&stream_id=${streamId}&sequence=${n}`
      : `/api/sessions/${state.session.id}/media?kind=recording&stream_id=${streamId}&sequence=${n}`;
    serial = serial.then(() =>
      state.queue.enqueue(
        url,
        new Blob([e.data], { type: mime.split(";")[0] }),
        n,
      ),
    );
    tracked(serial.then(flushQueue));
  };
  recorder.onerror = () =>
    message("Recording failed. Notify your proctor.", true);
  if (!mobile) void event("recording_started", { stream_id: streamId });
  recorder.start(5000);
  recorder.finished = () => serial;
  return recorder;
}
async function stopRecorder(recorder) {
  if (!recorder) return;
  if (recorder.state !== "inactive")
    await new Promise((resolve) => {
      recorder.addEventListener("stop", resolve, { once: true });
      recorder.stop();
    });
  await recorder.finished();
}
async function stopCapture() {
  state.stopping = true;
  state.gaze?.stop();
  state.gaze = null;
  state.displayRequest?.cancel();
  state.experience?.stop();
  state.experience = null;
  clearTimeout(state.saveTimeout);
  await stopRecorder(state.recorder).catch((e) =>
    message("Final recording chunk awaits upload: " + e.message, true),
  );
  state.recorder = null;
  for (const stream of [state.camera, state.screen])
    stream?.getTracks().forEach((t) => t.stop());
  state.camera = null;
  state.cameraSampling = false;
  state.mobileMonitoring = false;
  state.screenSampling = false;
  state.screen = null;
  state.screenReady = false;
  state.screenVideo?.remove();
  state.screenVideo = null;
  $("display-preview").hidden = true;
  clearInterval(state.audioMeterInterval);
  $("resume-audio").hidden = true;
  if (state.audioContext) {
    await state.audioContext.close();
    state.audioContext = null;
    $("audio-analysis-status").textContent = "Sound monitoring is off.";
  }
  state.rtc?.close();
  state.rtc = null;
  await Promise.allSettled([...state.captureTasks]);
}
function updateCaptureState() {
  const live = liveDeviceState();
  const session = state.session;
  if (!state.audioContext) {
    $("resume-audio").hidden = true;
    $("audio-analysis-status").textContent =
      session?.policy.enable_audio_monitoring === false
        ? "Sound monitoring disabled by organizer."
        : session?.status === "preflight"
          ? "Sound monitoring starts after Begin assessment."
          : session?.status === "in_progress"
            ? "Connect your microphone to start sound monitoring."
            : "Sound monitoring is off.";
  }
  setPill(
    "camera-status",
    live.camera
      ? live.audio
        ? "Camera & microphone active"
        : "Camera connected"
      : "Not connected",
    live.camera ? "positive" : "neutral",
  );
  setPill(
    "screen-status",
    live.screen ? "Display shared" : "Not shared",
    live.screen ? "positive" : "neutral",
  );
  setPill(
    "preview-status",
    live.camera ? "Live" : "Off",
    live.camera ? "positive" : "neutral",
  );
  state.experience?.render();
  $("camera-frame").classList.toggle(
    "is-live",
    live.camera || !!state.cameraConnecting,
  );
  $("capture-state").textContent =
    live.camera || live.screen
      ? `Camera ${live.camera ? "connected" : "off"} · Microphone ${live.audio ? "connected" : "off"} · Display ${live.screen ? "shared" : "not shared"}`
      : session &&
          !session.policy.require_camera &&
          !session.policy.require_audio &&
          !session.policy.require_screen
        ? "No live device connections are required for this assessment."
        : "Connect the required devices to continue.";
  if (session) renderSetup();
}
async function heartbeat() {
  if (!state.session || terminal.has(state.session.status)) return;
  const data = {
    camera: !!state.camera
      ?.getVideoTracks()
      .some((t) => t.readyState === "live"),
    screen: !!state.screen
      ?.getVideoTracks()
      .some((t) => t.readyState === "live"),
    audio: !!state.camera
      ?.getAudioTracks()
      .some((t) => t.readyState === "live"),
    screen_surface:
      state.screen?.getVideoTracks()[0]?.getSettings().displaySurface ||
      "unknown",
  };
  const result = await api(`/api/sessions/${state.session.id}/heartbeat`, {
    method: "POST",
    body: data,
  });
  state.session.mobile_connected = result.mobile_connected;
  if (result.last_screen_at)
    state.session.last_screen_at = result.last_screen_at;
  if (result.last_camera_at)
    state.session.last_camera_at = result.last_camera_at;
  updateCaptureState();
  return data;
}
function startAudioMeter(stream) {
  if (!stream.getAudioTracks().length) return;
  clearInterval(state.audioMeterInterval);
  const context = new AudioContext();
  state.audioContext = context;
  const source = context.createMediaStreamSource(stream);
  const analyser = context.createAnalyser();
  analyser.fftSize = 2048;
  source.connect(analyser);
  const detector = new SoundActivityDetector();
  $("audio-analysis-status").textContent =
    "Measuring background sound for 5 seconds…";
  context.resume().catch(() => {});
  state.audioMeterInterval = addInterval(async () => {
    if (
      state.audioContext !== context ||
      state.session?.status !== "in_progress" ||
      state.stopping
    )
      return;
    $("resume-audio").hidden = context.state === "running";
    if (context.state !== "running") {
      $("audio-analysis-status").textContent =
        "Sound monitoring paused. Select Resume sound monitoring.";
      return;
    }
    const track = stream.getAudioTracks()[0];
    if (
      !track ||
      track.readyState !== "live" ||
      !track.enabled ||
      track.muted
    ) {
      $("audio-analysis-status").textContent =
        "Microphone unavailable or muted. Reconnect your microphone.";
      if (state.experience) state.experience.sound = 0;
      return;
    }
    const data = new Float32Array(analyser.fftSize);
    analyser.getFloatTimeDomainData(data);
    const rms = Math.sqrt(data.reduce((s, x) => s + x * x, 0) / data.length);
    const observation = detector.sample(rms, performance.now());
    if (state.experience) state.experience.sound = rms;
    $("audio-analysis-status").textContent =
      detector.threshold === null
        ? "Measuring background sound for 5 seconds…"
        : "Sound monitoring active · quiet sounds included.";
    if (observation) {
      if (state.experience) {
        state.experience.notice =
          "Sustained sound detected. Keep your surroundings quiet.";
        state.experience.noticeUntil = Date.now() + 6000;
        state.experience.render();
      }
      await event("audio_level_change", observation);
    }
  }, 250);
}
bind("resume-audio", async () => {
  const context = state.audioContext;
  if (!context || state.session?.status !== "in_progress") return;
  await context.resume();
  if (context.state !== "running")
    throw new Error(
      "Your browser could not resume sound monitoring. Reconnect your microphone.",
    );
  $("resume-audio").hidden = true;
});
async function event(kind, details = {}) {
  if (
    !state.session ||
    state.session.status !== "in_progress" ||
    state.stopping
  )
    return;
  try {
    await api(`/api/sessions/${state.session.id}/events`, {
      method: "POST",
      body: {
        event_id: crypto.randomUUID(),
        kind,
        client_time: Date.now() / 1000,
        ...details,
      },
    });
    await state.experience?.poll();
  } catch (e) {
    if (e.status !== 409) console.warn("Observation was not received", e);
  }
}
async function setupRTC(mobile) {
  if (state.rtc) return state.rtc;
  const config = await api(mobile ? "/api/mobile/rtc" : "/api/rtc");
  const pc = new RTCPeerConnection(config);
  state.rtc = pc;
  state.pendingICE = [];
  const endpoint = mobile
    ? "/api/mobile/signals"
    : `/api/sessions/${state.session.id}/signals`;
  pc.onicecandidate = (e) => {
    if (e.candidate)
      guard(() =>
        api(endpoint, {
          method: "POST",
          body: { type: "ice", candidate: e.candidate.toJSON() },
        }),
      );
  };
  pc.ontrack = (e) => {
    if (!mobile && e.streams[0]) $("mobile-preview").srcObject = e.streams[0];
  };
  pc.onconnectionstatechange = () => {
    if (["failed", "disconnected"].includes(pc.connectionState))
      message(
        "Mobile live preview disconnected. Camera evidence uploads continue; reconnect the phone if needed.",
      );
  };
  addInterval(async () => {
    const signals = await api(endpoint + "?after=" + state.signalAfter);
    for (const signal of signals) {
      const p = signal.payload;
      if (p.type === "offer" && !mobile) {
        await pc.setRemoteDescription({ type: "offer", sdp: p.sdp });
        for (const c of state.pendingICE) await pc.addIceCandidate(c);
        state.pendingICE = [];
        const answer = await pc.createAnswer();
        await pc.setLocalDescription(answer);
        await api(endpoint, {
          method: "POST",
          body: { type: "answer", sdp: answer.sdp },
        });
      } else if (p.type === "answer" && mobile) {
        await pc.setRemoteDescription({ type: "answer", sdp: p.sdp });
        for (const c of state.pendingICE) await pc.addIceCandidate(c);
        state.pendingICE = [];
      } else if (p.type === "ice") {
        if (pc.remoteDescription) await pc.addIceCandidate(p.candidate);
        else state.pendingICE.push(p.candidate);
      }
      state.signalAfter = signal.id;
    }
  }, 2000);
  return pc;
}
const reviewLabels = {
  unreviewed: "Awaiting review",
  pending: "Awaiting review",
  needs_followup: "Needs follow-up",
  confirmed_by_reviewer: "Confirmed violation",
  dismissed: "Dismissed",
};
function renderReviewState() {
  const session = state.session;
  const student = state.reviewMedia?.student || {};
  const target = $("review-state");
  target.replaceChildren();
  for (const [label, value] of [
    ["Assessment", session.exam_name],
    ["Email", student.email || "Not provided"],
    [
      "Started",
      session.started_at
        ? new Date(session.started_at * 1000).toLocaleString()
        : "Not started",
    ],
    [
      "Student",
      state.reviewMedia?.submitted_details?.name || session.student_name,
    ],
    [
      "Student number or email",
      state.reviewMedia?.submitted_details?.reference ||
        session.student_number ||
        "Not provided",
    ],
    ["Attempt status", session.status.replaceAll("_", " ")],
    ["Identity", session.verification.replaceAll("_", " ")],
    ["Confirmed violations", `${session.confirmed_violation_count || 0} / 5`],
  ]) {
    const item = node("div", undefined, "review-metric");
    item.append(node("span", label), node("strong", value));
    target.append(item);
  }
  const identifiers = node("details", undefined, "review-identifiers");
  identifiers.append(
    node("summary", "Attempt identifiers"),
    node("p", `Exam: ${session.exam_id} · Attempt: ${session.id}`),
  );
  target.append(identifiers);
}
async function loadObservationHistory(reset = false) {
  const id = state.session.id;
  const offset = reset ? 0 : (state.reviewObservations || []).length;
  const rows = await api(
    `/api/sessions/${id}/observations?offset=${offset}&limit=100`,
  );
  if (state.session.id !== id) return;
  state.reviewObservations = reset
    ? rows
    : [...state.reviewObservations, ...rows];
  $("more-observations").hidden = rows.length < 100;
  renderObservationHistory();
  renderRecordingMarkers();
}
function renderObservationHistory() {
  const filter = $("history-filter").value;
  const search = $("history-search").value.trim().toLowerCase();
  const loaded = state.reviewObservations || [];
  const rows = loaded.filter((o) => {
    const matches =
      filter === "all" ||
      (filter === "incidents" && !o.informational) ||
      (filter === "warnings" && o.warning_only) ||
      (filter === "information" && o.informational && !o.warning_only) ||
      (filter === "pending" &&
        !o.informational &&
        ["unreviewed", "pending"].includes(o.disposition)) ||
      (filter === o.disposition && !o.informational);
    return matches && (o.name || o.kind).toLowerCase().includes(search);
  });
  $("history-count").textContent =
    `${rows.length} shown · ${loaded.length} records loaded · Newest first`;
  $("observations").replaceChildren();
  if (!rows.length)
    $("observations").append(
      node("p", "No records match this view.", "history-empty"),
    );
  for (const o of rows) {
    const card = node("article", undefined, "incident-card");
    const heading = node("div", undefined, "incident-heading");
    const title = node("div");
    const time = node("time", new Date(o.received_at * 1000).toLocaleString());
    time.dateTime = new Date(o.received_at * 1000).toISOString();
    title.append(time, node("h3", o.name || o.kind.replaceAll("_", " ")));
    heading.append(
      title,
      node(
        "span",
        o.warning_only
          ? "Camera warning · not a violation"
          : o.informational
            ? "Routine check"
            : reviewLabels[o.disposition] || "Awaiting review",
        `review-badge status-${o.disposition}`,
      ),
    );
    card.append(heading);
    const source =
      {
        client_reported: "Student browser",
        model: "Automated check",
        server: "Server check",
      }[o.source] || "System";
    const recovery = {
      pending: "Fullscreen recovery requested",
      restored: "Fullscreen restored",
      timed_out: "Recovery time expired",
    }[o.details.recovery_status];
    const duration =
      o.details.duration_ms == null
        ? ""
        : ` · ${(o.details.duration_ms / 1000).toFixed(1)} seconds`;
    card.append(
      node(
        "p",
        `${source}${recovery ? ` · ${recovery}` : ""}${duration}`,
        "incident-meta",
      ),
    );
    const details = node("details", undefined, "incident-details");
    details.append(node("summary", "Staff review history"));
    const snapshotArea = node("div", undefined, "incident-snapshots");
    if (o.images?.length) {
      for (const evidence of o.images) {
        const figure = node("figure");
        const img = node("img");
        img.alt = `${evidence.kind === "camera" ? "Camera" : "Screen"} image near ${o.name}`;
        img.loading = "lazy";
        const load = button(
          "View image",
          async () => {
            img.src = blobURL(await api(`/api/evidence/${evidence.id}`));
            img.hidden = false;
            load.hidden = true;
          },
          true,
        );
        img.hidden = true;
        const observer = new IntersectionObserver(
          (entries) => {
            if (!entries.some((entry) => entry.isIntersecting)) return;
            observer.disconnect();
            load.click();
          },
          { rootMargin: "100px" },
        );
        observer.observe(figure);
        const delta = Math.round(evidence.received_at - o.received_at);
        figure.append(
          img,
          load,
          node(
            "figcaption",
            `${evidence.kind === "camera" ? "Camera" : "Screen"} · ${new Date(evidence.received_at * 1000).toLocaleTimeString()} · ${Math.abs(delta)}s ${delta < 0 ? "before" : "after"} incident`,
          ),
        );
        snapshotArea.append(figure);
      }
    } else
      snapshotArea.append(
        node(
          "p",
          "No camera or screen image was saved within 10 seconds of this incident.",
          "muted",
        ),
      );
    card.append(snapshotArea);
    if (state.reviewMedia?.recording_available)
      card.append(
        button("View moment in recording", () => seekReviewIncident(o), true),
      );
    for (const review of o.review_history || []) {
      const entry = node("div", undefined, "review-entry");
      entry.append(
        node(
          "strong",
          `${reviewLabels[review.disposition] || review.disposition} · ${review.reviewer}`,
        ),
        node("small", new Date(review.reviewed_at * 1000).toLocaleString()),
        node("p", review.reason),
      );
      details.append(entry);
    }
    if (!o.review_history?.length)
      details.append(node("p", "No staff review recorded yet."));
    card.append(details);
    if (!o.informational) {
      const reasonLabel = node("label", "Review note");
      const reason = node("textarea");
      reason.rows = 2;
      reason.minLength = 10;
      reason.placeholder =
        "Describe what you checked and why you made this decision.";
      reasonLabel.append(reason);
      const actions = node("div", undefined, "incident-actions");
      for (const [text, disposition] of [
        ["Dismiss", "dismissed"],
        ["Follow up", "needs_followup"],
        ["Confirm violation", "confirmed_by_reviewer"],
      ]) {
        actions.append(
          button(
            text,
            async () => {
              if (reason.value.trim().length < 10)
                throw new Error(
                  "Enter a specific review note of at least 10 characters for this incident.",
                );
              await api(`/api/observations/${o.id}/review`, {
                method: "POST",
                body: { disposition, reason: reason.value.trim() },
              });
              state.session = await api(`/api/sessions/${state.session.id}`);
              renderReviewState();
              await loadObservationHistory(true);
              message(
                state.session.submission_reason === "five_confirmed_violations"
                  ? "Attempt automatically submitted after five proctor-confirmed violations."
                  : "Incident review saved.",
              );
            },
            true,
          ),
        );
      }
      card.append(reasonLabel, actions);
    }
    $("observations").append(card);
  }
}
$("history-filter").addEventListener("change", renderObservationHistory);
$("history-search").addEventListener("input", renderObservationHistory);
bind("refresh-history", () => loadObservationHistory(true));
bind("more-observations", () => loadObservationHistory());

async function openReview(id) {
  clearIntervals();
  await stopCapture();
  hideViews();
  revokeURLs();
  state.session = await api("/api/sessions/" + id);
  state.reviewMedia = await api(`/api/sessions/${id}/review-media`);
  state.recordingTimeline = [];
  state.reviewRecordingPromise = null;
  $("review-video").removeAttribute("src");
  $("review-video").load();
  $("download-recording").hidden = true;
  $("recording-markers").replaceChildren();
  $("recording-timing-note").hidden = true;
  $("load-recording").hidden = !state.reviewMedia.recording_available;
  $("load-recording").disabled = false;
  $("recording-status").textContent = state.reviewMedia.recording_available
    ? "Ready to prepare the complete video. Incident markers appear when it is ready."
    : "No screen recording was captured for this attempt.";
  $("review").hidden = false;
  $("account-panel").hidden = true;
  setShell("review", "Session review");
  window.scrollTo(0, 0);
  $("review-reason").value = "";
  for (const detail of document.querySelectorAll(
    "#review .staff-review-controls",
  ))
    detail.open = false;
  renderReviewState();
  $("review-identity-images").replaceChildren();
  for (const [kind, label] of [
    ["id", "Identity document"],
    ["selfie", "Student selfie"],
  ]) {
    const figure = node("figure", undefined, "identity-review-photo");
    figure.append(node("figcaption", label));
    if (state.reviewMedia.identity[kind]) {
      const img = node("img");
      img.alt = `${label} for ${state.session.student_name}`;
      figure.append(img);
      api(`/api/sessions/${id}/identity-images/${kind}`)
        .then((blob) => {
          if (state.session.id === id) img.src = blobURL(blob);
        })
        .catch(() => figure.append(node("p", "Image is no longer available.")));
    } else
      figure.append(node("p", "No image available for this attempt.", "muted"));
    $("review-identity-images").append(figure);
  }
  $("review-answers").replaceChildren();
  const questions = state.session.questions || [];
  $("review-answer-count").textContent =
    `${questions.filter((q) => state.session.answers[q.id]?.trim()).length} of ${questions.length} responses saved`;
  questions.forEach((q, index) => {
    const card = node("article", undefined, "answer-review-card");
    card.append(
      node("span", `QUESTION ${String(index + 1).padStart(2, "0")}`, "eyebrow"),
      node("h3", q.prompt),
      node(
        "p",
        state.session.answers[q.id] || "No response saved.",
        "student-answer",
      ),
    );
    $("review-answers").append(card);
  });
  if (!questions.length)
    $("review-answers").append(
      node(
        "p",
        "Answers will appear when the student starts the assessment.",
        "muted",
      ),
    );
  $("history-filter").value = "incidents";
  $("history-search").value = "";
  await loadObservationHistory(true);
}

function recordingPosition(incident) {
  for (const segment of state.recordingTimeline || []) {
    const delta = incident.received_at - segment.started_at;
    if (delta >= 0 && delta <= segment.duration) return segment.offset + delta;
  }
  return null;
}
function reviewTimestamp(seconds) {
  return `${Math.floor(seconds / 60)
    .toString()
    .padStart(2, "0")}:${Math.floor(seconds % 60)
    .toString()
    .padStart(2, "0")}`;
}
function renderRecordingMarkers() {
  const target = $("recording-markers");
  target.replaceChildren();
  const segments = state.recordingTimeline || [];
  const duration = segments.reduce(
    (total, segment) => total + segment.duration,
    0,
  );
  if (!duration) return;
  for (const incident of (state.reviewObservations || []).filter(
    (o) => !o.informational,
  )) {
    const position = recordingPosition(incident);
    if (position === null) continue;
    const marker = button(
      `${reviewTimestamp(position)} · ${incident.name}`,
      () => seekReviewIncident(incident),
      true,
    );
    marker.className = "recording-flag";
    marker.style.left = `${Math.min(98, (position / duration) * 100)}%`;
    marker.title = `${reviewTimestamp(position)} · ${incident.name}`;
    marker.setAttribute("aria-label", marker.title);
    target.append(marker);
  }
}
async function loadFullRecording() {
  if (state.recordingTimeline?.length) return;
  if (state.reviewRecordingPromise) return state.reviewRecordingPromise;
  const id = state.session.id;
  $("load-recording").disabled = true;
  $("recording-status").textContent =
    "Preparing the full recording… This can take a few minutes.";
  state.reviewRecordingPromise = (async () => {
    try {
      const response = await fetch(`/api/sessions/${id}/recording`, {
        credentials: "same-origin",
        signal: AbortSignal.timeout(900000),
      });
      if (!response.ok) {
        const error = await response.json();
        throw new Error(error.detail || "Recording could not be prepared.");
      }
      const timeline = JSON.parse(
        response.headers.get("X-Recording-Timeline") || "[]",
      );
      const blob = await response.blob();
      if (state.session.id !== id) return;
      const url = blobURL(blob);
      state.recordingTimeline = timeline;
      $("review-video").src = url;
      $("download-recording").href = url;
      $("download-recording").hidden = false;
      $("load-recording").hidden = true;
      $("recording-timing-note").hidden = false;
      $("recording-status").textContent =
        `Full recording ready · ${reviewTimestamp(timeline.reduce((n, s) => n + s.duration, 0))} · ${timeline.length} recording segment${timeline.length === 1 ? "" : "s"}`;
      renderRecordingMarkers();
    } catch (error) {
      if (state.session.id === id)
        $("recording-status").textContent = error.message;
      throw error;
    } finally {
      if (state.session.id === id) {
        $("load-recording").disabled = false;
        state.reviewRecordingPromise = null;
      }
    }
  })();
  return state.reviewRecordingPromise;
}
async function seekReviewIncident(incident) {
  await loadFullRecording();
  const position = recordingPosition(incident);
  if (position === null) {
    message(
      "This incident occurred outside the saved recording segments.",
      true,
    );
    return;
  }
  const video = $("review-video");
  if (video.readyState < 1)
    await new Promise((resolve, reject) => {
      video.addEventListener("loadedmetadata", resolve, { once: true });
      video.addEventListener(
        "error",
        () => reject(new Error("The recording could not be played.")),
        { once: true },
      );
    });
  video.currentTime = Math.max(0, position - 1);
  video.scrollIntoView({ behavior: "smooth", block: "center" });
  await video.play();
}
bind("load-recording", loadFullRecording);
async function mobilePage() {
  try {
    $("mobile-error").hidden = true;
    hideViews();
    $("mobile-panel").hidden = false;
    setShell("mobile", "Secondary camera");
    $("logout").hidden = true;
    $("identity").textContent = "Mobile evidence device";
    const secret = location.hash.slice(1);
    history.replaceState(null, "", "/mobile");
    let me;
    if (secret) {
      const claim = await api("/api/mobile/claim", {
        method: "POST",
        body: { token: secret },
      });
      state.csrf = claim.csrf_token;
      $("mobile-code").textContent =
        "Enter this code on the exam computer: " + claim.confirmation_code;
      me = await api("/api/mobile/me");
    } else me = await api("/api/mobile/me");
    state.csrf = me.csrf_token;
    state.deviceId = me.pairing_id;
    state.queue = await new EvidenceQueue("mobile:" + state.deviceId).init();
    state.queue.onstatus = (n) => {
      $("mobile-status").textContent = n
        ? `${n} encrypted evidence items awaiting upload.`
        : "Mobile evidence synchronized.";
    };
    $("mobile-notice").textContent = me.policy.notice;
    $("phone-start").disabled = !me.approved;
    $("mobile-status").textContent = me.approved
      ? "Device confirmed. Enable camera to begin."
      : "Waiting for confirmation on the exam computer.";
    addInterval(async () => {
      try {
        const info = await api("/api/mobile/me");
        if (!terminal.has(info.status)) await updateMobileMonitoring(info);
        $("phone-start").disabled = !info.approved;
        if (terminal.has(info.status)) await stopPhone();
      } catch (error) {
        if (![401, 409].includes(error.status)) throw error;
        clearIntervals();
        await stopCapture();
        $("phone-start").disabled = true;
        $("mobile-status").textContent =
          "Mobile access ended. Camera and recording stopped.";
        message(error.message, true);
      }
    }, 5000);
    bind("phone-start", async () => {
      if (!$("mobile-consent").checked)
        throw new Error("Accept the capture notice first.");
      if (state.camera) return;
      state.stopping = false;
      state.camera = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: { ideal: "environment" }, width: { ideal: 960 } },
        audio: false,
      });
      $("phone-preview").srcObject = state.camera;
      await $("phone-preview").play();
      await updateMobileMonitoring(await api("/api/mobile/me"));
    });
    bind("phone-stop", stopPhone);
  } catch (error) {
    $("mobile-error").hidden = false;
    $("mobile-error-detail").textContent =
      error.message +
      ". Return to the assessment computer and generate a new QR code. Use your phone’s normal browser, not an in-app scanner preview.";
    $("phone-start").disabled = true;
    $("mobile-status").textContent = "Phone is not connected.";
  }
}
bind("mobile-retry", mobilePage);
async function updateMobileMonitoring(info) {
  state.mobileStatus = info.status;
  if (
    !state.camera?.getVideoTracks().some((track) => track.readyState === "live")
  )
    return;
  await api("/api/mobile/heartbeat", {
    method: "POST",
    body: { camera: true },
  });
  if (info.status !== "in_progress") {
    $("mobile-status").textContent =
      "Camera preview ready. No photos or recording until the exam begins.";
    return;
  }
  if (state.mobileMonitoring) return;
  state.mobileMonitoring = true;
  $("mobile-status").textContent =
    "Exam started. Secondary camera monitoring is active.";
  await captureImage($("phone-preview"), "mobile", true);
  state.recorder = startRecorder(state.camera, true);
  addInterval(() => sampleVideo($("phone-preview"), "mobile", true), 10000);
  try {
    const pc = await setupRTC(true);
    for (const track of state.camera.getTracks())
      pc.addTrack(track, state.camera);
    const offer = await pc.createOffer();
    await pc.setLocalDescription(offer);
    await api("/api/mobile/signals", {
      method: "POST",
      body: { type: "offer", sdp: offer.sdp },
    });
  } catch (e) {
    message(
      "Live preview unavailable: " +
        e.message +
        ". Evidence capture remains active.",
    );
  }
}
async function stopPhone() {
  clearIntervals();
  await stopCapture();
  await flushQueue();
  $("mobile-status").textContent =
    "Camera stopped. Reload this page to reconnect.";
}

for (const destination of ["assessments", "history", "manage", "account"])
  bind("nav-" + destination, () => navigateWorkspace(destination));
for (const element of document.querySelectorAll("[data-nav]"))
  element.addEventListener("click", () =>
    guard(() => navigateWorkspace(element.dataset.nav)),
  );
for (const element of document.querySelectorAll("[data-step]"))
  element.addEventListener("click", () =>
    selectSetupStep(Number(element.dataset.step)),
  );
for (const element of document.querySelectorAll("[data-go-step]"))
  element.addEventListener("click", () =>
    selectSetupStep(Number(element.dataset.goStep)),
  );
bind("photos-next", async () => {
  if (
    !state.identityEvidence?.id ||
    !state.identityEvidence?.selfie ||
    state.selfieDraft
  )
    throw new Error("Save your ID image and confirm your selfie first.");
  selectSetupStep(4);
});
bind("devices-next", async () => {
  if (!devicesReady())
    throw new Error(
      "Connect your required camera, microphone, and display before continuing.",
    );
  selectSetupStep(3);
});
$("id-file").addEventListener("change", () => {
  const file = $("id-file").files[0];
  $("id-file-name").textContent = file?.name || "";
  if (file) guard(uploadIdentity);
});
window.addEventListener("online", updateConnection);
window.addEventListener("offline", updateConnection);
updateConnection();

$("login-form").addEventListener("submit", (e) => {
  e.preventDefault();
  guard(async () => {
    const data = Object.fromEntries(new FormData(e.target));
    const result = await api("/api/auth/login", { method: "POST", body: data });
    state.user = result.user;
    state.organization = result.organization;
    state.capabilities = result.capabilities;
    state.csrf = result.csrf_token;
    e.target.reset();
    renderProfile();
    $("logout").hidden = false;
    message();
    await dashboard();
  });
});
$("enrollment-number-form").addEventListener("submit", (e) => {
  e.preventDefault();
  guard(async () => {
    await api(`/api/users/${$("enrollment-user").value}/enrollment`, {
      method: "PUT",
      body: { student_number: $("student-number").value },
    });
    message("Student number saved.");
    await navigateWorkspace("manage");
  });
});
$("user-form").addEventListener("submit", (e) => {
  e.preventDefault();
  guard(async () => {
    await api("/api/users", {
      method: "POST",
      body: Object.fromEntries(new FormData(e.target)),
    });
    e.target.reset();
    message(
      "Account created. Provide credentials through your institution’s secure channel.",
    );
    await navigateWorkspace("manage");
  });
});
$("exam-form").addEventListener("submit", (e) => {
  e.preventDefault();
  guard(async () => {
    const f = new FormData(e.target);
    const policy = {
      verification_mode: f.get("verification_mode"),
      retention_days: Number(f.get("retention_days")),
    };
    for (const k of [
      "require_seb",
      "require_camera",
      "require_screen",
      "require_audio",
      "require_mobile",
      "enable_face_comparison",
      "enable_face_detection",
      "enable_audio_monitoring",
      "enable_behavior_monitoring",
      "enable_monitor_detection",
      "enable_screenshots",
      "enable_recording",
      "adaptive_capture",
      "require_fullscreen",
      "restrict_copy_paste",
      "restrict_context_menu",
      "restrict_print_save_shortcuts",
    ])
      policy[k] = f.has(k);
    for (const k of [
      "camera_interval_seconds",
      "screenshot_interval_seconds",
      "recording_fps",
      "recording_bitrate",
      "fullscreen_grace_seconds",
      "max_verification_attempts",
      "max_violations",
    ])
      policy[k] = Number(f.get(k));
    await api("/api/exams", {
      method: "POST",
      body: {
        name: f.get("name"),
        description: f.get("description"),
        kind: f.get("kind"),
        duration_minutes: Number(f.get("duration_minutes")),
        questions: f
          .get("prompts")
          .split("\n")
          .map((x) => x.trim())
          .filter(Boolean)
          .map((prompt, i) => ({ id: "q" + (i + 1), prompt, kind: "text" })),
        policy,
        seb_keys: f
          .get("seb_keys")
          .split("\n")
          .map((x) => x.trim())
          .filter(Boolean),
      },
    });
    e.target.reset();
    message("Assessment created. Assign students next.");
    await navigateWorkspace("manage");
  });
});
$("assign-form").addEventListener("submit", (e) => {
  e.preventDefault();
  guard(async () => {
    const f = new FormData(e.target);
    await api(`/api/exams/${f.get("exam_id")}/assign`, {
      method: "POST",
      body: {
        user_id: f.get("user_id"),
        max_attempts: Number(f.get("max_attempts")),
        extra_minutes: Number(f.get("extra_minutes")),
      },
    });
    message("Assignment saved.");
  });
});
$("password-form").addEventListener("submit", (e) => {
  e.preventDefault();
  guard(async () => {
    await api("/api/auth/password", {
      method: "POST",
      body: Object.fromEntries(new FormData(e.target)),
    });
    location.reload();
  });
});
bind("logout", async () => {
  if (state.session && !terminal.has(state.session.status))
    throw new Error(
      "Finish the active session or return to the dashboard before signing out.",
    );
  await api("/api/auth/logout", { method: "POST" });
  location.reload();
});
bind("refresh", () =>
  dashboard(
    document.body.dataset.view === "history" ? "history" : "assessments",
  ),
);
bind("back", dashboard);
bind("review-back", dashboard);
bind("consent", async () => {
  if (state.session.consent_at) {
    selectSetupStep(5);
    return;
  }
  if (!$("consent-check").checked)
    throw new Error("Read and accept the notice first.");
  state.session = await api(`/api/sessions/${state.session.id}/consent`, {
    method: "POST",
    body: { accepted: true },
  });
  $("consent").disabled = true;
  $("consent-check").disabled = true;
  selectSetupStep(5);
});
bind("decline", async () => {
  state.session = await api(`/api/sessions/${state.session.id}/consent`, {
    method: "POST",
    body: { accepted: false },
  });
  await dashboard();
});
bind("camera", enableCamera);
bind("resume-camera", enableCamera);
bind("resume-screen", enableScreen);
bind("screen", enableScreen);
async function uploadIdentity() {
  if (state.identityUploadPromise) return state.identityUploadPromise;
  const sessionId = state.session.id;
  const file = $("id-file").files[0];
  if (!file) throw new Error("Choose an ID image first.");
  if (!state.session.consent_at)
    throw new Error("Accept the assessment notice before uploading.");
  if (file.size > 8 * 1024 * 1024) {
    $("id-upload-error").textContent =
      "This image is too large. Choose an image smaller than 8 MB.";
    throw new Error($("id-upload-error").textContent);
  }
  state.idUploading = true;
  $("id-upload-error").textContent = "";
  renderSetup();
  state.identityUploadPromise = (async () => {
    let saved = false;
    try {
      const result = await api(
        `/api/sessions/${sessionId}/media?kind=id&stream_id=${crypto.randomUUID()}&sequence=0`,
        {
          method: "POST",
          headers: { "Content-Type": file.type || "image/jpeg" },
          body: file,
        },
      );
      if (state.session?.id !== sessionId) return;
      state.identityEvidence = { ...state.identityEvidence, id: true };
      state.identityEvidenceIds = {
        ...state.identityEvidenceIds,
        id: result.id,
      };
      state.identityEpoch++;
      state.photoComparison = null;
      state.documentChecks = result.document_checks || null;
      state.documentBackChecks = null;
      saved = true;
      $("comparison-result").textContent =
        "New ID image saved. Image comparison results will update when checks run.";
      $("id-saved-preview").src = blobURL(file);
      $("id-saved-preview").hidden = false;
      if (result.analysis)
        state.experience?.identityQuality("id", result.analysis);
      state.session.verification = "pending";
      message("Identity document uploaded. Registered student name found.");
    } catch (error) {
      if (state.session?.id !== sessionId) return;
      $("id-upload-error").textContent = error.message;
      throw error;
    } finally {
      if (state.session?.id === sessionId) {
        state.idUploading = false;
        state.identityUploadPromise = null;
        renderSession();
        if (saved) maybeRunImageChecks();
      }
    }
  })();
  return state.identityUploadPromise;
}
bind("upload-id", uploadIdentity);
async function captureSelfiePreview() {
  $("selfie-error").textContent = "";
  try {
    if (!state.camera?.getVideoTracks().some((t) => t.readyState === "live"))
      await enableCamera();
    const video = $("camera-preview");
    $("camera-frame").classList.add("is-live");
    await video.play();
    await waitForVideo(video);
    const canvas = document.createElement("canvas");
    canvas.width = Math.min(video.videoWidth, 1280);
    canvas.height = Math.round(
      (video.videoHeight * canvas.width) / video.videoWidth,
    );
    canvas.getContext("2d").drawImage(video, 0, 0, canvas.width, canvas.height);
    const blob = await new Promise((resolve) =>
      canvas.toBlob(resolve, "image/jpeg", 0.9),
    );
    if (!blob) throw new Error("No camera frame is available.");
    state.selfieDraft = blob;
    $("selfie-still-preview").src = blobURL(blob);
    $("selfie-preview-panel").hidden = false;
    $("confirm-selfie").hidden = false;
    $("selfie-draft-help").textContent =
      "Preview only. Select Use this selfie to save it, or retake.";
    renderSetup();
    message("Selfie preview captured. Confirm it to upload.");
  } catch (error) {
    $("selfie-error").textContent = cameraError(error);
    throw error;
  }
}
bind("selfie", captureSelfiePreview);
bind("retake-selfie", captureSelfiePreview);
bind("confirm-selfie", async () => {
  if (!state.selfieDraft) throw new Error("Capture a selfie preview first.");
  const sessionId = state.session.id;
  const result = await api(
    `/api/sessions/${state.session.id}/media?kind=selfie&stream_id=${crypto.randomUUID()}&sequence=0`,
    {
      method: "POST",
      headers: { "Content-Type": "image/jpeg" },
      body: state.selfieDraft,
    },
  );
  if (state.session?.id !== sessionId) return;
  state.selfieDraft = null;
  state.identityEvidence = { ...state.identityEvidence, selfie: true };
  state.identityEvidenceIds = {
    ...state.identityEvidenceIds,
    selfie: result.id,
  };
  state.identityEpoch++;
  state.photoComparison = null;
  $("comparison-result").textContent =
    "New selfie saved. Image comparison results will update when checks run.";
  state.session.verification = "pending";
  $("confirm-selfie").hidden = true;
  $("selfie-draft-help").textContent = "Selfie saved. Retake it if needed.";
  if (result.analysis)
    state.experience?.identityQuality("selfie", result.analysis);
  renderSession();
  message("Camera selfie uploaded.");
  maybeRunImageChecks();
});
$("demo-details").addEventListener("change", () => {
  if (!state.capabilities?.demo_details || $("demo-details").value !== "sample")
    return;
  const profile = state.session.enrollment || state.user;
  $("enrollment-name").value = profile.name;
  $("enrollment-reference").value =
    profile.student_number || profile.email || state.user.email;
  $("enrollment-name").dispatchEvent(new Event("input", { bubbles: true }));
  $("enrollment-result").textContent =
    "Demo details filled. Select Confirm & next.";
});
for (const field of ["enrollment-name", "enrollment-reference"]) {
  $(field).addEventListener("input", () => {
    $("enrollment-result").textContent = "Select Confirm & next to continue.";
    state.enrollmentEdited = true;
    state.session.enrollment_confirmed = false;
    renderSetup();
  });
}
bind("confirm-enrollment", async () => {
  const sessionId = state.session.id;
  const result = await api(`/api/sessions/${state.session.id}/enrollment`, {
    method: "POST",
    body: {
      name: $("enrollment-name").value,
      reference: $("enrollment-reference").value,
    },
  });
  if (state.session?.id !== sessionId) return;
  state.enrollmentEdited = false;
  state.session.enrollment_confirmed = result.confirmed;
  state.session.submitted_details = {
    name: $("enrollment-name").value,
    reference: $("enrollment-reference").value,
  };
  if (result.confirmed) selectSetupStep(2);
  else renderSetup();
  $("enrollment-result").textContent = result.confirmed
    ? "Your details were saved as entered."
    : "Enrollment check incomplete.";
});
$("retry-id").addEventListener("click", () => $("id-file").click());
$("restart-setup").addEventListener("click", () => {
  if (state.restartingSetup || state.session?.status !== "preflight") return;
  state.restartingSetup = true;
  $("restart-setup").disabled = true;
  guard(async () => {
    const next = await api("/api/sessions", {
      method: "POST",
      body: { exam_id: state.session.exam_id, fresh_start: true },
    });
    await openSession(next);
    message("New attempt created. Upload your ID and take a new selfie.");
  }).finally(() => {
    state.restartingSetup = false;
    if (state.session) renderSetup();
  });
});
$("retry-selfie").addEventListener("click", () => $("retake-selfie").click());
bind("verify", async () => {
  if (state.selfieDraft)
    throw new Error(
      "Confirm your selfie preview before requesting verification.",
    );
  if (state.identityUploadPromise) await state.identityUploadPromise;
  if (!state.identityEvidence?.id || !state.identityEvidence?.selfie)
    throw new Error(
      !state.identityEvidence?.id
        ? "Choose an ID image first; your selfie is already saved."
        : "Take a camera selfie first; your ID image is already saved.",
    );
  const result = await runImageChecks();
  if (!result) return;
  selectSetupStep(photoNeedsRetry(result) ? 3 : 4);
  if (state.session.policy.verification_mode === "institution_account")
    message(
      comparisonMessage(result),
      ["unavailable", "insufficient_quality"].includes(result.status),
    );
  else {
    selectSetupStep(4);
    message(
      "Identity review requested. Your proctor must approve before you start.",
    );
  }
});
async function maybeRunImageChecks() {
  if (
    state.session?.policy.verification_mode !== "institution_account" ||
    !state.session.policy.enable_face_comparison ||
    state.session.verification_attempts >= 5 ||
    !state.identityEvidence?.id ||
    !state.identityEvidence?.selfie ||
    state.lastCompareEpoch === state.identityEpoch
  )
    return;
  if (state.comparing) return;
  try {
    await runImageChecks();
  } catch (error) {
    $("comparison-result").textContent =
      "Advisory image checks could not complete: " + error.message;
  }
}
async function runImageChecks() {
  if (state.comparing) return;
  if (
    state.session.policy.verification_mode === "institution_account" &&
    state.session.verification_attempts >= 5
  ) {
    message(
      "Five guided image checks used. Account access and the required setup checks remain separate.",
    );
    return null;
  }
  const sessionId = state.session.id,
    epoch = state.identityEpoch;
  state.comparing = true;
  $("comparison-result").textContent = "Running advisory image checks…";
  renderSetup();
  try {
    const result = await api(`/api/sessions/${sessionId}/verification`, {
      method: "POST",
    });
    if (state.session?.id !== sessionId || state.identityEpoch !== epoch)
      return null;
    if (state.identityEpoch === epoch) {
      $("comparison-result").textContent = comparisonMessage(result);
      state.lastCompareEpoch = epoch;
      state.photoComparison = result;
      state.session.verification_attempts =
        (state.session.verification_attempts || 0) + 1;
    }
    state.session.verification = "pending_review";
    renderSession();
    return result;
  } finally {
    if (state.session?.id === sessionId) {
      state.comparing = false;
      renderSetup();
      if (state.identityEpoch !== epoch) maybeRunImageChecks();
    }
  }
}

bind("check-approval", async () => {
  const row = await api("/api/sessions/" + state.session.id);
  state.session = row;
  state.approvalCheckedAt = Date.now();
  renderSession();
});
bind("start", async () => {
  const fullscreen =
    state.session.policy.require_fullscreen !== false &&
    !document.fullscreenElement
      ? document.documentElement.requestFullscreen?.().catch(() => {})
      : Promise.resolve();
  await fullscreen;
  const devices = await heartbeat();
  const s = await api(`/api/sessions/${state.session.id}/start`, {
    method: "POST",
    body: devices,
  });
  state.session = s;
  state.experience?.beginAttempt();
  startCameraMonitoring();
  startScreenMonitoring();
  state.revision = s.answer_revision;
  state.serverOffset = s.server_time * 1000 - Date.now();
  renderQuestions(s);
  renderSession();
  message(
    "Assessment started. Answers save automatically; use Submit when finished.",
  );
});
async function submitAssessment(automatic = false) {
  if (state.submitting) throw new Error("Submission already in progress.");
  if (
    !automatic &&
    !confirm("Submit this assessment? You cannot edit responses afterward.")
  )
    return;
  const control = $("submit");
  control.disabled = true;
  state.submitting = true;
  control.textContent = "Submitting answers…";
  clearTimeout(state.saveTimeout);
  const inputs = [
    ...$("questions").querySelectorAll("input, textarea, select"),
  ];
  inputs.forEach((input) => {
    input.disabled = true;
  });
  try {
    // Finish any answer revision already in flight, independently of media uploads.
    if (state.saving) await state.saving.catch(() => {});
    const answers = collectAnswers();
    await state.queue
      .snapshot({ baseRevision: state.revision, answers })
      .catch((error) => console.error("Local submission backup failed", error));
    state.session = await api(
      `/api/sessions/${state.session.id}/submit${automatic ? "?reason=fullscreen_timeout" : ""}`,
      {
        method: "POST",
        body: { revision: state.revision + 1, answers },
      },
    );
    if (state.session.status !== "submitted") {
      renderSession();
      throw new Error(
        `This attempt is ${state.session.status}; submission was not accepted. Contact your organizer.`,
      );
    }
    if (state.session.submission_reason === "five_confirmed_violations") {
      clearIntervals();
      renderSession();
      message(
        "Your proctor-confirmed violation limit was reached. Server-saved answers were submitted; your local draft is retained for organizer follow-up.",
      );
      void stopCapture().catch((error) => console.error(error));
      return;
    }
    state.dirty = false;
    clearIntervals();
    await state.queue
      .clearSnapshot()
      .catch((error) => console.error("Local backup cleanup failed", error));
    renderSession();
    $("save-state").textContent =
      "Submitted successfully. Your final answers are saved on the server.";
    message(
      automatic
        ? "Assessment automatically submitted: fullscreen was not restored within 5 seconds. Your answers are saved."
        : "Assessment submitted successfully. Your final answers are saved.",
    );
    // Retain unsynchronized evidence locally; never discard it to make submission succeed.
    void stopCapture()
      .then(() => flushQueue())
      .catch((error) => console.error(error));
    const drainUntil = Date.now() + 115000;
    const drain = setInterval(() => {
      if (Date.now() > drainUntil) return clearInterval(drain);
      void flushQueue().catch((error) => console.error(error));
    }, 10000);
  } catch (error) {
    inputs.forEach((input) => {
      input.disabled = automatic || state.session.status !== "in_progress";
    });
    $("save-state").textContent =
      "Submission not confirmed. Keep this page open; your responses are still displayed. Try Submit again.";
    throw error;
  } finally {
    state.submitting = false;
    control.disabled = false;
    control.textContent = "Save answers and submit";
  }
}
bind("submit", () => submitAssessment(false));
bind("pair", async () => {
  const result = await api(`/api/sessions/${state.session.id}/pairing`, {
    method: "POST",
  });
  $("pair-qr").src = result.qr;
  $("pair-link").href = result.url;
  $("pair-info").hidden = false;
  state.rtc?.close();
  state.rtc = null;
  state.signalAfter = 0;
  message(
    "Pairing link expires in three minutes. Confirm the phone’s six-digit code here.",
  );
});
bind("confirm-mobile", async () => {
  await api(`/api/sessions/${state.session.id}/pairing/confirm`, {
    method: "POST",
    body: { code: $("pair-code").value },
  });
  $("pair-info").hidden = true;
  await setupRTC(false);
  message("Phone confirmed. Enable its camera on the mobile page.");
});
bind("revoke-mobile", async () => {
  await api(`/api/sessions/${state.session.id}/pairing`, { method: "DELETE" });
  state.rtc?.close();
  state.rtc = null;
  $("mobile-preview").srcObject = null;
  message("Mobile access revoked.");
});
for (const [id, decision] of [
  ["approve", "approved"],
  ["reject", "rejected"],
])
  bind(id, async () => {
    await api(`/api/sessions/${state.session.id}/verification/review`, {
      method: "POST",
      body: { decision, reason: $("review-reason").value },
    });
    message("Identity decision saved.");
    await openReview(state.session.id);
  });
bind("save-review", async () => {
  await api(`/api/sessions/${state.session.id}/review`, {
    method: "POST",
    body: {
      status: $("overall-review").value,
      reason: $("review-reason").value,
    },
  });
  message("Review saved.");
});
bind("terminate", async () => {
  if (
    !confirm(
      "End this student’s assessment? This requires a documented staff reason.",
    )
  )
    return;
  await api(`/api/sessions/${state.session.id}/terminate`, {
    method: "POST",
    body: { decision: "rejected", reason: $("review-reason").value },
  });
  message("Session ended by staff.");
  await openReview(state.session.id);
});
let lastFocusLoss = 0;
function reportFocusLoss() {
  if (state.session?.status !== "in_progress" || state.stopping) return;
  if (Date.now() - lastFocusLoss < 1500) return;
  lastFocusLoss = Date.now();
  if (state.experience) {
    state.experience.notice =
      "Assessment lost focus. Return to this exam window. This observation is recorded for review.";
    state.experience.noticeUntil = Date.now() + 12000;
    state.experience.render();
  }
  setTimeout(() => {
    if (!state.experience?.fullscreenIncident) void event("focus_lost");
  }, 150);
}
window.addEventListener("blur", reportFocusLoss);
document.addEventListener("visibilitychange", () => {
  if (document.hidden) reportFocusLoss();
});
document.addEventListener("fullscreenchange", () => {
  state.experience?.fullscreenChanged();
});
// Accessibility and IME/dictation are preserved. Input methods are not classified as cheating.
window.addEventListener("beforeunload", (e) => {
  if (state.session?.status === "in_progress" || state.dirty) {
    e.preventDefault();
    e.returnValue = "";
  }
});
window.addEventListener("online", () =>
  guard(async () => {
    await flushQueue();
    if (state.dirty) await saveAnswers();
    message("Connection restored. Pending evidence synchronized.");
  }),
);
(async () => {
  if (location.pathname === "/mobile") {
    await mobilePage();
    return;
  }
  try {
    const me = await api("/api/auth/me");
    state.user = me.user;
    state.organization = me.organization;
    state.capabilities = me.capabilities;
    state.csrf = me.csrf_token;
    renderProfile();
    $("logout").hidden = false;
    await dashboard();
  } catch (e) {
    if (e.status === 401) {
      hideViews();
      $("login-panel").hidden = false;
      setShell("login", "Sign in");
    } else throw e;
  }
})().catch((e) => message(e.message, true));

bind("calibrate-gaze", () => state.gaze?.calibrate());
$("gaze-quick-calibrate").addEventListener("click", () => {
  $("camera-monitoring-settings").open = true;
  guard(() => state.gaze?.calibrate());
});
