"""Exp017 tests run without Anvil or the real CSV."""
import copy
import io
import json
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from brokerlab.config import load_config, ETH
from brokerlab import controlled_direction as cd
from brokerlab import directional_experiment as de
from brokerlab.directional_workload import validate_prepared
from brokerlab.mapped_workload import load_prepared, write_json

ROOT = Path(__file__).resolve().parents[1]
HERE = ROOT / "experiments/exp017_control_direction"


def config(count=100):
    cfg = load_config(HERE / "config.yaml")
    return replace(cfg, scale=replace(cfg.scale, num_brokers=1),
                   exp={**cfg.exp, "ctx_per_broker":count})


def pool(count=100):
    # Include outgoing target, incoming target, background, and intra-shard.
    rows = []
    for i in range(count):
        src, dst = [(0,1),(1,0),(2,3),(4,20)][i%4]
        rows.append(dict(ctx_id=f"tx{i}", orig_from=hex(src), orig_to=hex(dst),
                         amount_wei=(i%10+1)*ETH//10, sender_idx=200+src, receiver_idx=200+dst,
                         source_line=i+2, source_block=str(100+i//5), source_timestamp=str(i)))
    return rows


def test_pairing_nested_bits_and_source_preservation():
    cfg = config()
    original = pool(200)
    unchanged = copy.deepcopy(original)
    scenes, design = cd.controlled_layouts(original, cfg, 100)
    assert original == unchanged
    assert len(scenes) == 5
    assert scenes == cd.controlled_layouts(original,cfg,100)[0]
    identities = [[(r['ctx_id'],r['amount_wei'],r['source_line']) for r in s[1]] for s in scenes]
    assert all(x == identities[0] for x in identities)
    for index in range(100):
        bits = [s[1][index]['control_bit'] for s in scenes]
        assert bits == sorted(bits)
    zero, full = scenes[0][1], scenes[-1][1]
    assert all(r['control_bit']==0 and r['src_shard']==r['base_src_shard'] and r['dst_shard']==r['base_dst_shard'] for r in zero)
    assert all(r['control_bit']==1 and r['dst_shard']==0 and r['src_shard']!=0 for r in full)
    assert any(r['source_reassigned'] for r in full)
    for r in full:
        if r['base_src_shard']==0:
            assert r['src_shard']==r['base_dst_shard']
    assert all(r['src_shard']!=r['dst_shard'] for s in scenes for r in s[1])


def test_control_independent_of_amount_and_route_seed():
    cfg = config()
    a = pool(200)
    b = [{**r,'amount_wei':ETH} for r in a]
    scenes, _ = cd.controlled_layouts(a,cfg,100)
    cfg.exp['route_seed'] = 999
    others, _ = cd.controlled_layouts(b,cfg,100)
    assert [[r['control_draw_hex'] for r in s[1]] for s in scenes] == [[r['control_draw_hex'] for r in s[1]] for s in others]
    assert cd.route_fields(a[0],.5,1,0)['control_draw_hex'] != cd.route_fields(a[0],.5,2,0)['control_draw_hex']


def test_probability_distribution_and_nonzero_target():
    cfg = config(10000)
    cfg.exp['target_shard'] = 3
    scenes,_ = cd.controlled_layouts(pool(14000),cfg,10000)
    for _,rows,_,info in scenes:
        p = info['control_probability']
        assert abs(sum(r['control_bit'] for r in rows)/len(rows)-p)<.025
    assert all(r['dst_shard']==3 and r['src_shard']!=3 for r in scenes[-1][1])


@pytest.mark.parametrize('probabilities,seed', [([],1),([-.1],1),([1.1],1),([float('nan')],1),
    ([float('inf')],1),([0,0],1),([True],1),(['0.5'],1),([.5],-1),([.5],True),([.5],1.5)])
def test_bad_settings(probabilities,seed):
    cfg=config()
    cfg.exp.update(control_probabilities=probabilities,control_seed=seed)
    with pytest.raises(ValueError): de.resolve(cfg,'controlled')


def test_insufficient_pool_rejected():
    with pytest.raises(ValueError,match='Insufficient'):
        cd.controlled_layouts(pool(10),config(),100)


def frozen(tmp_path):
    cfg,_=de.resolve(config(),'controlled')
    name,rows,mapping,info=cd.controlled_layouts(pool(200),cfg,100)[0][-1]
    item=cd.export_controlled(tmp_path/name,name,rows,mapping,info,cfg,{})
    payload=json.loads(Path(item['path']).read_text(encoding='utf-8'))
    return cfg,item,payload


def test_schema3_roundtrip_audit_and_static_schema_not_relaxed(tmp_path):
    cfg,item,payload=frozen(tmp_path)
    rows,total=load_prepared(replace(cfg,exp={**cfg.exp,'prepared_workload_path':item['path'],
                                            'prepared_workload_sha256':item['sha256']}))
    assert len(rows)==100 and total==sum(r['amount_wei'] for r in rows)
    a=item['audit']
    assert a['target_net_fraction']==1
    assert a['control']['realized_target_destination_share']==1
    assert a['control']['controlled_count']==100
    assert a['control']['source_reassigned_count']>0
    with pytest.raises(ValueError,match='schema'):
        validate_prepared(payload,cfg)


@pytest.mark.parametrize('field', ['control_bit','src_shard','dst_shard','base_src_shard','base_dst_shard',
    'source_reassigned','destination_changed','control_draw_hex','amount_wei','sender_idx',
    'arrival_pos','source_line','ctx_id'])
def test_schema3_rejects_tampering(tmp_path,field):
    cfg,item,p=frozen(tmp_path)
    if field=='source_line': p['rows'][1][field]=p['rows'][0][field]
    elif field=='ctx_id': p['rows'][1][field]=p['rows'][0][field]
    elif field=='control_draw_hex': p['rows'][0][field]='tampered'
    elif field=='amount_wei': p['rows'][0][field]=0
    else: p['rows'][0][field]+=1
    with pytest.raises(ValueError): cd.validate_controlled(p,cfg)


def test_policy_and_hash_mismatch(tmp_path):
    cfg,item,p=frozen(tmp_path)
    p['scenario']['control_seed']+=1
    with pytest.raises(ValueError,match='policy/config'):
        cd.validate_controlled(p,cfg)
    with pytest.raises(ValueError,match='SHA256'):
        load_prepared(replace(cfg,exp={**cfg.exp,'prepared_workload_path':item['path'],'prepared_workload_sha256':'wrong'}))
    before=de.fingerprint(cfg,'controlled')
    cfg.exp['control_seed']+=1
    assert before!=de.fingerprint(cfg,'controlled')


def test_dry_run_has_no_io_or_chains(monkeypatch):
    def fail(*a,**k): pytest.fail('Dry run must not prepare data/start chains')
    monkeypatch.setattr(de,'prepare',fail)
    monkeypatch.setattr(de.subprocess,'Popen',fail)
    assert de.main(HERE,'controlled',['--dry-run'])==0


def test_prepare_and_runner_with_fake_children(tmp_path,monkeypatch):
    cfg,_=de.resolve(config(),'controlled')
    (tmp_path/'config.yaml').write_text(yaml.safe_dump(de.to_params_dict(cfg)),encoding='utf-8')
    monkeypatch.setattr(de,'snapshot_source',lambda *a:(Path('unused.csv'),{}))
    monkeypatch.setattr(de,'candidate_pool',lambda *a:(pool(200),{}))
    monkeypatch.setattr(de,'ports_free',lambda *a:None)
    calls=[]
    class FakeProcess:
        def __init__(self,command,**kwargs):
            calls.append(command)
            self.stdout=io.StringIO('No Anvil: mocked child\n')
            child=load_config(command[command.index('--config')+1])
            rows,total=load_prepared(child)
            assert len(rows)==100
            folder=Path(command[command.index('--out-root')+1])/'mock'
            folder.mkdir()
            arms=[dict(arm=tag,n=100,failed=0,gates={'g':True},burn_reconcile={'ok':True},relayed=1,
                tdr=dict(events_opened=2,transfers_done=3,transfers_lost=0),legs={'tdr_legs':6},
                throughput_ctx_per_s=120,coord={'achieved_injection_ctx_per_s':120}) for tag in de.tags(child)]
            write_json(folder/'summary.json',dict(passed=True,status={'complete':True},arms=arms))
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def wait(self): return 0
    monkeypatch.setattr(de.subprocess,'Popen',FakeProcess)
    assert de.main(tmp_path,'controlled',[])==0
    output=next((tmp_path/'out').iterdir())
    assert len(calls)==5
    assert json.loads((output/'report.json').read_text(encoding='utf-8'))['complete']
    audit=(output/'direction_audit.csv').read_text(encoding='utf-8-sig')
    assert 'controlled_count' in audit and 'realized_target_destination_share' in audit
    # Reuse the same prepared rows, rather than recompute control bits at runtime.
    assert de.main(tmp_path,'controlled',['--prepared',str(output)])==0
    assert len(calls)==10
