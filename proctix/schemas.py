from typing import Literal, Annotated
import re
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator


Secret = Annotated[str, StringConstraints(strip_whitespace=False)]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, allow_inf_nan=False)


class LoginInput(Model):
    institution: str = Field(min_length=1, max_length=80, pattern=r"^[a-z0-9-]+$")
    email: str = Field(min_length=3, max_length=254)
    password: Secret = Field(min_length=1, max_length=256)
    otp: str = Field(default="", max_length=8)


class StudentNumberInput(Model):
    student_number: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9._-]+$")


class UserInput(Model):
    email: str = Field(min_length=3, max_length=254)
    name: str = Field(min_length=1, max_length=160)
    password: Secret = Field(min_length=14, max_length=256)
    role: Literal["student", "proctor"] = "student"
    student_number: str = Field(default="", max_length=80, pattern=r"^[A-Za-z0-9._-]*$")

    @field_validator("email")
    @classmethod
    def email_valid(cls, v):
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", v):
            raise ValueError("Enter an email address")
        return v.lower()


class Question(Model):
    id: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    prompt: str = Field(min_length=1, max_length=10000)
    kind: Literal["text", "choice"] = "text"
    options: list[str] = Field(default_factory=list, max_length=12)

    @model_validator(mode="after")
    def choices(self):
        if self.kind == "choice" and (len(self.options) < 2 or len(set(self.options)) != len(self.options)):
            raise ValueError("Choice questions require at least two distinct options")
        if any(len(option) > 1000 for option in self.options):
            raise ValueError("Option is too long")
        return self


class Policy(Model):
    require_seb: bool = True
    require_camera: bool = True
    require_screen: bool = True
    require_audio: bool = True
    require_mobile: bool = False
    verification_mode: Literal["human", "biometric_review", "institution_account"] = "human"
    enable_face_detection: bool = True
    enable_face_comparison: bool = False
    enable_audio_monitoring: bool = True
    enable_behavior_monitoring: bool = True
    enable_monitor_detection: bool = True
    enable_screenshots: bool = True
    enable_recording: bool = True
    adaptive_capture: bool = True
    require_fullscreen: bool = True
    restrict_copy_paste: bool = False
    restrict_context_menu: bool = False
    restrict_print_save_shortcuts: bool = False
    camera_interval_seconds: int = Field(default=3, ge=1, le=30)
    screenshot_interval_seconds: int = Field(default=5, ge=5, le=60)
    recording_fps: int = Field(default=5, ge=1, le=15)
    recording_bitrate: int = Field(default=450000, ge=100000, le=1500000)
    fullscreen_grace_seconds: int = Field(default=5, ge=5, le=60)
    max_verification_attempts: int = Field(default=5, ge=0, le=10)  # 0 means unlimited
    max_violations: int = Field(default=5, ge=1, le=100)  # review escalation threshold, never a verdict
    retention_days: int = Field(default=30, ge=1, le=365)
    allow_alternative_input: bool = True
    notice: str = Field(
        default="Camera, microphone and full-screen evidence are collected for this assessment. "
        "Enabled checks count visible faces, assess image quality, compare the submitted ID portrait and selfie, periodically compare the live face to the pre-exam selfie, "
        "and measure sound levels. Input monitoring stores aggregate activity counts, not key values or clipboard text. "
        "Automated observations require human review and do not establish misconduct. "
        "Contact your institution for accommodations or an alternative assessment.",
        max_length=8000,
    )

    @model_validator(mode="after")
    def capture_consistency(self):
        if self.require_screen and not self.enable_screenshots and not self.enable_recording:
            raise ValueError("Required display capture needs snapshots or recording enabled")
        return self


class ExamInput(Model):
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=10000)
    kind: Literal["exam", "interview"] = "exam"
    duration_minutes: int = Field(ge=1, le=480)
    questions: list[Question] = Field(min_length=1, max_length=200)
    policy: Policy = Field(default_factory=Policy)
    seb_keys: list[str] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def valid_exam(self):
        if len({q.id for q in self.questions}) != len(self.questions):
            raise ValueError("Question IDs must be unique")
        if self.policy.require_seb and not self.seb_keys:
            raise ValueError("Add approved Browser Exam Keys before enabling lockdown")
        if any(not re.fullmatch(r"[0-9a-fA-F]{64}", k) for k in self.seb_keys):
            raise ValueError("Browser Exam Keys must be 64 hex characters")
        return self


