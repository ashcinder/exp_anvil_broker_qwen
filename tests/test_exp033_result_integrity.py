import csv
import json
import pytest
from experiments.exp033_sota_comparison.plot_pareto import load, aggregate


def write(path, obj):
    path.write_text(json.dumps(obj), encoding='utf-8')


def run(root, values):
    root.mkdir()
    tasks, rows = [], []
    for arm, round_, seed, relay, moves in values:
        ident = f'{arm}_{round_}'
        tag = f'{arm}@45.0'
        tasks.append(dict(id=ident, status='complete', value=arm, round=round_,
                          route_seed=seed, token=f'{arm}@45', tag=tag, summary=f'{ident}.json'))
        stats = dict(arm=tag, n=100, served=100-relay, relayed=relay,
                     gates={'G1':True, 'G3':True}, burn_reconcile={'ok':True},
                     tdr={'transfers_done':moves})
        write(root/f'{ident}.json', dict(task_id=ident, workload_sha256='workload',
              params={'exp':{'route_seed':seed}}, status={'complete':True}, arms=[stats]))
        rows.append(dict(task_id=ident, sweep='figures', value=arm, round=round_,
                         route_seed=seed, n=100, served=100-relay, relayed=relay,
                         transfers=moves, moved_eth=1))
    write(root/'manifest.json', dict(algorithm_revision='exp033-faithful-v2',
         source_files={'a.py':'code'}, workload_sha256='workload', training_sha256='training',
         formal=False, tasks=tasks))
    write(root/'report.json', dict(formal=False, status={'complete':True}))
    write(root/'params.json', dict(exp={'arms':','.join(t['token'] for t in tasks),
          'q_min':.3}, chain={'base_port':8600}))
    with (root/'raw_results.csv').open('w',newline='',encoding='utf-8') as f:
        writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    return root


def test_economic_threshold_is_median_of_paired_ratios(tmp_path):
    root = run(tmp_path/'run', [('plain',1,7,10,0),('topup',1,7,5,5),
                              ('plain',2,17,100,0),('topup',2,17,50,5)])
    assert aggregate(load([root]))['topup']['rho'] == pytest.approx(.55)
    assert not aggregate(load([root]))['plain']['formal']


@pytest.mark.parametrize('field,value', [('source_files', {'a.py':'changed'}),
    ('workload_sha256','other'),('training_sha256','other'),('algorithm_revision','old')])
def test_incompatible_runs_are_rejected(tmp_path,field,value):
    a=run(tmp_path/'a',[('plain',1,7,10,0)])
    b=run(tmp_path/'b',[('topup',1,7,5,5)])
    doc=json.loads((b/'manifest.json').read_text());doc[field]=value
    write(b/'manifest.json',doc)
    with pytest.raises(ValueError): load([a,b])


def test_round_coverage_and_seed_mismatch_rejected(tmp_path):
    a=run(tmp_path/'a',[('plain',1,7,10,0),('plain',2,17,20,0),('topup',1,7,5,5)])
    with pytest.raises(ValueError,match='coverage'): load([a])
    b=run(tmp_path/'b',[('plain',1,7,10,0),('topup',1,17,5,5)])
    with pytest.raises(ValueError,match='seed'): load([b])


def test_failed_accounting_gate_is_rejected(tmp_path):
    a=run(tmp_path/'a',[('plain',1,7,10,0),('topup',1,7,5,5)])
    doc=json.loads((a/'topup_1.json').read_text());doc['arms'][0]['gates']['G3']=False
    write(a/'topup_1.json',doc)
    with pytest.raises(ValueError,match='gates'): load([a])


def test_unknown_arm_is_not_silently_plain():
    from experiments.exp025_regime_shift_sensitivity.pipeline import _arm_spec
    with pytest.raises(ValueError,match='Unknown'): _arm_spec('typo')
