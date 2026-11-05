"""WP-1: STOP / confirmation must work in it, en, zh, ja and am."""

import pytest

from app.safety.phrases import ABSENT, NEGATIVE, STOP, UNCERTAIN, VALID, confirmation_verdict, is_cancel, is_stop
from app.skills.registry import norm


@pytest.mark.parametrize("t", [
    "请马上停下来",
    "停下！",
    "停下!",
    "停止",
    "别动",
    "住手",
    "暂停",
    "紧急",
    "ロボット、止まって！",
    "止まれ",
    "止めて",
    "ストップ",
    "やめて",
    "እባክህ አቁም",
    "አቁሚ።",
    "በቃ",
    "ተው",
    "aqum now",
    "please akum",
    "beka",
    "ＳＴＯＰ",
    "停一下，别动",
])
def test_stop_all_languages(t):
    assert is_stop(t)
    assert confirmation_verdict(t) == STOP


@pytest.mark.parametrize("t,v", [
    ("不好", NEGATIVE),
    ("不可以", NEGATIVE),
    ("不对", NEGATIVE),
    ("不行", NEGATIVE),
    ("不要", NEGATIVE),
    ("いいえ", NEGATIVE),
    ("いや", NEGATIVE),
    ("だめ", NEGATIVE),
    ("是的，确认", VALID),
    ("好的", VALID),
    ("确认", VALID),
    ("可以", VALID),
    ("行", VALID),
    ("执行", VALID),
    ("继续", VALID),
    ("はい", VALID),
    ("ええ", VALID),
    ("うん", VALID),
    ("お願いします", VALID),
    ("どうぞ", VALID),
    ("いいよ", VALID),
    ("オーケー", VALID),
    ("አዎ", VALID),
    ("አዎን", VALID),
    ("እሺ", VALID),
    ("awo", VALID),
    ("ishi", VALID),
    ("是的？", UNCERTAIN),
    ("是的?", UNCERTAIN),
    ("好吗", UNCERTAIN),
    ("可以吗", UNCERTAIN),
    ("也许", UNCERTAIN),
    ("等一下", UNCERTAIN),
    ("大概", UNCERTAIN),
    ("たぶん", UNCERTAIN),
    ("いいですか", UNCERTAIN),
    ("かな", UNCERTAIN),
    ("ምናልባት", UNCERTAIN),
    ("minalbat", UNCERTAIN),
    ("koy", UNCERTAIN),
    ("大丈夫", ABSENT),
    ("桌子", ABSENT),
    ("አይ", NEGATIVE),
    ("aydelem", NEGATIVE),
    ("ሰርዝ", NEGATIVE),
])
def test_confirmation_order(t, v):
    assert confirmation_verdict(t) == v


@pytest.mark.parametrize("t", ["取消", "算了", "不要", "キャンセル", "ሰርዝ"])
def test_cancel_cjk_and_amharic(t):
    assert is_cancel(t)


def test_stop_beats_negative_particle():
    """别 is negative, but 别动 is STOP and must win."""
    assert confirmation_verdict("别") == NEGATIVE
    assert confirmation_verdict("别动") == STOP
    assert not is_stop("别")


def test_fullwidth_punctuation_and_dakuten():
    assert is_stop("停下！")
    assert confirmation_verdict("是的？") == UNCERTAIN
    assert confirmation_verdict("どうぞ") == VALID
    assert norm("どうぞ") == "どうぞ"


def test_registry_norm_resolves_zones(registry):
    assert norm("左边") == "左边"
    assert registry.normalize("zona", norm("左边")) == "sinistra"
    assert registry.normalize("zona", "ひだり") == "sinistra"
    assert registry.normalize("zona", "ግራ") == "sinistra"
    assert registry.normalize("zona", "中间") == "centro"
    assert registry.normalize("zona", "真ん中") == "centro"
    assert registry.normalize("zona", "መሃል") == "centro"
    assert registry.normalize("zona", "右边") == "destra"
    assert registry.normalize("zona", "みぎ") == "destra"
    assert registry.normalize("zona", "ቀኝ") == "destra"
    assert registry.normalize("direzione", "左边") == "sinistra"


def test_rules_see_han_zone():
    from app.conversation.rules import parse
    intent = parse("左边的托盘")
    assert intent.params.get("posizione") == "sinistra" or intent.kind in ("clarify", "out_of_scope", "skill")
    # the zone itself must be recovered even without a skill verb
    found = parse("请看左边")
    assert found.params.get("posizione") == "sinistra"
