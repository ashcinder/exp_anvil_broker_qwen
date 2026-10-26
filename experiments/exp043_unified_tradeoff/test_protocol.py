from pathlib import Path
import sys,copy,pytest,json,math
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE));sys.path.insert(0,str(HERE.parents[1]))
from protocol import METHODS,validate_protocol,PROTOCOL
from runner import tasks_for,source_fingerprint,task_cfg
from brokerlab.config import load_config,apply_overrides
from experiments.exp025_regime_shift_sensitivity import pipeline
from plot import economics,load_rows

def cfg():return load_config(HERE/'config.yaml')

def test_single_baseline_full_and_unique_tasks():
 c=cfg();validate_protocol(c);tasks=tasks_for(c,'exp043')
 assert len(tasks)==42 and len({t['id'] for t in tasks})==42
 assert sum(t['value']=='plain' for t in tasks)==sum(t['value']=='topup' for t in tasks)==3
 for n,seed in [(1,7),(2,17),(3,27)]:
  g=[t for t in tasks if t['round']==n]
  assert len(g)==14 and {t['route_seed'] for t in g}=={seed}
  assert len({pipeline._arm_spec(t['token'])[2] for t in g})==1

def arm(name):return pipeline._arm_spec(METHODS[name][1])

def test_estimator_only_changes_in_matched_pair():
 hard,ewma=arm('topup_hard'),arm('topup')
 assert hard[1]==ewma[1]=='topup'
 assert hard[2:6]==ewma[2:6] and hard[7:]==ewma[7:]
 assert hard[6]==0 and ewma[6]==20
 hard,ewma=arm('prop_hard'),arm('prop_ewma')
 assert hard[1]==ewma[1]=='proportional'
 assert hard[2:6]==ewma[2:6] and hard[7:]==ewma[7:]

def test_policy_only_changes_in_matched_pair():
 p,t=arm('prop_ewma'),arm('topup')
 assert p[2:]==t[2:] and p[1]=='proportional' and t[1]=='topup'
 p,t=arm('prop_hard'),arm('topup_hard')
 assert p[2:]==t[2:]

def test_deadzone_is_only_changed_numeric_parameter():
 a,b=arm('topup'),arm('topup_e0')
 assert a[1:4]==b[1:4] and a[5:]==b[5:]
 assert a[4]==.95 and b[4]==0

@pytest.mark.parametrize('change',['exp.balances_eth=150','exp.arms=plain@45','exp.tdr_q_min=0.1','exp.tdr_ewma_min_blocks=5','exp.model_income_per_service=0.2'])
def test_refuse_mismatched_protocol(change):
 with pytest.raises(ValueError):validate_protocol(apply_overrides(cfg(),[change]))

def test_own_source_files_fingerprinted():
 hashes=source_fingerprint()
 for name in ['run.py','runner.py','protocol.py','plot.py','label_layout.py']:
  assert str((HERE/name).relative_to(HERE.parents[1])) in hashes

def test_profit_zero_and_count_plane_agree():
 R0,R,A,N=30000,20,2998,40000
 assert economics(N-R,N-R0,A,.1,1)==pytest.approx(0)
 assert economics(N-R,N-R0,A-1,.1,1)>0
 assert economics(N-R,N-R0,A+1,.1,1)<0

def test_historical_runs_rejected_before_combining(tmp_path):
 (tmp_path/'manifest.json').write_text(json.dumps({'experiment':'exp033'}))
 (tmp_path/'report.json').write_text('{}')
 with pytest.raises(ValueError,match='historical'):load_rows(tmp_path)
