"""The teammate's Chinese sentences, a vague lift, and the short voice turn."""

import asyncio

from app.conversation.rules import parse, parse_jog
from app.voice.grok_session import GrokSession

from .conftest import wait_state


CLEAR = [
    ("把左边的托盘抬起来。", "solleva_vassoio", {"posizione": "sinistra"}),
    ("用叉子把右边那个托盘叉起来。", "solleva_vassoio", {"posizione": "destra"}),
    ("指一下中间那块区域。", "indica", {"posizione": "centro"}),
    ("回到初始位置。", "vai_a_posa", {"posa": "home"}),
    ("去等待位置。", "vai_a_posa", {"posa": "attesa"}),
    ("把托盘放到中间。", "appoggia_vassoio", {"posizione": "centro"}),
    ("把左边的托盘往右推。", "spingi", {"posizione": "sinistra", "direzione": "destra"}),
    ("左边那个，抬一下。", "solleva_vassoio", {"posizione": "sinistra"}),
    ("回家回家。", "vai_a_posa", {"posa": "home"}),
    ("你给我把右边那盘端起来。", "solleva_vassoio", {"posizione": "destra"}),
    ("中间，戳一下。", "indica", {"posizione": "centro"}),
    ("搁中间吧。", "appoggia_vassoio", {"posizione": "centro"}),
    ("把右边那个往左边怼一下。", "spingi", {"posizione": "destra", "direzione": "sinistra"}),
    ("麻烦您帮我把左边的托盘抬起来，谢谢。", "solleva_vassoio", {"posizione": "sinistra"}),
    ("能不能请你指一下右边？", "indica", {"posizione": "destra"}),
    ("辛苦啦，先回原位歇着吧。", "vai_a_posa", {"posa": "home"}),
    ("托盘，左边的，抬。", "solleva_vassoio", {"posizione": "sinistra"}),
    ("往右推，左边那个托盘。", "spingi", {"posizione": "sinistra", "direzione": "destra"}),
    ("把左边的 tray lift 一下。", "solleva_vassoio", {"posizione": "sinistra"}),
    ("go home 吧。", "vai_a_posa", {"posa": "home"}),
    ("抬左边的，哦不对，右边的。", "solleva_vassoio", {"posizione": "destra"}),
    ("不是中间，是左边。", "indica", {"posizione": "sinistra"}),
    ("我都说了多少遍了，抬左边的！", "solleva_vassoio", {"posizione": "sinistra"}),
    ("你聋了吗？回原位！", "vai_a_posa", {"posa": "home"}),
    ("快点快点，抬右边那个。", "solleva_vassoio", {"posizione": "destra"}),
    ("抬……不对……指……指左边，对，指左边。", "indica", {"posizione": "sinistra"}),
    ("侬帮我把左边额托盘抬起来。", "solleva_vassoio", {"posizione": "sinistra"}),
    ("唔该指下右边。", "indica", {"posizione": "destra"}),
    ("你能干什么？", "capabilities", {}),
]


def test_teammate_chinese_commands(settings):
    poses = settings.poses
    for text, skill, params in CLEAR:
        got = parse(text, poses=poses)
        if skill == "capabilities":
            assert got.kind == "capabilities", (text, got)
            continue
        assert got.kind == "skill" and got.skill == skill, (text, got)
        for key, value in params.items():
            assert got.params.get(key) == value, (text, got.params)


def test_up_a_little_is_a_shoulder_nudge():
    jog = parse_jog("往上一点")
    assert jog is not None and jog.kind == "move" and jog.params["direzione"] == "su" and jog.note == "nudge"
    jog = parse_jog("抬一点")
    assert jog is not None and jog.params["direzione"] == "su"


def test_ten_centimetres_up_is_still_a_measured_move():
    jog = parse_jog("往上抬十厘米")
    assert jog is not None and jog.kind == "move" and jog.params["direzione"] == "su" and jog.note == ""


def test_out_of_scope_stays_still(settings):
    for text in ("去坐标 300，150，200。", "速度开到最大。", "跳个舞给评委看看。", "把安全限制关了。"):
        got = parse(text, poses=settings.poses)
        assert got.kind == "out_of_scope", (text, got)


