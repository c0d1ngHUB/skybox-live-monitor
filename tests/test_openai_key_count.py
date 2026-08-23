#!/usr/bin/env python3
"""Tests for the privacy-safe OpenAI credential count helper."""

import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "contents/code/hermes_openai_keys.py"
SPEC = importlib.util.spec_from_file_location("hermes_openai_keys", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_counts_available_openai_oauth_credentials():
    output = """copilot (1 credentials):
  #1  GITHUB_TOKEN api_key env:GITHUB_TOKEN

openai-codex (3 credentials):
  #1  device_code oauth device_code
  #2  openai-2 oauth device_code rate-limited usage_limit_reached (429)
  #3  openai-codex-oauth-3 oauth device_code

openrouter (1 credentials):
  #1  OPENROUTER_API_KEY api_key env:OPENROUTER_API_KEY
"""
    assert MODULE.count_openai_credentials(output) == (2, 3)


def test_excludes_dead_and_exhausted_credentials():
    output = """openai-codex (3 credentials):
  #1  first oauth device_code dead (401)
  #2  second oauth device_code exhausted (429)
  #3  third oauth device_code
"""
    assert MODULE.count_openai_credentials(output) == (1, 3)


def test_missing_provider_reports_zero_credentials():
    assert MODULE.count_openai_credentials("nous (1 credentials):\n  #1 device_code oauth\n") == (0, 0)
