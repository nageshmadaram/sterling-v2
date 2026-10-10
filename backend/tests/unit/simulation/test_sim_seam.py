"""The broker-mode seam: real money, virtual money and the legacy paper double are told apart exactly."""
from unittest.mock import MagicMock

from app.services.simulation import seam
from app.services.simulation import sim_client as sc


class Real:
    _is_paper = False


class LegacyPaper:
    _is_paper = True


class Unknown:
    pass


class Spoof:
    """Not a SimulatedKiteClient, and its flag is not an explicit True."""
    _is_paper = True
    _is_simulated = 1


def test_the_three_kinds_of_client_are_classified_exactly(ledger):
    sim = sc.SimulatedKiteClient("default", lambda: None, ledger=ledger)
    assert (seam.is_live_like(Real()), seam.is_instant_paper(Real()), seam.is_simulated(Real())) == (True, False, False)
    assert (seam.is_live_like(sim), seam.is_instant_paper(sim), seam.is_simulated(sim)) == (True, False, True)
    assert (seam.is_live_like(LegacyPaper()), seam.is_instant_paper(LegacyPaper())) == (False, True)
    assert (seam.is_live_like(Unknown()), seam.is_instant_paper(Unknown())) == (False, False)
    assert seam.is_live_like(None) is False and seam.is_instant_paper(None) is False


def test_a_simulated_client_is_never_real_money(ledger):
    sim = sc.SimulatedKiteClient("default", lambda: None, ledger=ledger)
    assert sim._is_paper is True          # every real-money gate (``is False``) stays closed to it
    assert getattr(sim, "_is_paper", None) is not False


def test_a_mock_or_a_loose_truthy_flag_never_passes_for_simulated():
    assert seam.is_simulated(MagicMock()) is False and seam.is_live_like(MagicMock()) is False
    paper_mock = MagicMock()
    paper_mock._is_paper = True
    assert seam.is_live_like(paper_mock) is False and seam.is_instant_paper(paper_mock) is True
    assert seam.is_simulated(Spoof()) is False and seam.is_live_like(Spoof()) is False


def test_the_sim_client_helper_is_the_seam():
    assert sc.is_live_like(Real()) and not sc.is_live_like(LegacyPaper())
