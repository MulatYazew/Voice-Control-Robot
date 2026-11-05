import pytest
import yaml

from app.config import ConfigError, load_settings
from app.skills.registry import PRECONDIZIONE, RICHIESTA_AMBIGUA, SKILL_SCONOSCIUTA, Registry, SkillError
from app.skills.world import WorldModel


def world(settings):
    return WorldModel.initial(settings.zones, settings.robot["workspace"]["trays_assumed_at"])


def test_registration_and_config_loading(registry):
    assert set(registry.skills) == {"vai_a_posa", "indica", "solleva_vassoio", "appoggia_vassoio", "spingi",
                                    "sposta_vassoio", "porta_in_ispezione", "smista"}
    assert registry.skills["spingi"].parametri == {"posizione": "zona", "direzione": "direzione"}
    assert registry.domain("zona") == ["sinistra", "centro", "destra"]
    assert "home" in registry.selectable_poses()


@pytest.mark.parametrize("skill,params,expect", [
    ("indica", {"posizione": "centrale"}, {"posizione": "centro"}),
    ("indica", {"posizione": "a destra"}, {"posizione": "destra"}),
    ("solleva_vassoio", {"posizione": "sinistra"}, {"posizione": "sinistra"}),
    ("spingi", {"posizione": "destra", "direzione": "sinistra"}, {"posizione": "destra", "direzione": "sinistra"}),
    ("vai_a_posa", {"posa": "casa"}, {"posa": "home"}),
    ("vai_a_posa", {"posa": "attesa"}, {"posa": "attesa"}),
])
def test_valid_parameters(registry, settings, skill, params, expect):
    a = registry.validate(skill, params, world(settings))
    assert a.params == expect and a.motion and all(m.joints for m in a.motion if m.move != "wait")


def test_missing_parameter_is_ambiguous(registry, settings):
    with pytest.raises(SkillError) as e:
        registry.validate("solleva_vassoio", {}, world(settings))
    assert e.value.code == RICHIESTA_AMBIGUA and e.value.param == "posizione"
    assert e.value.options == ["sinistra", "destra"]  # only zones where a tray is assumed


def test_invalid_parameter_is_ambiguous(registry, settings):
    with pytest.raises(SkillError) as e:
        registry.validate("indica", {"posizione": "sopra il tavolo"}, world(settings))
    assert e.value.code == RICHIESTA_AMBIGUA and e.value.options == ["sinistra", "centro", "destra"]
    with pytest.raises(SkillError) as e:
        registry.validate("spingi", {"posizione": "centro", "direzione": "avanti"}, world(settings))
    assert e.value.code in (RICHIESTA_AMBIGUA, PRECONDIZIONE)


def test_unknown_skill(registry, settings):
    with pytest.raises(SkillError) as e:
        registry.validate("disegna_cerchio", {}, world(settings))
    assert e.value.code == SKILL_SCONOSCIUTA


def test_no_unrestricted_pose_from_conversation(registry, settings):
    # a joint vector or unknown pose name must never be accepted
    for bad in ([0, 0, 0, 0, 0, 0], "sinistra_presa", "x: 0.3 y: 0.2"):
        with pytest.raises(SkillError):
            registry.validate("vai_a_posa", {"posa": bad}, world(settings))


def test_preconditions(registry, settings):
    w = world(settings)
    with pytest.raises(SkillError) as e:
        registry.validate("solleva_vassoio", {"posizione": "centro"}, w)  # no tray assumed there
    assert e.value.code == PRECONDIZIONE
    with pytest.raises(SkillError) as e:
        registry.validate("appoggia_vassoio", {"posizione": "centro"}, w)  # nothing on fork
    assert e.value.code == PRECONDIZIONE
    w.apply_effects("solleva_vassoio", {"posizione": "sinistra"})
    assert w.holding_tray and "sinistra" not in w.trays_at
    with pytest.raises(SkillError):
        registry.validate("solleva_vassoio", {"posizione": "destra"}, w)  # fork busy
    with pytest.raises(SkillError):
        registry.validate("appoggia_vassoio", {"posizione": "destra"}, w)  # zone occupied
    assert registry.validate("appoggia_vassoio", {"posizione": "centro"}, w).params == {"posizione": "centro"}


def test_config_rejects_missing_pose(cfg_dir, tmp_path):
    p = cfg_dir / "poses.yaml"
    d = yaml.safe_load(p.read_text())
    del d["poses"]["centro_indica"]
    p.write_text(yaml.safe_dump(d))
    with pytest.raises(ConfigError, match="centro_indica"):
        load_settings("sim", config_dir=cfg_dir, data_dir=tmp_path, env_file=None)


def test_real_mode_requires_explicit_config(cfg_dir, tmp_path):
    with pytest.raises(ConfigError, match="explicit"):
        load_settings("real", config_dir=cfg_dir, data_dir=tmp_path, env_file=None)


def test_real_mode_refuses_unverified_poses(cfg_dir, tmp_path):
    p = cfg_dir / "robot.yaml"
    d = yaml.safe_load(p.read_text())
    d["connections"]["real"].update(enabled=True, host="10.0.0.3")
    p.write_text(yaml.safe_dump(d))
    s = load_settings("real", config_dir=cfg_dir, data_dir=tmp_path, env_file=None)
    with pytest.raises(SkillError, match="registrata"):
        Registry(s).validate("indica", {"posizione": "centro"}, WorldModel.initial(s.zones, []))


def test_bad_mode(cfg_dir, tmp_path):
    with pytest.raises(ConfigError):
        load_settings("fly", config_dir=cfg_dir, data_dir=tmp_path, env_file=None)
