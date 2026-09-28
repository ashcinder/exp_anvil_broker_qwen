"""No Anvil: source preservation, mapping, sampling, held-out split and reporting."""
import copy
import json
import io
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from brokerlab.config import load_config, ETH
from brokerlab import directional_workload as dw
from brokerlab import directional_experiment as de
from brokerlab.mapped_workload import load_prepared, write_json, sha256_file

ROOT = Path(__file__).resolve().parents[1]


def cfg(method="natural"):
    names = {"natural":"exp014_natural_direction", "learned":"exp015_learned_direction", "sampled":"exp016_sampled_direction"}
    return load_config(ROOT / "experiments" / names[method] / "config.yaml")


def row(i, source=1, dest=0, amount=ETH//10):
    return dict(ctx_id=f"tx{i}", orig_from=hex(source), orig_to=hex(dest),
                amount_wei=amount, sender_idx=200+source%200, receiver_idx=200+dest%200,
                source_line=i+2, source_block=str(100+i//3), source_timestamp=str(i))


def small_cfg(method="natural", count=10):
    c=cfg(method)
    return replace(c,scale=replace(c.scale,num_brokers=1),
                   exp={**c.exp,"ctx_per_broker":count,"rate":1,"audit_window_blocks":2})


def test_snapshot_preserves_and_reuses_source(tmp_path):
    source=tmp_path/"original.csv"
    source.write_bytes(b"from,to,value\n1,2,500\n")
    original=source.read_bytes()
    path,manifest=dw.snapshot_source(source,tmp_path/"copy")
    assert source.read_bytes()==path.read_bytes()==original
    assert dw.snapshot_source(source,tmp_path/"copy")[1]==manifest
    path.write_bytes(b"tampered")
    with pytest.raises(ValueError,match="changed"):
        dw.snapshot_source(source,tmp_path/"copy")
    assert source.read_bytes()==original


def test_snapshot_does_not_overwrite_existing(tmp_path):
    source=tmp_path/"original.csv"
    source.write_bytes(b"source")
    target=tmp_path/"copy"
    target.mkdir()
    (target/source.name).write_bytes(b"unrelated")
    with pytest.raises(ValueError,match="Incomplete"):
        dw.snapshot_source(source,target)
    assert (target/source.name).read_bytes()==b"unrelated"


def test_natural_disjoint_windows_no_invented_rows():
    c=small_cfg()
    pool=[row(i,source=1,dest=0) if i<10 or i>=20 else row(i,source=0,dest=1) for i in range(40)]
    scenarios,design=dw.natural_windows(pool,c,10)
    assert len(scenarios)==3 and len(design["window_catalog"])==4
    used=[]
    for _,rows,mapping,info in scenarios:
        assert len(rows)==10 and not mapping
        dw.ensure_order(rows)
        used.extend(r["ctx_id"] for r in rows)
    assert len(used)==len(set(used))


def test_learned_mapping_uses_only_train_and_is_role_invariant():
    c=small_cfg("learned",10)
    c.exp.update(train_candidates=30,max_receiver_address_fraction=1,min_address_observations=1)
    train=[row(i,source=1,dest=2) for i in range(30)]
    test=[row(i,source=2,dest=3) for i in range(30,80)]
    scenarios,design=dw.learned_layouts(train+test,c,10)
    learned=scenarios[1]
    assert learned[2][dw.normalized("0x2")]==0
    assert all(r["src_shard"]==0 for r in learned[1])  # Receiver in train, sender in test stays in same shard.
    assert min(int(r["source_block"]) for r in learned[1])>int(train[-1]["source_block"])
    # A new receiver in TEST cannot enter the training-derived address table.
    assert dw.normalized("0x3") not in learned[2]


def test_sampling_exact_quotas_preserves_amount_and_order():
    rows=[]
    for i in range(100):
        source,dest=[(1,0),(0,1),(1,2)][i%3]
        rows.append(row(i,source,dest))
    selected,quotas=dw.stratified_sample(dw.mapped(rows),20,0,0.8,0.5)
    assert len(selected)==20
    assert sum(r["dst_shard"]==0 for r in selected)==8
    assert sum(r["src_shard"]==0 for r in selected)==2
    dw.ensure_order(selected)
    assert all(r["amount_wei"]==ETH//10 for r in selected)
    with pytest.raises(ValueError,match="Insufficient"):
        dw.stratified_sample(dw.mapped([row(i) for i in range(30)]),20,0,0.8,0.5)


def test_schema2_roundtrip_and_reject_role_dependent_mapping(tmp_path):
    c=small_cfg()
    rows=dw.mapped([row(i,source=1,dest=2) for i in range(10)],{dw.normalized("0x2"):0})
    item=dw.export_scenario(tmp_path/"s","s",rows,{dw.normalized("0x2"):0},{},c,{})
    test_cfg=replace(c,exp={**c.exp,"prepared_workload_path":item["path"],"prepared_workload_sha256":item["sha256"]})
    loaded,total=load_prepared(test_cfg)
    assert len(loaded)==10 and total==ETH
    p=json.loads(Path(item["path"]).read_text(encoding="utf-8"))
    p["rows"][0]["dst_shard"]=2
    with pytest.raises(ValueError,match="mapping"):
        dw.validate_prepared(p,c)


@pytest.mark.parametrize("issue",["duplicate","order","amount","count","account"])
def test_schema2_rejects_corruption(issue):
    c=small_cfg()
    rows=[{**r,"arrival_pos":i} for i,r in enumerate(dw.mapped([row(i) for i in range(10)]))]
    p=dict(schema=2,num_shards=16,num_users=200,user_base_index=200,mapping={},rows=rows)
    if issue=="duplicate": rows[1]["ctx_id"]=rows[0]["ctx_id"]
    if issue=="order": rows[1]["source_line"]=rows[0]["source_line"]
    if issue=="amount": rows[0]["amount_wei"]=0
    if issue=="count": rows.pop()
    if issue=="account": rows[0]["sender_idx"]=1
    with pytest.raises(ValueError): dw.validate_prepared(p,c)


def test_report_partial_not_complete_and_combined_median(tmp_path):
    c,_=de.resolve(small_cfg(),"natural")
    c.exp["sessions"]=3
    scenarios=[dict(name="s")]
    records=[]
    for session,(relay,transfers) in enumerate([(1,100),(10,1),(20,2)],1):
        arms=[dict(arm=tag,n=10,failed=0,gates={"g":True},burn_reconcile={"ok":True},
                   relayed=relay,tdr=dict(events_opened=2,transfers_done=transfers,transfers_lost=0),
                   legs=dict(tdr_legs=2*transfers),throughput_ctx_per_s=1,
                   coord=dict(achieved_injection_ctx_per_s=1)) for tag in de.tags(c)]
        p=tmp_path/f"{session}.json"
        write_json(p,dict(passed=True,status=dict(complete=True),arms=arms))
        records.append(dict(scenario="s",session=session,returncode=0,summary=str(p)))
        report=de.summarize(records,tmp_path,c,scenarios)
        assert report["complete"]==(session==3)
    assert report["aggregates"][0]["relay_plus_transfers"]==22
    assert report["aggregates"][0]["relay"]+report["aggregates"][0]["tdr_transfers"]==12


@pytest.mark.parametrize("method,name",[("natural","exp014_natural_direction"),("learned","exp015_learned_direction"),("sampled","exp016_sampled_direction")])
def test_dry_run_never_prepares_or_executes(method,name,monkeypatch):
    def fail(*a,**kw): pytest.fail("No source copying or processes in dry-run")
    monkeypatch.setattr(de,"prepare",fail)
    monkeypatch.setattr(de.subprocess,"Popen",fail)
    assert de.main(ROOT/"experiments"/name,method,["--dry-run"])==0


@pytest.mark.parametrize("returncode",[0,1])
def test_runner_reuses_frozen_workload_with_mocked_children(tmp_path,monkeypatch,returncode):
    c,_=de.resolve(small_cfg(),"natural")
    config=tmp_path/"config.yaml"
    config.write_text(yaml.safe_dump(de.to_params_dict(c)),encoding="utf-8")
    bundle_dir=tmp_path/"prepared"
    bundle_dir.mkdir()
    scenarios=[]
    for index in range(3):
        name=f"s{index}"
        item=dw.export_scenario(bundle_dir/name,name,dw.mapped([row(i) for i in range(10)]),{}, {},c,{})
        scenarios.append({k:v for k,v in item.items() if k!="audit"})
    write_json(bundle_dir/"prepared.json",dict(fingerprint=de.fingerprint(c,"natural"),scenarios=scenarios))
    calls=[]
    class FakeProcess:
        def __init__(self,command,**kwargs):
            calls.append(command)
            self.stdout=io.StringIO("mock child: no chains started\n")
            child=load_config(command[command.index("--config")+1])
            folder=Path(command[command.index("--out-root")+1])/"mock_time"
            folder.mkdir()
            assert child.exp["prepared_workload_sha256"] in {s["sha256"] for s in scenarios}
            rows,total=load_prepared(child)
            assert len(rows)==10
            arms=[dict(arm=tag,n=10,failed=0,gates={"g":True},burn_reconcile={"ok":True},
                       relayed=1,tdr=dict(events_opened=1,transfers_done=1,transfers_lost=0),
                       legs=dict(tdr_legs=2),throughput_ctx_per_s=1,
                       coord=dict(achieved_injection_ctx_per_s=1)) for tag in de.tags(child)]
            write_json(folder/"summary.json",dict(passed=returncode==0,status=dict(complete=returncode==0),arms=arms))
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def wait(self): return returncode
    monkeypatch.setattr(de.subprocess,"Popen",FakeProcess)
    monkeypatch.setattr(de,"ports_free",lambda c:None)
    if returncode:
        with pytest.raises(RuntimeError,match="integrity failure"):
            de.main(tmp_path,"natural",["--prepared",str(bundle_dir)])
    else:
        assert de.main(tmp_path,"natural",["--prepared",str(bundle_dir)])==0
    output=next((tmp_path/"out").iterdir())
    report=json.loads((output/"report.json").read_text(encoding="utf-8"))
    assert report["complete"]==(returncode==0)
    assert len(calls)==(1 if returncode else 3)
    assert (output/"report.md").exists()
