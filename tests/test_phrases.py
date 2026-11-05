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
