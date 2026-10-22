"""Collector-level regressions from independent review; synthetic journal/XML only."""
from test_blackwell_health import module, XML

def test_xid_survives_gpu_measurement_outage(monkeypatch):
    m=module();m.UUID='GPU-test'
    boot=m.Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    state=m.advance({},m.parse_gpu(XML,'GPU-test'),90,boot,'s')
    state['journal_cursor']='baseline'
    monkeypatch.setattr(m.time,'time',lambda:100)
    monkeypatch.setattr(m,'capture',lambda args:None)
    monkeypatch.setattr(m,'journal',lambda cursor:[{'__CURSOR':'next','__REALTIME_TIMESTAMP':'100000000','MESSAGE':'NVRM: Xid (PCI:0000:02:00): 31, test'}])
    result=m.collect(state,'s')
    assert result['available'] is False
    assert result['journal_cursor']=='next'
    assert any(e['kind']=='xid' for e in result['events'])

def test_no_identity_does_not_consume_unprocessed_journal(monkeypatch):
    m=module()
    boot=m.Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    state=m.advance({},None,90,boot,'s');state['journal_cursor']='baseline'
    monkeypatch.setattr(m.time,'time',lambda:100)
    monkeypatch.setattr(m,'capture',lambda args:None)
    monkeypatch.setattr(m,'journal',lambda cursor:[{'__CURSOR':'next','MESSAGE':'NVRM: Xid (PCI:0000:02:00): 31, test'}])
    result=m.collect(state,'s')
    assert result['journal_cursor']=='baseline'
    assert result['journal_available'] is False

def test_session_paths_are_distinct_and_do_not_allow_traversal(tmp_path):
    m=module()
    a=m.state_path(tmp_path,'one','boot');b=m.state_path(tmp_path,'two','boot')
    assert a!=b
    assert m.state_path(tmp_path,'../../outside','boot').parent==tmp_path
    assert a!=m.state_path(tmp_path,'one','newboot')

def test_first_journal_entry_is_only_a_baseline(monkeypatch):
    m=module();m.UUID='GPU-test'
    monkeypatch.setattr(m,'capture',lambda args:XML)
    monkeypatch.setattr(m,'journal',lambda cursor:[{'__CURSOR':'first','MESSAGE':'NVRM: Xid (PCI:0000:02:00): 31, historical'}])
    result=m.collect({},'s')
    assert not any(e['kind']=='xid' for e in result['events'])
    assert result['journal_cursor']=='first'


def test_empty_or_unsupported_clock_reasons_are_unknown():
    m=module()
    for value in ('', 'N/A', 'Unsupported'):
        xml=XML.replace('>Active<','>'+value+'<')
        r=m.parse_gpu(xml,'GPU-test')
        assert r['reasons_complete'] is False
        assert r['reasons']==[]


def test_journal_failure_preserves_cursor(monkeypatch):
    m=module();m.UUID='GPU-test'
    boot=m.Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    state=m.advance({},m.parse_gpu(XML,'GPU-test'),90,boot,'s')
    state['journal_cursor']='baseline'
    monkeypatch.setattr(m.time,'time',lambda:100)
    monkeypatch.setattr(m,'capture',lambda args:XML)
    monkeypatch.setattr(m,'journal',lambda cursor:None)
    result=m.collect(state,'s')
    assert result['journal_available'] is False
    assert result['journal_cursor']=='baseline'
