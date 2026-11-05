"""Motion guard: IK branches, checks, via-point planning from arbitrary poses, and the gate hook."""

import numpy as np
import pytest

from app.robot.kinematics import Kinematics
from app.robot.ur import urscript
from app.robot.ur_ik import ik_all
from app.skills.motion_guard import GuardRejected, MotionGuard, guard_from_settings, hover_and_lower
from app.skills.registry import MotionStep, ValidatedAction

KIN = Kinematics("UR3e", (0.0, 0.0, 0.0))


def _guard(**cfg):
    return MotionGuard(KIN, cfg, table_z=0.0, joint_speed=0.8)


def test_ik_all_contains_the_original_branch():
    rng = np.random.default_rng(3)
    for _ in range(100):
        q = rng.uniform(-np.pi, np.pi, 6)
        sols = ik_all(KIN, KIN.fk(q), q)
        assert any(np.allclose(np.cos(s - q), 1, atol=1e-5) for s in sols)
        for s in sols:
            assert np.allclose(KIN.fk(s), KIN.fk(q), atol=1e-4)


def test_named_poses_are_not_false_alarms(settings):
    g = guard_from_settings(settings)
    for name, p in settings.poses.items():
        assert g.config_ok(p["joints"]), name


def test_blocked_movej_gets_via_points_and_every_leg_is_safe():
    g = _guard()
    rng = np.random.default_rng(11)
    lo, hi = np.array([-3, -3.0, -2.6, -3, -3, -3]), np.array([3, 0, 2.6, 3, 3, 3])

    def valid():
        while True:
            q = rng.uniform(lo, hi)
            if g.config_ok(q):
                return q

    solved = 0
    for _ in range(8):
        while True:
            a, b = valid(), valid()
            if g.check_movej(a, b):
                break
        vias = g.plan(a, b)
        pts = [a, *vias]
        assert np.allclose(vias[-1], b)
        assert all(g.check_movej(pts[i], pts[i + 1]) is None for i in range(len(vias)))
        solved += 1
    assert solved == 8


def test_movel_across_the_base_axis_is_a_singularity():
    g = _guard()
    T = np.eye(4)
    T[:3, :3] = np.diag([1.0, -1.0, -1.0])
    T[:3, 3] = [0.25, 0.05, 0.25]
    qa = KIN.ik(T, [0, -1.6, 1.6, -1.57, -1.57, 0])
    T2 = T.copy()
    T2[:3, 3] = [-0.25, 0.05, 0.25]
    qb = min(ik_all(KIN, T2, qa), key=lambda q: np.max(np.abs(q - qa)))
    assert g.check_movel(qa, qb) is not None


def test_target_inside_the_table_is_refused():
    g = _guard()
    T = np.eye(4)
    T[:3, :3] = np.diag([1.0, -1.0, -1.0])
    T[:3, 3] = [0.3, 0.0, -0.02]
    q = ik_all(KIN, T)[0]
    with pytest.raises(GuardRejected):
        g.secure([0, -1.5708, 0, -1.5708, 0, 0], [MotionStep("movej", None, tuple(q), 0.5, 0.5)])


def test_hover_from_home_where_the_arc_planner_refuses(settings):
    g = guard_from_settings(settings)
    home = settings.poses["home"]["joints"]
    for xy in [(0.25, 0.15), (-0.2, 0.25), (0.0, -0.3)]:
        motion, info = hover_and_lower(g, home, xy, hover_flange_z=0.16, low_flange_z=0.105, speeds=settings.motion)
        tip = KIN.fk(motion[-1].joints)
        assert np.allclose(tip[:2, 3], xy, atol=0.003) and abs(tip[2, 3] - 0.105) < 0.003
        assert tip[2, 2] < -0.99                                        # tool straight down
        assert g.secure(home, motion).vias_added == 0                    # its own plan passes the re-check


def test_urscript_blend_only_on_via_points():
    s = urscript([MotionStep("movej", None, (0, 0, 0, 0, 0, 0), 0.5, 0.5, blend=0.02),
                  MotionStep("movej", None, (0.1, 0, 0, 0, 0, 0), 0.5, 0.5)])
    lines = s.splitlines()
    assert "r=0.0200" in lines[1] and "r=" not in lines[2]


async def test_gate_refuses_unsafe_motion_before_sending(gate, sim):
    T = np.eye(4)
    T[:3, :3] = np.diag([1.0, -1.0, -1.0])
    T[:3, 3] = [0.3, 0.0, -0.05]
    q = ik_all(gate.motion_guard.kin, T)[0]
    act = ValidatedAction(skill="test", params={}, motion=[MotionStep("movej", None, tuple(q), 0.5, 0.5)],
                          timeout_s=10, conferma="", risultato="", strumento="punta")
    import threading
    gate.s.robot.setdefault("guard", {})["enabled"] = True
    motion, why = await gate._guarded_motion(act, threading.Event())
    assert motion is None and "non sicuro" in why
