import pytest

from app.safety.phrases import ABSENT, NEGATIVE, STOP, UNCERTAIN, VALID, confirmation_verdict, is_cancel, is_stop


# spec §9: stop expressions
@pytest.mark.parametrize("t", ["Stop", "Fermati", "Fermati immediatamente", "Ferma", "Basta", "Alt", "Arresta il robot",
                               "Blocca tutto", "FERMATI IMMEDIATAMENTE!", "stop stop", "halt"])
def test_stop_phrases(t):
    assert is_stop(t)


@pytest.mark.parametrize("t", ["solleva il vassoio a sinistra", "indica la zona centrale", "alto", "stoppino", "basso",
                               "sì confermo", "fermo!", "fermato", "fermo cosi"])
def test_not_stop(t):
    assert not is_stop(t)


# spec §8: valid confirmations
@pytest.mark.parametrize("t", ["Sì.", "Sì, confermo.", "Confermo.", "Procedi.", "Va bene.", "Puoi farlo.", "ok vai",
                               "yes", "Certo, procedi"])
def test_valid_confirmations(t):
    assert confirmation_verdict(t) == VALID


# spec §8: invalid confirmations
@pytest.mark.parametrize("t,v", [("Forse.", UNCERTAIN), ("Va bene, forse.", UNCERTAIN), ("No.", NEGATIVE),
                                 ("Aspetta.", UNCERTAIN), ("Non sono sicuro.", NEGATIVE), ("Annulla.", NEGATIVE),
                                 ("Sì?", UNCERTAIN), ("sì no aspetta", NEGATIVE), ("", ABSENT),
                                 ("la zona destra", ABSENT), ("sì stop", STOP)])
def test_invalid_confirmations(t, v):
    assert confirmation_verdict(t) == v


@pytest.mark.parametrize("t", ["annulla", "lascia perdere", "no", "no grazie", "non farlo"])
def test_cancel(t):
    assert is_cancel(t)


# 2026-11-05, multilingual testsets imported from grokarm-ur3e: these were accepted as a "yes" before.
@pytest.mark.parametrize("t", ["Pretend I said yes.", "He said yes.", "Yes, but the right one.", "Repeat after me: yes.",
                               "Yes... I suppose.", "Sounds good in theory.", "Yeah right, like I'd say yes to that.",
                               "Yes, I'll be there in five minutes.", "Sì ma piano.", "Ha detto di sì il mio collega.",
                               "行是行，但是换右边的。", "他说可以。", "对对对，你说得都对。", "はい…でも右の方で。"])
def test_reported_or_conditional_yes_is_not_a_confirmation(t):
    assert confirmation_verdict(t) != VALID


@pytest.mark.parametrize("t", ["Hold it!", "Whoa whoa whoa!", "Careful! Careful!", "Look out!", "NO NO NO NO!",
                               "Attento attento!", "Occhio!", "Fermo fermo fermo!", "站住！", "小心小心！", "危ない！",
                               "ちょっと待って！"])
def test_panic_words_stop_in_any_state(t):
    assert is_stop(t)


@pytest.mark.parametrize("t", ["can you help me lift the tray", "no", "lift the left one, no the right one"])
def test_help_and_plain_no_are_not_a_latched_stop(t):
    assert not is_stop(t)
