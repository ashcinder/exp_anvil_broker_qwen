"""Review regressions: grid coverage, snapshot correctness, resume integrity and plot completeness."""
import copy
import csv
import json
from pathlib import Path
import pytest
import yaml
from brokerlab.config import load_config, to_params_dict
from experiments.exp025_regime_shift_sensitivity import runner, pipeline
from experiments.exp034_qmin_floor import plot_sensitivity
ROOT=Path(__file__).resolve().parents[1]
HERE=ROOT/'experiments/exp034_qmin_floor'

def plan():
    cfg=load_config(HERE/'config.yaml')
    space=yaml.safe_load((HERE/'search_space.yaml').read_text())
    return cfg,space,runner.tasks_for(cfg,'exp034',space)

def test_default_plan_covers_all_requested_points_and_ten_paired_rounds():
    cfg,space,tasks=plan()
    assert len(tasks)==540
    assert len({t['id'] for t in tasks})==540
    assert space['epsilon']['values']==[.05,.1,.2,.3,.5,.7,1,1.5,2,4,8,16]
    assert space['low_water_ratio']['values']==[.05,.1,.15,.2,.3,.4,.5,.6,.7,.8,.9,1]
    assert cfg.scale.num_brokers*cfg.exp['ctx_per_broker']==40000
    for dimension,spec in space.items():
        for value in spec['values']:
            group=[t for t in tasks if t['sweep']==dimension and t['value']==value]
            assert [t['round'] for t in group]==list(range(1,11))
            assert [t['route_seed'] for t in group]==list(range(7,98,10))

def test_actual_token_and_parameter_snapshot_agree_at_every_point():
    cfg,_,tasks=plan()
    for task in tasks:
        effective=runner.task_cfg(cfg,task)
        _,engine,_,_,eps,_,hl,q,_=pipeline._arm_spec(task['token'])
        assert effective.exp['tdr_epsilon']==eps
        assert effective.exp['tdr_ewma_half_life']==hl
        assert effective.exp['tdr_q_min']==q
        assert effective.exp['low_water_ratio']==task['low_water_ratio']
        runner.validate_task_params(task,dict(params=to_params_dict(effective),arms=[dict(engine=engine)]))

def saved_task(tmp_path):
    cfg,_,tasks=plan();task=copy.deepcopy(tasks[0]);task['summary']='summary.json';task['status']='complete'
    stats=dict(arm=task['tag'],engine='topup_lowwater',n=1,served=1,relayed=0,failed=0,
               gates={'G1':True},burn_reconcile={'ok':True},
               tdr=dict(transfers_done=0,moved_wei=0,transfers_lost=0,lost_in_transit_wei=0),throughput_ctx_per_s=1)
    workload=[{'ctx_id':'unit-test-fixture'}]
    doc=dict(task_id=task['id'],workload_sha256=runner.digest(workload),
             params=to_params_dict(runner.task_cfg(cfg,task)),arms=[stats],status={'complete':True})
    runner.write_json(tmp_path/'summary.json',doc)
    return task,workload,doc

@pytest.mark.parametrize('field',['low_water_ratio','tdr_epsilon','tdr_q_min','tdr_ewma_half_life','arms'])
def test_resume_rejects_mismatched_execution_parameters(tmp_path,field):
    task,workload,doc=saved_task(tmp_path)
    assert runner.load_completed(tmp_path,task,workload,validate=False)['n']==1
    doc['params']['exp'][field]='wrong'
    runner.write_json(tmp_path/'summary.json',doc)
    with pytest.raises(ValueError,match='parameters'):
        runner.load_completed(tmp_path,task,workload,validate=False)

def test_resume_rejects_failed_gate_even_when_not_reexporting_rows(tmp_path):
    task,workload,doc=saved_task(tmp_path)
    doc['arms'][0]['gates']['G1']=False
    runner.write_json(tmp_path/'summary.json',doc)
    with pytest.raises(ValueError,match='gates'):
        runner.load_completed(tmp_path,task,workload,validate=False)

def result_fixture(tmp_path):
    task,workload,doc=saved_task(tmp_path)
    runner.write_json(tmp_path/'manifest.json',dict(experiment='exp034',algorithm_revision='exp034-independent-lowwater-v3',
                      formal=False,tasks=[task],workload_sha256=runner.digest(workload)))
    runner.write_json(tmp_path/'report.json',dict(formal=False,status={'complete':True}))
    row=dict(task_id=task['id'],round=task['round'],route_seed=task['route_seed'],sweep=task['sweep'],
             value=task['value'],n=1,served=1,relayed=0,transfers=0,moved_eth=0)
    with (tmp_path/'raw_results.csv').open('w',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(row));writer.writeheader();writer.writerow(row)
    return doc

def test_plot_rejects_corrupt_parameter_record(tmp_path):
    doc=result_fixture(tmp_path)
    summary,formal=plot_sensitivity.collect(tmp_path)
    assert len(summary)==1 and not formal
    doc['params']['exp']['low_water_ratio']=.7
    runner.write_json(tmp_path/'summary.json',doc)
    with pytest.raises(ValueError,match='parameters'):plot_sensitivity.collect(tmp_path)

def test_all_epsilon_points_are_in_log_plot(monkeypatch,tmp_path):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    _,space,_=plan()
    values=space['epsilon']['values']
    records=[dict(sweep='epsilon',value=e,rounds=1,
                  service_ratio_mean=.8,service_ratio_sd=0,transfers_mean=1,transfers_sd=0,
                  moved_eth_mean=1,moved_eth_sd=0) for e in values]
    monkeypatch.setattr(plot_sensitivity,'collect',lambda source:(records,False))
    monkeypatch.setattr(plot_sensitivity.sys,'argv',['plot','--experiment-source',str(tmp_path),'--output',str(tmp_path/'figs')])
    figures=[];close=plt.close
    monkeypatch.setattr(plt,'close',lambda figure:figures.append(figure))
    assert plot_sensitivity.main()==0
    figure=figures[-1]
    for axis in figure.axes:
        assert axis.get_xscale()=='log'
        assert list(axis.lines[0].get_xdata())==values
        assert axis.get_xlim()[0] < min(values) and axis.get_xlim()[1] > max(values)
    assert (tmp_path/'figs/exp034_sensitivity.png').is_file()
    close(figure)


def test_default_windows_paths_fit_even_for_longest_parameter_names():
    _,_,tasks=plan()
    run=HERE/'out'/'20261012_000000_000000'
    for number,task in enumerate(tasks,1):
        directory=runner.task_directory(run,number)/task['tag']
        for suffix in ('logs/anvil_shard_15.log','execution_summary.json','balances_by_block.csv'):
            assert len(str(directory/suffix)) < 250
