"""Git-less release diagnostics must use content-verified artifact identity."""
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from app.core import runtime_identity as identity
from app.core import build_artifact_identity as artifact

@pytest.mark.parametrize('valid',[True,False])
def test_gitless_runtime_uses_verified_artifact(monkeypatch,valid):
    monkeypatch.setattr(identity.subprocess,'run',Mock(side_effect=FileNotFoundError('git absent')))
    monkeypatch.setattr(artifact,'artifact_identity',lambda:SimpleNamespace(present=True,valid=valid,build_sha='a'*40))
    assert identity.checkout_sha()==('a'*40 if valid else 'UNKNOWN')
    assert identity.source_dirty() is (not valid)


def test_gitless_runtime_without_metadata_is_unknown(monkeypatch):
    monkeypatch.setattr(identity.subprocess,'run',Mock(side_effect=FileNotFoundError('git absent')))
    monkeypatch.setattr(artifact,'artifact_identity',lambda:SimpleNamespace(present=False,valid=False,build_sha='UNKNOWN'))
    assert identity.checkout_sha()=='UNKNOWN'
    assert identity.source_dirty() is None
