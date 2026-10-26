"""Synthetic public CLI rows: identity text is not credential health."""
import importlib.util
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location('oauth_parser', Path(__file__).parents[1] / 'contents/code/hermes_openai_keys.py')
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


@pytest.mark.parametrize('identity', [
    'device_code',
    'id=deadbeef priority=0 device_code',
])
@pytest.mark.parametrize('label', ['deadpool', 'deadline', 'cooldown backup', 'invalid', 'disabled', 'rate-limited', 'oauth team'])
def test_unavailable_words_in_identity_are_not_status(label, identity):
    text = f'openai-codex (1 credentials):\n  #1  {label} oauth {identity} ←\n'
    assert MODULE.count_openai_credentials(text) == (1, 1)


@pytest.mark.parametrize('status', [*MODULE.UNAVAILABLE_MARKERS, 'auth failed (401)'])
@pytest.mark.parametrize('identity', ['device_code', 'id=synthetic priority=0 device_code'])
def test_actual_status_is_still_unavailable(status, identity):
    text = f'openai-codex (1 credentials):\n  #1  healthy oauth {identity} {status}\n'
    assert MODULE.count_openai_credentials(text) == (0, 1)
