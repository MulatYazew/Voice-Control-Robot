"""Four table corners are computed from the current tip, then a confirmed move reaches one."""

import numpy as np

from app.skills.cartesian import Planner


def test_corners_form_a_rectangle_around_the_tip(settings):
    planner = Planner(settings)
    q = settings.poses["sinistra_sopra"]["joints"]
    tip = planner.kin.tip(q)
    corners = planner.table_corners(q)
    assert [c["name"] for c in corners] == [
        "avanti_sinistra", "avanti_destra", "indietro_destra", "indietro_sinistra"]
    xyz = np.array([c["xyz"] for c in corners])
    assert np.allclose(xyz[:, 2], tip[2])
    width = abs(corners[2]["xyz"][0] - corners[0]["xyz"][0])
    depth = abs(corners[1]["xyz"][1] - corners[0]["xyz"][1])
    assert width >= 0.08 and depth >= 0.08


def test_table_reach_is_the_arm_not_the_cup(settings):
    planner = Planner(settings)
    q = settings.poses["sinistra_sopra"]["joints"]
    T = planner.kin.fk(q)
    cells = planner.table_reach(q, T[:3, :3], float(T[2, 3]))
    xs = [c[0] for c in cells]
    ys = [c[1] for c in cells]
    assert max(xs) - min(xs) > 0.15
    assert max(ys) - min(ys) > 0.15
    assert all(np.hypot(x, y) >= 0.10 for x, y in cells)
    tip = T[:3, 3]
    assert min(np.hypot(x - tip[0], y - tip[1]) for x, y in cells) < 0.03


def test_goto_corner_ends_on_that_point(settings):
    planner = Planner(settings)
    q = settings.poses["sinistra_sopra"]["joints"]
    corner = planner.table_corners(q)[0]
    plan = planner.goto_table_point(q, corner["xyz"], np.array(corner["rot"]))
    end = planner.kin.tip(plan.motion[-1].joints)
    assert abs(end[0] - corner["xyz"][0]) < 2e-3
    assert abs(end[1] - corner["xyz"][1]) < 2e-3
    assert abs(end[2] - corner["xyz"][2]) < 2e-3


async def test_one_tap_moves_to_the_corner(gate, sim):
    from .conftest import wait_state
    with sim._lock:
        sim._q = list(gate.s.poses["sinistra_sopra"]["joints"])
    gate.s.lang = "zh"
    before = gate.planner.kin.tip(sim.current_joints())
    assert (await gate.calibration_corners())["ok"]
    done = await gate.go_calib_corner(0)
    assert done["ok"], done
    assert await wait_state(gate, "IDLE")
    after = gate.planner.kin.tip(sim.current_joints())
    assert abs(after[0] - before[0]) > 0.03 or abs(after[1] - before[1]) > 0.03


async def test_corner_move_needs_confirmation(gate, sim):
    with sim._lock:
        sim._q = list(gate.s.poses["sinistra_sopra"]["joints"])
    gate.s.lang = "zh"
    found = await gate.calibration_corners()
    assert found["ok"] and found["corners"][0]["label"] == "前左"
    move = await gate.propose_calib_corner(0)
    assert move["ok"] and "确认" in move["frase_conferma"]
    assert gate.pending is not None
