"""Mesh collision model: same flange pose as the DH model, and it sees gripper hits the capsules miss."""
import numpy as np
import pytest

pytest.importorskip("fcl")
pytest.importorskip("trimesh")

from app.robot.kinematics import Kinematics  # noqa: E402
from app.skills.mesh_model import MeshModel, chain  # noqa: E402
from app.skills.motion_guard import MotionGuard  # noqa: E402

GRIP = {"enabled": True, "offset_axis_flange": [0, -1, 0]}


def test_urdf_chain_matches_dh():
    k = Kinematics("UR3e")
    rng = np.random.default_rng(0)
    for _ in range(50):
        q = rng.uniform(-3, 3, 6)
        assert np.allclose(k.frames(q)[-1], chain(q)[7], atol=1e-9)


def test_home_is_clear():
    m = MeshModel(GRIP)
    assert min(m.margins([0, -1.5708, 0, -1.5708, 0, 0]).values()) > 0


def test_gripper_into_forearm_is_caught_by_mesh_not_capsules():
    q = [0.92, -0.17, -0.81, 1.6, -2.73, -2.1]          # found by random search: beam inside the forearm tube
    m = MeshModel(GRIP)
    assert m.margins(q)["pinza_forearm"] < 0
    cap = MotionGuard(Kinematics("UR3e"), {}, table_z=0, joint_speed=0.5, gripper=GRIP)
    assert cap.margins(q)["pinza_braccio"] >= 0         # the old capsule model says "clear"
    guarded = MotionGuard(Kinematics("UR3e"), {}, table_z=0, joint_speed=0.5, gripper=GRIP, mesh=MeshModel(GRIP))
    assert not guarded.config_ok(q)
