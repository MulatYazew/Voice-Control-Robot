"""Named commands are recognised without a recorded pose, and height is not capped at 10 cm."""

from app.conversation.rules import parse, parse_jog
from app.skills.cartesian import Planner
from app.skills.registry import SkillError


def test_chinese_commands_map_onto_skills(settings):
    poses = settings.poses
    cases = [
        ("把左边的托盘抬起来。", "solleva_vassoio", {"posizione": "sinistra"}),
        ("用叉子把右边那个托盘叉起来。", "solleva_vassoio", {"posizione": "destra"}),
        ("指一下中间那块区域。", "indica", {"posizione": "centro"}),
        ("回到初始位置。", "vai_a_posa", {"posa": "home"}),
        ("去等待位置。", "vai_a_posa", {"posa": "attesa"}),
        ("把托盘放到中间。", "appoggia_vassoio", {"posizione": "centro"}),
        ("把左边的托盘往右推。", "spingi", {"posizione": "sinistra", "direzione": "destra"}),
        ("往右推，左边那个托盘。", "spingi", {"posizione": "sinistra", "direzione": "destra"}),
    ]
    for text, skill, params in cases:
        got = parse(text, poses=poses)
        assert got.kind == "skill" and got.skill == skill, (text, got)
        for key, value in params.items():
            assert got.params.get(key) == value, (text, got.params)


def test_height_command_is_a_move_with_no_centimetre_cap(settings):
    jog = parse_jog("再高五厘米")
    assert jog is not None and jog.kind == "move" and jog.params["direzione"] == "su"
    jog = parse_jog("往上抬四十厘米")
    assert jog is not None and jog.params["direzione"] == "su"
    planner = Planner(settings)
    q = settings.poses["home"]["joints"]
    try:
        plan = planner.translate(q, "su", 40)
    except SkillError as e:
        assert "Al massimo" not in str(e)
        assert e.code == "fuori_dai_limiti"
        return
    assert plan.motion
    assert plan.info["cm"] == 40.0


def test_a_turn_can_pass_forty_five_degrees(settings):
    planner = Planner(settings)
    assert planner.max_deg == 360
    q = settings.poses["attesa"]["joints"]
    try:
        plan = planner.rotate(q, "base", "sinistra", 90)
    except SkillError as e:
        assert "Al massimo" not in str(e)
        assert e.code == "fuori_dai_limiti"
    else:
        assert plan.motion
    try:
        planner.rotate(q, "base", "sinistra", 361)
    except SkillError as e:
        assert e.code == "fuori_dai_limiti"
        assert "360" in str(e)
    else:
        raise AssertionError("a turn past 360 degrees was accepted")


def test_home_and_waiting_pose_keep_their_joint_list(settings):
    planner = Planner(settings)
    q = settings.poses["home"]["joints"]
    assert planner.command_plan(q, "vai_a_posa", {"posa": "home"}) is None
    assert planner.command_plan(q, "vai_a_posa", {"posa": "attesa"}) is None
    # Folded home cannot arc to the table in one Cartesian sweep. That is a reach limit,
    # not a missing recording. The gate then searches a joint route.
    try:
        planner.command_plan(q, "indica", {"posizione": "centro"})
    except SkillError as e:
        assert e.code == "fuori_dai_limiti"
        assert "registrata" not in str(e)
