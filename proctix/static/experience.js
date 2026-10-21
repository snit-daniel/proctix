"use strict";
// Candidate-facing monitoring: observations and device facts, never verdicts.
class MonitoringExperience {
  constructor(context) {
    this.context = context;
    this.controller = new AbortController();
    this.lastPoll = 0;
    this.polling = false;
    this.stats = {
      snapshots: 0,
      chunks: 0,
      bytes: 0,
      pending: 0,
      pendingBytes: 0,
      throughput: null,
    };
    this.counts = this.emptyCounts();
    this.periodStart = Date.now();
    this.warnings = new Set();
    this.sound = 0;
    this.fullscreenLost = null;
    this.fullscreenReported = false;
    this.lastResize = 0;
    this.resizeIgnoreUntil = Date.now() + 700;
    this.profile = {
      name: "Standard",
      width: 1280,
      fps: 5,
      bitrate: 450000,
      screenSeconds: 5,
      cameraSeconds: 3,
    };
    const signal = this.controller.signal;
    const count = (key, e) => {
      if (!this.active() || !this.policy.enable_behavior_monitoring) return;
      this.counts[key]++;
      if (e && !e.isTrusted) this.counts.untrusted_events++;
    };
    document.addEventListener("keydown", (e) => count("key_events", e), {
      signal,
    });
    document.addEventListener(
      "pointerdown",
      (e) => count("pointer_events", e),
      { signal },
    );
    document.addEventListener("input", (e) => count("input_events", e), {
      signal,
    });
    document.addEventListener(
      "compositionend",
      (e) => count("composition_events", e),
      { signal },
    );
    document.addEventListener(
      "visibilitychange",
      (e) => count("focus_changes", e),
      { signal },
    );
    document.addEventListener(
      "paste",
      (e) => {
        count("paste_events", e);
        this.clipboard(e);
      },
      { signal },
    );
    document.addEventListener("copy", (e) => this.clipboard(e), { signal });
    document.addEventListener("cut", (e) => this.clipboard(e), { signal });
    document.addEventListener(
      "contextmenu",
      (e) => {
        if (
          this.active() &&
          this.policy.restrict_context_menu &&
          e.target.closest("#questions")
        ) {
          e.preventDefault();
          this.context.event("context_menu_blocked");
          this.el("live-warning").textContent =
            "The answer-editor context menu is disabled by your organizer.";
        }
      },
      { signal },
    );
    document.addEventListener(
      "keydown",
      (e) => {
        if (
          (e.ctrlKey || e.metaKey) &&
          ["c", "x", "v"].includes(e.key.toLowerCase())
        )
          this.clipboard(e);
        if (
          this.active() &&
          e.target.closest?.("#questions") &&
          (e.ctrlKey || e.metaKey)
        ) {
          e.preventDefault();
          e.stopImmediatePropagation();
          this.el("live-warning").textContent =
            "Control and Command shortcuts are disabled while answering. Browser and operating-system shortcuts may require Safe Exam Browser.";
        }
      },
      { signal, capture: true },
    );
    window.addEventListener(
      "resize",
      () => {
        this.drawBoxes();
        if (!this.active() || Date.now() < (this.resizeIgnoreUntil || 0))
          return;
        this.resizeRecoveryRequired = true;
        this.startFullscreenIncident("window_geometry_change");
        this.checkFullscreen();
      },
      { signal },
    );
    this.el("check-displays").addEventListener(
      "click",
      () => this.checkDisplays(),
      { signal },
    );
    this.el("recover-fullscreen").addEventListener(
      "click",
      () => this.recoverFullscreen(),
      { signal },
    );
    document.addEventListener(
      "keydown",
      (e) => {
        if (e.key === "Tab" && !this.el("fullscreen-recovery").hidden) {
          e.preventDefault();
          (this.el("recover-fullscreen").disabled
            ? this.el("fullscreen-recovery")
            : this.el("recover-fullscreen")
          ).focus();
        }
      },
      { signal },
    );
    this.el("report-remote-help").addEventListener(
      "click",
      () => {
        this.context.event("remote_control_indicator");
        this.el("remote-capability").textContent =
          "Report requested as a client-reported observation. Check upload synchronization and contact your organizer for assistance.";
      },
      { signal },
    );
    this.interval = setInterval(() => this.tick(), 1000);
    this.tick();
  }
  el(id) {
    return document.getElementById(id);
  }
  get state() {
    return this.context.state();
  }
  get policy() {
    return this.state.session?.policy || {};
  }
  active() {
    return this.state.session?.status === "in_progress" && !this.state.stopping;
  }
  beginAttempt() {
    clearTimeout(this.fullscreenTimer);
    clearTimeout(this.fullscreenRetry);
    this.fullscreenSubmissionPending = false;
    this.fullscreenIncident = null;
    this.resizeRecoveryRequired = false;
    this.resizeIgnoreUntil = Date.now() + 700;
    this.counts = this.emptyCounts();
    this.periodStart = Date.now();
    this.face = null;
    this.data = null;
    this.warnings.clear();
    this.el("time-warning").hidden = true;
    this.el("fullscreen-recovery").hidden = true;
    this.sound = 0;
    this.noticeUntil = 0;
    this.fullscreenLost = null;
    this.fullscreenReported = false;
    this.lastPoll = 0;
    this.render();
    void this.poll();
  }
  emptyCounts() {
    return {
      key_events: 0,
      pointer_events: 0,
      input_events: 0,
      paste_events: 0,
      composition_events: 0,
      untrusted_events: 0,
      focus_changes: 0,
    };
  }
  stop() {
    this.controller.abort();
    clearInterval(this.interval);
    clearTimeout(this.fullscreenTimer);
    clearTimeout(this.fullscreenRetry);
    for (const id of [
      "time-warning",
      "fullscreen-recovery",
      "assessment-alert",
      "assessment-observations",
      "monitoring-panel",
    ])
      this.el(id).hidden = true;
    this.el("time-warning").textContent = "";
    this.el("fullscreen-help").textContent = "";
    this.fullscreenLost = null;
    this.warnings.clear();
    this.el("face-overlay").getContext("2d").clearRect(0, 0, 1920, 1920);
  }
  clipboard(e) {
    if (!this.active() || !e.target.closest?.("#questions")) return;
    const action =
      e.type === "keydown"
        ? { c: "copy", x: "cut", v: "paste" }[e.key.toLowerCase()]
        : e.type;
    if (!action) return;
    if (this.policy.restrict_copy_paste) e.preventDefault();
    this.notice = `${action[0].toUpperCase() + action.slice(1)} ${this.policy.restrict_copy_paste ? "blocked by assessment policy" : "observed in assessment answers"}. No clipboard content is collected.`;
    this.noticeUntil = Date.now() + 12000;
    if (Date.now() - (this.lastClipboard || 0) > 2000) {
      this.context.event(
        this.policy.restrict_copy_paste
          ? "clipboard_action_blocked"
          : "clipboard_action_observed",
      );
      this.lastClipboard = Date.now();
    }
    this.render();
  }
  uploaded(url, bytes, elapsed, result) {
    if (
      !new URL(url, location.origin).pathname.startsWith(
        `/api/sessions/${this.state.session?.id}/`,
      )
    )
      return;
    this.stats.bytes += bytes || 0;
    const kind = new URL(url, location.origin).searchParams.get("kind");
    if (!result.duplicate && ["camera", "screen", "mobile"].includes(kind))
      this.stats.snapshots++;
    if (!result.duplicate && ["recording", "mobile_recording"].includes(kind))
      this.stats.chunks++;
    if (elapsed > 0 && bytes > 1000) {
      const rate = (bytes * 8) / (elapsed / 1000) / 1000;
      this.stats.throughput =
        this.stats.throughput === null
          ? rate
          : 0.75 * this.stats.throughput + 0.25 * rate;
    }
    if (kind === "camera" && result.analysis)
      this.face = { details: result.analysis, received_at: Date.now() / 1000 };
    if (result.analysis && ["id", "selfie"].includes(kind))
      this.identityQuality(kind, result.analysis);
    this.adapt();
    this.render();
  }
  adapt() {
    if (!this.policy.adaptive_capture) return;
    const target = captureProfile(
      this.stats.throughput,
      this.stats.pendingBytes,
      this.policy,
    );
    if (this.profile.name !== target.name) {
      if (this.proposed !== target.name) {
        this.proposed = target.name;
        this.proposalAt = Date.now();
        return;
      }
      if (Date.now() - this.proposalAt < 10000) return;
      this.profile = target;
      this.context.adapt?.(target);
    } else this.profile = target;
  }
  identityQuality(kind, result) {
    const expected = this.state.identityEvidenceIds?.[kind];
    if (
      !this.state.identityEvidence?.[kind] ||
      !expected ||
      result?.evidence_id !== expected
    )
      return;
    this.state.identityQuality ||= {};
    this.state.identityQuality[kind] = result;
    const e = this.el(kind === "id" ? "id-quality" : "selfie-quality");
    e.textContent =
      (kind === "id" ? "Saved ID image: " : "Saved selfie: ") +
      qualityMessage(result);
    e.classList.toggle(
      "field-error",
      result.status === "assessed" &&
        !(result.comparison_usable ?? result.quality_ok),
    );
  }
  clearIdentityQuality(kind, saved = false) {
    if (this.state.identityQuality) delete this.state.identityQuality[kind];
    const e = this.el(kind + "-quality");
    e.classList.remove("field-error");
    e.textContent = saved
      ? "Saved image has no current photo-check result."
      : kind === "id"
        ? "Upload an ID image to run photo checks."
        : "Capture and confirm a selfie to run photo checks.";
  }
  async poll() {
    if (this.polling || !this.state.session || this.state.stopping) return;
    this.polling = true;
    try {
      const phase = this.state.session.status;
      const data = await this.context.api(
        `/api/sessions/${this.state.session.id}/monitoring`,
      );
      if (this.controller.signal.aborted || this.state.session.status !== phase)
        return;
      this.data = data;
      this.face = data.latest.face_sample;
      this.lastSuccess = Date.now();
      for (const kind of ["id", "selfie"]) {
        const result = data.latest[kind + "_quality"]?.details;
        if (
          this.state.identityEvidence?.[kind] &&
          result &&
          result.evidence_id === this.state.identityEvidenceIds?.[kind]
        )
          this.identityQuality(kind, result);
        else
          this.clearIdentityQuality(
            kind,
            !!this.state.identityEvidence?.[kind],
          );
      }
      const documentChecks = data.latest.document_checks?.details;
      if (
        documentChecks?.primary_evidence_id ===
        this.state.identityEvidenceIds?.id
      )
        this.state.documentChecks =
          documentChecks.side === "id_back" ? null : documentChecks;
      else if (
        !documentChecks &&
        data.identity_evidence_ids?.id === this.state.identityEvidenceIds?.id
      )
        this.state.documentChecks = null;
      const comparison = data.latest.identity_comparison?.details;
      const back = data.latest.document_back_checks?.details;
      this.state.documentBackChecks =
        back?.primary_evidence_id === this.state.identityEvidenceIds?.id
          ? back
          : null;
      if (this.state.documentBackChecks)
        this.el("id-back-result").textContent =
          "Supporting document saved. Your main ID photo is unchanged.";
      const ids = comparison?.evidence_ids;
      if (
        comparison &&
        !this.state.comparing &&
        ids &&
        ids.id === this.state.identityEvidenceIds?.id &&
        ids.selfie === this.state.identityEvidenceIds?.selfie &&
        ids.id === data.latest.id_quality?.details.evidence_id &&
        ids?.selfie === data.latest.selfie_quality?.details.evidence_id
      ) {
        this.state.photoComparison = comparison;
        this.el("comparison-result").textContent =
          comparisonMessage(comparison);
      }
      this.context.identityUpdated?.();
      this.render();
    } catch (error) {
      this.el("monitoring-sync").textContent =
        "Monitoring results could not refresh. Local capture and the encrypted retry queue may still be active.";
    } finally {
      this.polling = false;
    }
  }
  async tick() {
    if (this.controller.signal.aborted) return;
    this.render();
    this.checkFullscreen();
    this.checkTime();
    if (Date.now() - this.lastPoll >= 3000) {
      this.lastPoll = Date.now();
      this.poll();
    }
    if (Date.now() - this.periodStart >= 30000) {
      const elapsed = Math.round((Date.now() - this.periodStart) / 1000),
        counts = this.counts;
      this.counts = this.emptyCounts();
      this.periodStart = Date.now();
      if (this.active() && this.policy.enable_behavior_monitoring) {
        try {
          await this.context.behavior({
            event_id: crypto.randomUUID(),
            window_seconds: Math.min(elapsed, 300),
            ...counts,
          });
        } catch (error) {
          this.el("behavior-status").textContent =
            "Activity summary awaiting synchronization.";
        }
      }
    }
  }
  render() {
    const s = this.state;
    this.el("monitoring-panel").hidden = !this.active();
    this.el("assessment-observations").hidden = !this.active();
    const liveVideo = s.camera
      ?.getVideoTracks()
      .some((t) => t.readyState === "live");
    const faceFresh =
      this.active() &&
      this.face &&
      Date.now() / 1000 - this.face.received_at <=
        Math.max(12, (this.policy.camera_interval_seconds || 3) * 3);
    const face = this.face?.details;
    const continuity = this.data?.latest?.face_continuity_sample;
    this.el("face-continuity-status").textContent = !this.active()
      ? "Live face consistency starts after Begin."
      : !this.policy.enable_face_comparison
        ? "Live face consistency disabled by organizer."
        : !continuity || Date.now() / 1000 - continuity.received_at > 60
          ? "Live face consistency: waiting for a clear single-face sample. Checks run at most every 15 seconds."
          : continuity.details.status !== "compared"
            ? "Live face consistency unavailable: improve lighting and face position."
            : continuity.details.below_reference_threshold
              ? "Live face similarity is low. Face the camera in good lighting; this is an observation, not an identity verdict."
              : "Live face comparison completed. This does not verify identity or liveness.";

    this.el("face-live-status").textContent = !this.policy.enable_face_detection
      ? "Face counting disabled by organizer"
      : !liveVideo
        ? "Connect camera to start"
        : !faceFresh
          ? "Waiting for a recent camera sample"
          : face?.status !== "assessed"
            ? "Face model unavailable"
            : face.face_count === 0
              ? "Face not detected in the latest sample"
              : `${face.face_count} face${face.face_count === 1 ? "" : "s"} detected · sampled check`;
    this.el("live-warning").textContent =
      Date.now() < (this.noticeUntil || 0)
        ? this.notice
        : faceFresh && face?.status === "assessed"
          ? face.face_count > 1
            ? "More than one face is visible. Ask others to leave the camera view."
            : face.face_count === 0
              ? "Face not detected. Return to camera view and improve lighting or camera position. Repeated missing-face samples are recorded for staff review."
              : !face.quality_ok
                ? "Camera warning: your face is detected, but the image is unclear. Improve lighting, focus or distance. This is not an eye-direction check and does not count as a violation."
                : ""
          : "";
    // Show quiet but measurable inputs on a dB scale rather than a nearly
    // invisible linear RMS bar. This is a level meter, not a speech score.
    this.el("sound-meter").value =
      this.sound > 0
        ? Math.max(0, Math.min(1, (20 * Math.log10(this.sound) + 75) / 75))
        : 0;
    this.el("sound-value").textContent =
      this.sound > 0
        ? `${Math.round(20 * Math.log10(Math.max(this.sound, 0.000001)))} dBFS`
        : "No current sound-level sample";
    this.el("recording-live-status").textContent =
      s.recorder?.state === "recording"
        ? "Recording active"
        : this.policy.enable_recording
          ? "Recording not active"
          : "Recording disabled by organizer";
    this.el("capture-counts").textContent =
      `${this.stats.snapshots} synchronized snapshots · ${this.stats.chunks} recording chunks this page visit`;
    this.el("capture-profile").textContent =
      `${this.profile.name} · ${this.profile.width}px · up to ${Math.min(this.profile.fps, this.policy.recording_fps || 5)} fps`;
    this.el("network-measurement").textContent =
      `${this.stats.throughput === null ? "Measuring uploads" : `Observed upload rate: ${Math.round(this.stats.throughput)} kbps (includes processing)`} · ${this.stats.pending} pending · ${(this.stats.pendingBytes / 1048576).toFixed(1)} MB queued`;
    this.el("behavior-status").textContent = this.policy
      .enable_behavior_monitoring
      ? `Aggregate input monitoring · ${this.counts.key_events} key events, ${this.counts.input_events} input events in this window. No key values or clipboard text collected.`
      : "Input summaries disabled by organizer";
    this.el("observation-count").textContent =
      "No observations in this attempt yet";
    this.el("recent-observations").replaceChildren();
    this.el("confirmed-violation-count").textContent =
      `${this.data?.confirmed_violation_count || 0} / 5 proctor-confirmed violations. At five, your latest server-saved answers are automatically submitted. Automated observations alone do not count.`;
    // Auto‑submit if confirmed violations reach threshold
    if (this.active() && !this.state.submitting &&
        (this.data?.confirmed_violation_count || 0) >= 5) {
      void this.context.submit().catch((error) => {
        this.el("monitoring-sync").textContent = error.message;
      });
    }
    if (this.active() && this.data) {
      this.el("observation-count").textContent = this.data.observation_count
        ? `${this.data.observation_count} observation${this.data.observation_count === 1 ? "" : "s"} · requires review`
        : "No incidents requiring review";
      this.el("monitoring-sync").textContent =
        `Last result refresh: ${new Date(this.lastSuccess).toLocaleTimeString()}`;
      this.el("recent-observations").replaceChildren(
        ...this.data.recent.slice(0, 10).map((o) => {
          const li = document.createElement("li");
          li.textContent = `${o.name || o.kind.replaceAll("_", " ")} · ${new Date(o.received_at * 1000).toLocaleTimeString()}`;
          return li;
        }),
      );
    }
    const warnings = this.data?.recent_warnings || [];
    this.el("camera-warning-history").hidden = !warnings.length;
    this.el("camera-warning-count").textContent =
      `${this.data?.warning_count || 0} camera warnings · excluded from violation counts`;
    this.el("recent-camera-warnings").replaceChildren(
      ...warnings.slice(0, 3).map((warning) => {
        const item = document.createElement("li");
        item.textContent = `${warning.name} · ${new Date(warning.received_at * 1000).toLocaleTimeString()}`;
        return item;
      }),
    );
    const top = this.el("assessment-alert");
    const recent = this.data?.recent?.[0];
    const guidance = {
      focus_lost: "Assessment lost focus. Return to the assessment window.",
      fullscreen_exit: "Full screen closed. Use Resume full screen above.",
      fullscreen_recovery_timeout:
        "Full-screen recovery time elapsed. Resume full screen to continue monitoring.",
      gaze_outside_calibrated_area:
        "A calibrated gaze estimate stayed outside the screen area. This is a client-reported estimate and needs review, not proof of looking at unauthorized material.",
      looking_away_estimate:
        "Eye/head direction suggests looking away. This is an approximate observation for review.",
      possible_person_change:
        "Live face appearance differs from the pre-exam selfie across two checks. Face the camera in good lighting. Recorded for review; this is not proof of a person change.",
      multiple_faces:
        "More than one face is visible. Ask others to leave the camera view.",
      no_face_visible:
        "Face not detected. Return to camera view. Recorded for staff review.",
      audio_level_change:
        "Sustained sound detected. Check your surroundings and microphone.",
      camera_stopped: "Camera disconnected. Re-enable camera capture.",
      screen_stopped:
        "Display sharing stopped. Re-enable entire display capture.",
    };
    top.hidden = !this.active();
    top.textContent =
      this.el("live-warning").textContent ||
      (recent
        ? `Observation: ${guidance[recent.kind] || recent.kind.replaceAll("_", " ")} · ${new Date(recent.received_at * 1000).toLocaleTimeString()}. Recorded for review.`
        : "Monitoring active. Device and assessment observations will appear here.");
    if (this.active() && this.data?.observation_count)
      top.textContent = `${this.data.observation_count} observations · ${top.textContent}`;
    this.el("check-displays").disabled = !this.policy.enable_monitor_detection;
    this.drawBoxes();
  }
  drawBoxes() {
    const canvas = this.el("face-overlay"),
      video = this.el("camera-preview"),
      ctx = canvas.getContext("2d");
    const width = video.clientWidth,
      height = video.clientHeight;
    canvas.width = width;
    canvas.height = height;
    if (!this.policy.enable_face_detection) return;
    if (
      !width ||
      !height ||
      !video.videoWidth ||
      !this.face ||
      Date.now() / 1000 - this.face.received_at >
        Math.max(12, (this.policy.camera_interval_seconds || 3) * 3)
    )
      return;
    const scale = Math.max(
        width / video.videoWidth,
        height / video.videoHeight,
      ),
      ox = (width - video.videoWidth * scale) / 2,
      oy = (height - video.videoHeight * scale) / 2;
    ctx.lineWidth = 3;
    ctx.strokeStyle = "#51e0a0";
    for (const box of this.face.details.boxes || [])
      ctx.strokeRect(
        box.x * video.videoWidth * scale + ox,
        box.y * video.videoHeight * scale + oy,
        box.width * video.videoWidth * scale,
        box.height * video.videoHeight * scale,
      );
  }
  async checkDisplays() {
    const target = this.el("display-inventory");
    try {
      let count = null;
      if (window.getScreenDetails) {
        this.screenDetails = await window.getScreenDetails();
        count = this.screenDetails.screens.length;
        this.screenDetails.addEventListener(
          "screenschange",
          () => this.showDisplayCount(this.screenDetails.screens.length),
          { signal: this.controller.signal },
        );
      } else if (screen.isExtended === true) {
        target.textContent =
          "Multiple displays are indicated; this browser cannot provide the exact count.";
        if (this.active()) this.context.event("multiple_monitors_reported");
        return;
      }
      if (count !== null) this.showDisplayCount(count);
      else
        target.textContent =
          "This browser cannot provide a reliable display count. A shared entire display does not prove that no other display exists.";
    } catch (error) {
      target.textContent =
        "Display inventory permission was denied or is unavailable. Entire-display sharing remains a separate check.";
    }
  }
  showDisplayCount(count) {
    this.el("display-inventory").textContent =
      `${count} display${count === 1 ? "" : "s"} reported by this browser. This is client-reported information.`;
    if (count > 1 && this.active())
      this.context.event("multiple_monitors_reported", { value: count });
  }
  startFullscreenIncident(kind) {
    if (!this.active() || this.fullscreenIncident) return;
    this.fullscreenIncident = {
      event_id: crypto.randomUUID(),
      kind,
      started: Date.now(),
    };
    this.fullscreenLost = Date.now();
    this.reportFullscreenIncident("pending");
    this.fullscreenTimer = setTimeout(() => this.checkFullscreen(), 5000);
  }
  reportFullscreenIncident(recovery_status) {
    const incident = this.fullscreenIncident;
    if (!incident) return;
    const details = {
      event_id: incident.event_id,
      recovery_status,
      duration_ms: Math.min(86400000, Date.now() - incident.started),
    };
    this.incidentWrite = Promise.resolve(this.incidentWrite).then(() =>
      this.context.event(incident.kind, details),
    );
  }
  fullscreenChanged() {
    if (document.fullscreenElement) {
      this.resizeIgnoreUntil = Date.now() + 700;
      this.resizeRecoveryRequired = false;
    } else if (this.active() && this.policy.require_fullscreen) {
      this.startFullscreenIncident("fullscreen_exit");
    }
    this.checkFullscreen();
  }
  async recoverFullscreen() {
    if (this.fullscreenSubmissionPending) return;
    try {
      if (!document.fullscreenElement)
        await document.documentElement.requestFullscreen();
      this.resizeRecoveryRequired = false;
      this.resizeIgnoreUntil = Date.now() + 700;
      this.el("fullscreen-help").textContent = "Fullscreen restored.";
      this.checkFullscreen();
    } catch (error) {
      this.el("fullscreen-help").textContent =
        "Fullscreen could not start here. Open the assessment in a regular supported browser or contact your organizer.";
    }
  }
  checkFullscreen() {
    const banner = this.el("fullscreen-recovery");
    if (this.fullscreenSubmissionPending && this.active()) return;
    if (
      !this.active() ||
      (!this.resizeRecoveryRequired &&
        (!this.policy.require_fullscreen || document.fullscreenElement))
    ) {
      if (this.fullscreenIncident && this.active())
        this.reportFullscreenIncident("restored");
      this.fullscreenIncident = null;
      banner.hidden = true;
      if (this.fullscreenPreviousFocus?.isConnected && this.active())
        this.fullscreenPreviousFocus.focus({ preventScroll: true });
      this.fullscreenPreviousFocus = null;
      this.fullscreenLost = null;
      this.fullscreenReported = false;
      clearTimeout(this.fullscreenTimer);
      return;
    }
    if (this.fullscreenLost === null) {
      this.startFullscreenIncident("fullscreen_exit");
    }
    const remaining = Math.max(
      0,
      5 - Math.floor((Date.now() - this.fullscreenLost) / 1000),
    );
    const firstWarning = banner.hidden;
    banner.hidden = false;
    this.el("fullscreen-countdown").textContent = String(remaining);
    this.el("recover-fullscreen").disabled = remaining === 0;
    if (firstWarning) {
      this.fullscreenPreviousFocus = document.activeElement;
      this.el("recover-fullscreen").focus({ preventScroll: true });
    }
    this.el("fullscreen-help").textContent = remaining
      ? `Return to fullscreen within ${remaining} seconds or your exam will submit automatically.`
      : "Time is up. Submitting your answers…";
    if (!remaining && !this.fullscreenReported) {
      this.fullscreenReported = true;
      this.reportFullscreenIncident("timed_out");
      this.fullscreenSubmissionPending = true;
      void this.submitFullscreen();
    }
  }
  async submitFullscreen() {
    if (!this.active() || this.controller.signal.aborted) return;
    try {
      await this.incidentWrite;
      await this.context.submit();
    } catch {
      if (!this.active() || this.controller.signal.aborted) return;
      this.el("fullscreen-help").textContent =
        "Submission not confirmed. Keep this page open; retrying automatically…";
      this.fullscreenRetry = setTimeout(
        () => void this.submitFullscreen(),
        3000,
      );
    }
  }
  checkTime() {
    const deadline = this.state.session?.deadline_at;
    if (!this.active() || !deadline) {
      this.el("time-warning").hidden = true;
      this.el("time-warning").textContent = "";
      return;
    }
    const remaining = Math.ceil(
      deadline - (Date.now() + (this.state.serverOffset || 0)) / 1000,
    );
    for (const threshold of [300, 60])
      if (
        remaining > 0 &&
        remaining <= threshold &&
        !this.warnings.has(threshold)
      ) {
        this.warnings.add(threshold);
        this.el("time-warning").hidden = false;
        this.el("time-warning").textContent =
          remaining <= 60
            ? "One minute or less remains. Your answers save automatically."
            : "Five minutes or less remain. Review your responses.";
        this.context.event("deadline_warning", { value: threshold });
      }
  }
}