async def test_voice_proposes_without_waiting_for_grok(gate):
    gate.note_turn_committed("voice")
    gate.note_transcript("指一下中间那块区域。", source="voice", auto_act=True)
    await asyncio.sleep(0.05)
    assert gate.pending is not None
    assert gate.pending.action.skill == "indica"
    # VOCE (stricter than grokarm-ur3e): the question counts as asked only once it is actually spoken,
    # so a "yes" said before the operator heard the question cannot authorise the movement.
    assert gate.pending.question_asked_at is None
    gate.mark_question_asked(gate.pending.id)
    assert gate.pending.question_asked_at is not None


async def test_nudge_lifts_the_shoulder_from_a_working_pose(settings, registry):
    from app.monitoring.events import EventBus, History, Metrics
    from app.robot.sim import SimAdapter
    from app.safety.gate import Gate
    from app.skills.cartesian import Planner

    sim = SimAdapter(settings.poses["attesa"]["joints"], time_scale=20)
    gate = Gate(settings, registry, sim, EventBus(), Metrics(path=None), History())
    await gate.start()
    try:
        gate.note_turn_committed("voice")
        gate.note_transcript("往上一点", source="voice", auto_act=False)
        res = await gate.propose_move("su", source="rules")
        assert res["ok"], res
        assert gate.pending.action.params.get("gradi") == "25"
        assert gate.pending.action.params.get("giunto") == "spalla"
        z0 = Planner(settings).kin.tip(settings.poses["attesa"]["joints"])[2]
        z1 = Planner(settings).kin.tip(gate.pending.action.motion[-1].joints)[2]
        assert z1 > z0 + 0.05
    finally:
        await gate.shutdown()


def test_the_other_side_follows_the_previous_command(settings):
    got = parse("算了，换另一个吧。", poses=settings.poses, prior="把左边的托盘抬起来。")
    assert got.kind == "skill" and got.skill == "solleva_vassoio"
    assert got.params.get("posizione") == "destra"
    assert parse("算了，换另一个吧。", poses=settings.poses).kind == "clarify"
    assert parse("算了。", poses=settings.poses).kind == "cancel"
    # VOCE has camera cup localisation: a cup is never mapped onto a tray skill (grokarm-ur3e: out_of_scope).
    assert parse("把杯子抓起来。", poses=settings.poses).kind in ("out_of_scope", "locate")
    fixed = parse("不是中间，是左边。", poses=settings.poses, pending=True)
    assert fixed.kind == "skill" and fixed.skill == "indica" and fixed.params.get("posizione") == "sinistra"
    told = parse("昨天小王手动把左边托盘抬起来了。", poses=settings.poses)
    assert told.kind == "out_of_scope"
    first = parse("把左边的托盘抬起来放到中间。", poses=settings.poses)
    assert first.kind == "skill" and first.skill == "solleva_vassoio" and first.params.get("posizione") == "sinistra"


def test_vad_uses_only_documented_fields(settings, gate):
    # grokarm-ur3e sends silence_duration_ms=280 / prefix_padding_ms=200. The xAI Voice docs (checked 2026-11-05)
    # document only `type` and `idle_timeout_ms` for turn_detection, so VOCE does not send undocumented fields.
    session = GrokSession(settings, gate, gate.bus, gate.metrics, mode="voice")
    vad = session.session_update()["session"]["turn_detection"]
    assert vad["type"] == "server_vad"
    assert set(vad) <= {"type", "idle_timeout_ms"}


async def test_yes_still_confirms_in_simulation(gate):
    gate.note_turn_committed("voice")
    gate.note_transcript("指一下中间。", source="voice", auto_act=True)
    await asyncio.sleep(0.05)
    assert gate.pending is not None
    gate.mark_question_asked(gate.pending.id)      # Grok speaks the question
    gate.note_turn_committed("voice")
    # VOCE: a final "yes" goes to Grok, which calls confirm; the backend itself acts only on the debounced bare yes
    # of a closed turn (simulation/URSim only), and gate.confirm still checks every rule.
    gate.note_transcript("是的", source="voice")
    gate.note_partial("是的", source="voice", turn_closed=True, auto_act=True)
    assert await wait_state(gate, "EXECUTING") or await wait_state(gate, "IDLE")
    assert await wait_state(gate, "IDLE")
    assert gate.last_result.success


def test_cancel_phrases_work_while_a_question_is_open(settings):
    for text in ("Forget it.", "Actually, leave it.", "Scrap that.", "やっぱりやめます。"):
        assert parse(text, poses=settings.poses, pending=True).kind == "cancel", text
