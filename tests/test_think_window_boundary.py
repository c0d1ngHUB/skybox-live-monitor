"""Completion timestamps define the window, not initiating-message timestamps."""
import importlib.util
from pathlib import Path
import sqlite3

import pytest

SPEC = importlib.util.spec_from_file_location('think_boundary', Path(__file__).parents[1] / 'contents/code/hermes_max_think.py')
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
CUTOFF = 113600


@pytest.mark.parametrize('modern', [False, True])
@pytest.mark.parametrize('turns, expected', [
    ([('root', 'user', 113590), ('child', 'assistant', 113650)], 60),
    ([('root', 'user', 113590), ('child', 'assistant', CUTOFF)], 10),
    ([('root', 'user', 113590), ('child', 'assistant', CUTOFF - 1)], 0),
    ([('root', 'user', 1000), ('child', 'assistant', 199999)], 198999),
    ([('root', 'user', 113000), ('child', 'assistant', 113500), ('child', 'assistant', 113650)], 0),
    ([('root', 'user', 113000), ('child', 'user', 113590), ('child', 'assistant', 113650)], 60),
    ([('root', 'user', 113590), ('child', 'assistant', 200001)], 0),
])
def test_completion_window_preserves_pairing(tmp_path, modern, turns, expected):
    db = tmp_path / 'synthetic.db'
    with sqlite3.connect(db) as connection:
        connection.execute('CREATE TABLE sessions (id TEXT, source TEXT, parent_session_id TEXT)')
        connection.executemany('INSERT INTO sessions VALUES (?, ?, ?)', [('root', 'telegram', None), ('child', 'telegram', 'root')])
        connection.execute('CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, content TEXT, timestamp REAL)')
        for index, (session, role, stamp) in enumerate(turns):
            connection.execute('INSERT INTO messages VALUES (?, ?, ?, ?, ?)', (index, session, role, 'synthetic', stamp))
        if modern:
            connection.execute('ALTER TABLE messages ADD COLUMN tool_calls TEXT')
            connection.execute('ALTER TABLE messages ADD COLUMN finish_reason TEXT')
    assert MODULE.longest_completed_turn(db, 200000) == expected