class AssignmentInput(Model):
    user_id: str = Field(min_length=36, max_length=36)
    max_attempts: int = Field(default=1, ge=1, le=10)
    extra_minutes: int = Field(default=0, ge=0, le=480)


class SessionInput(Model):
    exam_id: str = Field(min_length=36, max_length=36)
    fresh_start: bool = False


class ConsentInput(Model):
    accepted: bool


class HeartbeatInput(Model):
    camera: bool = False
    screen: bool = False
    audio: bool = False
    screen_surface: Literal["monitor", "window", "browser", "unknown"] = "unknown"


class AnswersInput(Model):
    revision: int = Field(ge=1)
    answers: dict[str, str]

    @field_validator("answers")
    @classmethod
    def limits(cls, v):
        if len(v) > 200 or any(len(k) > 64 or len(a) > 20000 for k, a in v.items()):
            raise ValueError("Answer payload too large")
        return v


EVENT_KINDS = Literal[
    "recording_started",
    "focus_lost",
    "fullscreen_exit",
    "screen_stopped",
    "camera_stopped",
    "audio_stopped",
    "multiple_monitors_reported",
    "audio_level_change",
    "input_method",
    "network_gap",
    "remote_control_indicator",
    "capture_error",
    "behavior_summary",
    "window_geometry_change",
    "fullscreen_recovery_timeout",
    "deadline_warning",
    "clipboard_action_blocked",
    "clipboard_action_observed",
    "gaze_outside_calibrated_area",
    "looking_away_estimate",
    "context_menu_blocked",
    "print_save_shortcut_blocked",
]


class EventInput(Model):
    event_id: str = Field(pattern=r"^[0-9a-f-]{36}$")
    kind: EVENT_KINDS
    # Never accept raw keystrokes, clipboard text, or arbitrary executable/client markup.
    duration_ms: int | None = Field(default=None, ge=0, le=86400000)
    value: float | None = Field(default=None, ge=0, le=100000)
    client_time: float | None = Field(default=None, ge=0)
    stream_id: str | None = Field(default=None, pattern=r"^[0-9a-f-]{36}$")
    recovery_status: Literal["pending", "restored", "timed_out"] | None = None


class EnrollmentCheck(Model):
    name: str = Field(default="", max_length=160)
    reference: str = Field(default="", max_length=254)


class BehaviorSummary(Model):
    event_id: str = Field(pattern=r"^[0-9a-f-]{36}$")
    window_seconds: int = Field(ge=10, le=300)
    key_events: int = Field(ge=0, le=100000)
    pointer_events: int = Field(ge=0, le=100000)
    input_events: int = Field(ge=0, le=100000)
    paste_events: int = Field(ge=0, le=10000)
    composition_events: int = Field(ge=0, le=10000)
    untrusted_events: int = Field(ge=0, le=100000)
    focus_changes: int = Field(ge=0, le=10000)
    # Aggregate counters only. No text, key values, clipboard content or coordinates.


class ReviewInput(Model):
    decision: Literal["approved", "rejected"]
    reason: str = Field(min_length=10, max_length=2000)


class ObservationReview(Model):
    disposition: Literal["dismissed", "needs_followup", "confirmed_by_reviewer"]
    reason: str = Field(min_length=10, max_length=2000)


class SessionReview(Model):
    status: Literal["cleared", "needs_followup", "reviewed"]
    reason: str = Field(min_length=10, max_length=2000)


class PairClaim(Model):
    token: str = Field(min_length=40, max_length=64)


class PairConfirm(Model):
    code: str = Field(pattern=r"^\d{6}$")


class SignalInput(Model):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False, allow_inf_nan=False)
    type: Literal["offer", "answer", "ice", "restart"]
    sdp: str | None = Field(default=None, max_length=64000)
    candidate: dict | None = None

    @model_validator(mode="after")
    def bounded(self):
        import json

        if len(json.dumps(self.model_dump())) > 70000:
            raise ValueError("Signal too large")
        if self.type in ("offer", "answer") and not self.sdp:
            raise ValueError("SDP required")
        if self.type == "ice" and self.candidate is None:
            raise ValueError("ICE candidate required")
        return self


class PasswordInput(Model):
    current_password: Secret = Field(min_length=1, max_length=256)
    new_password: Secret = Field(min_length=14, max_length=256)
