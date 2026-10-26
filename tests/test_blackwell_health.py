"""Health monitoring: real parsers/state machine; all XML inputs are test fixtures."""
import importlib.util
from pathlib import Path
import pytest

PATH = Path(__file__).parents[1] / 'contents/code/blackwell_health.py'

def module():
    assert PATH.exists(), 'Blackwell health collector is not implemented'
    spec = importlib.util.spec_from_file_location('blackwell_health', PATH)
    assert spec is not None and spec.loader is not None
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m

XML = '''<nvidia_smi_log><gpu id="00000000:02:00.0"><uuid>GPU-test</uuid>
<product_name>NVIDIA RTX PRO 4000 Blackwell</product_name>
<ecc_mode><current_ecc>Enabled</current_ecc></ecc_mode>
<ecc_errors><aggregate><sram_correctable>0</sram_correctable><dram_correctable>0</dram_correctable><sram_uncorrectable_parity>0</sram_uncorrectable_parity><sram_uncorrectable_secded>0</sram_uncorrectable_secded><dram_uncorrectable>0</dram_uncorrectable></aggregate></ecc_errors>
<clocks_event_reasons><clocks_event_reason_sw_power_cap>Active</clocks_event_reason_sw_power_cap><clocks_event_reason_hw_thermal_slowdown>Not Active</clocks_event_reason_hw_thermal_slowdown><clocks_event_reason_sw_thermal_slowdown>Not Active</clocks_event_reason_sw_thermal_slowdown><clocks_event_reason_reliability>Active</clocks_event_reason_reliability></clocks_event_reasons>
<clocks><sm_clock>2500 MHz</sm_clock><mem_clock>14001 MHz</mem_clock></clocks>
<gpu_power_readings><average_power_draw>100 W</average_power_draw><current_power_limit>115 W</current_power_limit></gpu_power_readings><temperature><gpu_temp>60 C</gpu_temp></temperature><utilization><gpu_util>80 %</gpu_util></utilization></gpu></nvidia_smi_log>'''

def reading():
    m=module(); r=m.parse_gpu(XML,'GPU-test'); r['aer']={'gpu/correctable':0,'root/fatal':0}; return r

def step(state,r,t):
    return module().advance(state,r,t,'boot','session')

def test_parser_retains_real_reasons_and_clock_fields():
    r=reading()
    assert r['power_w']==100 and r['limit_w']==115
    assert r['thermal'] is False
    assert r['reasons']==['sw_power_cap','reliability']
    assert r['sm_mhz']==2500 and r['memory_mhz']==14001

def test_missing_and_nonfinite_values_are_unknown_not_zero():
    m=module(); r=m.parse_gpu('<nvidia_smi_log><gpu><uuid>GPU-test</uuid></gpu></nvidia_smi_log>','GPU-test')
    assert r['thermal'] is None
    assert r['power_w'] is None
    assert r['reasons_complete'] is False
    assert m.numeric('NaN') is None and m.numeric('inf') is None
    with pytest.raises(ValueError): m.parse_gpu(XML,'GPU-wrong')

def test_baseline_is_not_new_error_and_recovery_detects_increment():
    r=reading(); r['aer']['gpu/correctable']=10
    s=step({},r,100)
    assert s['new_faults']==0 and s['events']==[]
    s=step(s,None,110)
    assert s['available'] is False and s['events'][-1]['kind']=='measurement'
    r['aer']['gpu/correctable']=12
    s=step(s,r,120)
    assert s['new_faults']==2
    assert sum(e['kind']=='aer' for e in s['events'])==1
    s=step(s,r,130)
    assert sum(e['kind']=='aer' for e in s['events'])==1

def test_energy_integrates_only_covered_intervals_and_window_average():
    r=reading(); s=step({},r,100)
    s=step(s,r,110)
    assert s['energy_wh']==pytest.approx(1000/3600)
    assert s['average_w']==100 and s['covered_s']==10
    s=step(s,None,120);s=step(s,r,130)
    assert s['covered_s']==10
    s=step(s,r,140)
    assert s['covered_s']==20
    s=step(s,r,200) # suspend/poll stall must not fabricate energy
    assert s['covered_s']==20 and s['average_w'] is None

def test_session_boot_or_uuid_change_resets_energy_and_baseline():
    r=reading();s=step(step({},r,100),r,110)
    for field,value in [('session','new'),('boot','new'),('uuid','new')]:
        old=dict(s);old[field]=value
        reset=step(old,r,120)
        assert reset['energy_wh']==0 and reset['new_faults']==0

def test_thermal_event_requires_sustained_sampling_and_deduplicates():
    r=reading();r['thermal']=True
    s=step({},r,100);s=step(s,r,110)
    assert not s['events']
    s=step(s,r,120);s=step(s,r,130)
    assert sum(e['kind']=='thermal' for e in s['events'])==1
    r['thermal']=False;s=step(s,r,140)
    assert s['thermal_since'] is None

def test_counter_reset_is_not_new_error():
    r=reading();r['aer']['gpu/correctable']=20;s=step({},r,100)
    r['aer']['gpu/correctable']=0;s=step(s,r,110)
    assert s['new_faults']==0 and s['events'][-1]['kind']=='reset'

def test_aer_parses_totals_without_double_counting(tmp_path):
    m=module();d=tmp_path/'0000:00:06.0'/'0000:02:00.0';d.mkdir(parents=True)
    (d/'aer_dev_correctable').write_text('RxErr 3\nBadTLP 2\nTOTAL_ERR_COR 5\n')
    (d.parent/'aer_rootport_total_err_cor').write_text('7\n')
    r=m.read_aer(d)
    assert r['gpu/correctable']==5 and r['root/correctable']==7
    assert r['gpu/fatal'] is None

def test_xid_targets_only_matching_pci_and_deduplicates_cursor():
    m=module()
    rows=[{'__CURSOR':'a','__REALTIME_TIMESTAMP':'120000000','MESSAGE':'NVRM: Xid (PCI:0000:02:00): 31, test'}, {'__CURSOR':'b','MESSAGE':'NVRM: Xid (PCI:0000:85:00): 43, other GPU'}]
    events=m.xid_events(rows,'0000:02:00.0')
    assert len(events)==1 and events[0]['kind']=='xid' and '31' in events[0]['text']

def test_history_and_events_are_bounded():
    s={};r=reading()
    for t in range(100,4000,10):
        r['aer']['gpu/correctable']+=1;s=step(s,r,t)
    assert len(s['history'])<=14 and len(s['events'])<=20

def test_ui_integrates_health_component():
    root=PATH.parents[1]/'ui'
    assert 'BlackwellHealth {' in (root/'main.qml').read_text()
    qml=(root/'BlackwellHealth.qml').read_text()
    assert 'UNBEKANNT' in qml and 'blackwell_health.py' in qml
    assert '15000' in qml and '10000' in qml
