from types import SimpleNamespace

import pytest

from app.core import clock_discipline as clock
from app.services import snapback_preflight as preflight


def install_probe(monkeypatch, *, state=0, status=0, maxerror=3_000):
    class Probe:
        def __call__(self, pointer):
            observation = pointer._obj
            assert observation.modes == 0, "clock probe must never request clock mutation"
            observation.status = status
            observation.maxerror = maxerror
            return state
    monkeypatch.setattr(clock.platform, "system", lambda: "Linux")
    monkeypatch.setattr(clock.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(clock.ctypes, "CDLL", lambda *args, **kwargs: SimpleNamespace(adjtimex=Probe()))


@pytest.mark.parametrize("state,status,maxerror,expected", [
    (0, 0, 3_000, True), (1, 0, 3_000, True),
    (5, 0, 3_000, False), (-1, 0, 3_000, False),
    (0, 0x40, 3_000, False), (0, 0x1000, 3_000, False),
    (0, 0, 500_001, False), (0, 0, -1, False),
])
def test_kernel_evidence_not_process_presence(monkeypatch, state, status, maxerror, expected):
    install_probe(monkeypatch, state=state, status=status, maxerror=maxerror)
    ok, detail = clock.kernel_clock_synchronised()
    assert ok is expected
    assert detail


def test_unsupported_platform_stays_closed(monkeypatch):
    monkeypatch.setattr(clock.platform, "system", lambda: "Darwin")
    assert clock.kernel_clock_synchronised() == (False, "kernel_clock_unsupported_abi")


def test_unavailable_syscall_stays_closed(monkeypatch):
    def unavailable(*args, **kwargs):
        raise OSError("unavailable")
    monkeypatch.setattr(clock.ctypes, "CDLL", unavailable)
    assert clock.kernel_clock_synchronised()[0] is False


def test_systemd_negative_is_never_overruled(monkeypatch):
    monkeypatch.setattr(preflight.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="no"))
    monkeypatch.setattr(clock, "kernel_clock_synchronised", lambda: pytest.fail("must not override explicit no"))
    assert preflight._ntp_synchronised() == (False, "not_synchronised:no")


def test_no_systemd_bus_uses_real_kernel_evidence(monkeypatch):
    monkeypatch.setattr(preflight.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=1, stdout=""))
    monkeypatch.setattr(clock, "kernel_clock_synchronised", lambda: (True, "kernel_synchronised"))
    assert preflight._ntp_synchronised() == (True, "kernel_synchronised")


def test_unavailable_both_probes_stays_closed(monkeypatch):
    monkeypatch.setattr(preflight.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=1, stdout=""))
    monkeypatch.setattr(clock, "kernel_clock_synchronised", lambda: (False, "kernel_clock_unavailable"))
    assert preflight._ntp_synchronised() == (False, "kernel_clock_unavailable")
