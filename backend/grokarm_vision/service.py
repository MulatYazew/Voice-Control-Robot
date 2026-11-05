"""Read-only vision pipeline: frame -> candidates -> chosen object -> robot XY.

Nothing here moves the robot. The robot server calls `locate`, turns the
result into its own proposal, and still needs the user's spoken "yes".
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from . import phrases
from .calibration import CalPoint, Calibration, CalibrationError, estimate_drift_px
from .config import Settings
from .detect import Candidate, annotate, find_candidates
from .frames import Frame, FrameStore, encode_jpeg
from .resolve import GrokResolver, OfflineResolver, Resolution, Resolver

SIDE_DEADBAND_MM = 40.0


@dataclass
class LocateResult:
    ok: bool
    status: str
    message: str
    lang: str
    target_mm: list[float] | None = None
    label: str = ""
    chosen_id: int | None = None
    candidates: list[dict] = field(default_factory=list)
    resolution: dict | None = None
    drift_px: float | None = None
    frame_id: int | None = None
    moved_mm: float | None = None
    timing_ms: dict = field(default_factory=dict)

    def public(self) -> dict:
        return self.__dict__.copy()


class VisionService:
    def __init__(self, settings: Settings, store: FrameStore | None = None, resolver: Resolver | None = None):
        self.settings = settings
        self.store = store or FrameStore()
        self.resolver = resolver or self._default_resolver()
        self._lock = threading.Lock()
        self.data_dir = settings.data_dir
        self.calibration: Calibration | None = None
        self.calibration_error = ""
        self.reference: np.ndarray | None = None
        self.background: np.ndarray | None = None
        self.last_annotated: bytes | None = None
        self._load()

    def _default_resolver(self) -> Resolver:
        s = self.settings
        if s.xai_api_key:
            return GrokResolver(s.xai_api_key, s.xai_vision_model, s.xai_base_url, s.xai_timeout_s)
        return OfflineResolver()

    @property
    def _cal_path(self) -> Path:
        return self.data_dir / "calibration.json"

    def _load(self) -> None:
        try:
            self.calibration = Calibration.load(self._cal_path)
        except (CalibrationError, KeyError, ValueError) as exc:
            self.calibration_error = str(exc)
        self.reference = _read_image(self.data_dir / "reference.jpg")
        self.background = _read_image(self.data_dir / "background.jpg")

    def status(self) -> dict:
        frame = self.store.latest()
        cal = self.calibration
        problems = self._setup_problems(frame)
        return {
            "camera_source": self.settings.camera_source,
            "frame": None if frame is None else {
                "id": frame.frame_id, "age_s": round(frame.age(), 2), "size": list(frame.size)},
            "calibration": None if cal is None else cal.summary(),
            "calibration_error": self.calibration_error,
            "background": None if self.background is None else list(self.background.shape[1::-1]),
            "resolver": self.resolver.name,
            "ready": not problems,
            "problems": problems,
        }

    def _setup_problems(self, frame: Frame | None) -> list[str]:
        problems = []
        if frame is None or frame.age() > self.settings.max_frame_age_s:
            problems.append("no recent camera frame")
        if self.calibration is None:
            problems.append("not calibrated")
        if self.background is None:
            problems.append("no empty-table background")
        if frame is not None and self.calibration is not None and frame.size != self.calibration.frame_size:
            problems.append("camera resolution differs from calibration")
        if (
            self.background is not None
            and self.calibration is not None
            and tuple(self.background.shape[1::-1]) != self.calibration.frame_size
        ):
            problems.append("background resolution differs from calibration")
        return problems

    def _fresh_frame(self) -> Frame:
        frame = self.store.latest()
        if frame is None or frame.age() > self.settings.max_frame_age_s:
            raise LookupError("no recent camera frame")
        return frame

    def save_calibration(self, points: list[dict]) -> dict:
        frame = self._fresh_frame()
        parsed = []
        for p in points:
            u, v = (float(x) for x in p["px"])
            x, y = (float(x) for x in p["robot_mm"])
            if not (0 <= u < frame.size[0] and 0 <= v < frame.size[1]):
                raise CalibrationError(f"pixel {u:.0f},{v:.0f} is outside the frame")
            parsed.append(CalPoint((u, v), (x, y)))
        cal = Calibration.fit(parsed, frame.size)
        with self._lock:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            cal.save(self._cal_path)
            cv2.imwrite(str(self.data_dir / "reference.jpg"), frame.image)
            self.calibration = cal
            self.calibration_error = ""
            self.reference = frame.image.copy()
        return cal.summary()

    def capture_background(self) -> dict:
        frame = self._fresh_frame()
        if self.calibration is not None and frame.size != self.calibration.frame_size:
            raise CalibrationError("camera resolution differs from calibration; calibrate again")
        drift = self._drift(frame.image)
        if drift is not None and drift > self.settings.max_drift_px:
            raise CalibrationError(f"camera moved {drift:.1f} px since calibration; calibrate again")
        with self._lock:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            cv2.imwrite(str(self.data_dir / "background.jpg"), frame.image)
            self.background = frame.image.copy()
        return {"size": list(frame.size), "drift_px": drift}

    def _drift(self, image: np.ndarray) -> float | None:
        if self.reference is None or not self.settings.drift_check:
            return None
        return estimate_drift_px(self.reference, image)

    def locate(
        self,
        query: str,
        lang: str | None = None,
        expect_mm: tuple[float, float] | None = None,
        tolerance_mm: float = 15.0,
    ) -> LocateResult:
        lang = phrases.lang_or_default(lang)
        t0 = time.perf_counter()
        frame = self.store.latest()
        problems = self._setup_problems(frame)
        if problems:
            if "no recent camera frame" in problems:
                return LocateResult(False, "no_frame", phrases.say("no_frame", lang), lang)
            return LocateResult(
                False, "not_ready", phrases.say("not_ready", lang, what=", ".join(problems)), lang
            )
        assert frame is not None and self.calibration is not None and self.background is not None
        cal = self.calibration
        image = frame.image

        drift = self._drift(image)
        t_drift = time.perf_counter()
        if drift is None and self.settings.drift_check:
            return LocateResult(False, "camera_check_failed", phrases.say("camera_check_failed", lang), lang,
                                frame_id=frame.frame_id)
        if drift is not None and drift > self.settings.max_drift_px:
            return LocateResult(False, "camera_moved", phrases.say("camera_moved", lang), lang,
                                drift_px=round(drift, 1), frame_id=frame.frame_id)

        candidates = find_candidates(
            image,
            self.background,
            diff_threshold=self.settings.diff_threshold,
            min_area_frac=self.settings.min_area_frac,
            max_area_frac=self.settings.max_area_frac,
        )
        for c in candidates:
            c.robot_mm = cal.to_mm(*c.px)
            c.inside_workspace = cal.inside_workspace(c.robot_mm, self.settings.workspace_margin_mm)
        t_detect = time.perf_counter()

        base = dict(lang=lang, drift_px=None if drift is None else round(drift, 1), frame_id=frame.frame_id,
                    candidates=[c.public() for c in candidates])
        if not candidates:
            self._save_annotated(image, candidates, [])
            return LocateResult(False, "empty_table", phrases.say("empty_table", lang), **base)

        resolution = self.resolver.resolve(query, lang, candidates, image, self.settings.left_axis_sign)
        t_resolve = time.perf_counter()
        base["resolution"] = resolution.public()
        base["timing_ms"] = {
            "drift": _ms(t0, t_drift), "detect": _ms(t_drift, t_detect),
            "resolve": _ms(t_detect, t_resolve), "total": _ms(t0, t_resolve),
        }
        self._save_annotated(image, candidates, resolution.ids)

        if resolution.match == "none":
            return LocateResult(False, "not_found", phrases.say("none", lang), **base)
        if resolution.match == "ambiguous":
            pool = [c for c in candidates if c.id in resolution.ids]
            return LocateResult(False, "ambiguous", self._ambiguity_message(lang, pool, resolution), **base)

        chosen = next(c for c in candidates if c.id == resolution.ids[0])
        label = resolution.label if resolution.source != "offline" and resolution.label else \
            phrases.object_label(lang, chosen.colour)
        assert chosen.robot_mm is not None
        target = [round(chosen.robot_mm[0], 1), round(chosen.robot_mm[1], 1)]
        base.update(label=label, chosen_id=chosen.id, target_mm=target)
        if not chosen.inside_workspace:
            return LocateResult(False, "outside_workspace",
                                phrases.say("outside_workspace", lang, label=label), **base)
        if expect_mm is not None:
            moved = float(np.hypot(target[0] - expect_mm[0], target[1] - expect_mm[1]))
            base["moved_mm"] = round(moved, 1)
            if moved > tolerance_mm:
                return LocateResult(False, "target_moved",
                                    phrases.say("target_moved", lang, label=label, dist=int(round(moved))), **base)
        dist = int(round(float(np.hypot(*chosen.robot_mm))))
        message = phrases.say("found", lang, label=label, side=self._side(lang, chosen), dist=dist)
        return LocateResult(True, "found", message, **base)

    def _side(self, lang: str, c: Candidate) -> str:
        y = self.settings.left_axis_sign * (c.robot_mm[1] if c.robot_mm else 0.0)
        key = "left" if y > SIDE_DEADBAND_MM else "right" if y < -SIDE_DEADBAND_MM else "centre"
        return phrases.side(lang, key)

    def _ambiguity_message(self, lang: str, pool: list[Candidate], resolution: Resolution) -> str:
        if resolution.question:
            return resolution.question
        cjk = lang in ("zh", "ja")
        sep, gap = ("、", "") if cjk else (", ", " ")
        options = sep.join(f"{phrases.object_label(lang, c.colour)}{gap}{self._side(lang, c)}" for c in pool)
        return phrases.say("ambiguous", lang, n=len(pool), options=options)

    def _save_annotated(self, image: np.ndarray, candidates: list[Candidate], chosen: list[int]) -> None:
        out = annotate(image, candidates, chosen)
        if self.calibration is not None:
            inv = np.linalg.inv(self.calibration.homography)
            poly = self.calibration.workspace_polygon().reshape(-1, 1, 2).astype(np.float64)
            px = cv2.perspectiveTransform(poly, inv).astype(np.int32)
            cv2.polylines(out, [px], True, (255, 200, 0), 2)
        self.last_annotated = encode_jpeg(out)


def _ms(a: float, b: float) -> float:
    return round((b - a) * 1000.0, 1)


def _read_image(path: Path) -> np.ndarray | None:
    if not path.exists():
        return None
    return cv2.imread(str(path), cv2.IMREAD_COLOR)
