"""Observational hooks copied ONLY into exp042's private backend snapshot."""
import csv
import time
from pathlib import Path

def install(engine, counter, directory, broker_idx, fee_wei):
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    path=directory/f'broker_{broker_idx}_costs.csv'
    fields=['broker_idx','transaction_index','observed_monotonic_ns','transfer_hash',
            'src_shard','dst_shard','amount_wei','burn_block','cost_wei']
    with path.open('w',newline='',encoding='utf-8') as f:
        csv.DictWriter(f,fieldnames=fields).writeheader()
    original_emit=engine._emit
    def emit(row):
        # One global ordering of completed USER CTXs, shared by all brokers.
        with counter.get_lock():
            counter.value+=1
            row['accounting_index']=int(counter.value)
            row['accounting_observed_ns']=time.monotonic_ns()
        return original_emit(row)
    engine._emit=emit
    seen=set()
    def record_cost(transfer):
        identity=str(transfer['h1'])
        if identity in seen:raise RuntimeError('Duplicate TDR fee recognition')
        seen.add(identity)
        with counter.get_lock():
            index=int(counter.value);timestamp=time.monotonic_ns()
        row=dict(broker_idx=broker_idx,transaction_index=index,observed_monotonic_ns=timestamp,
                 transfer_hash=identity,src_shard=transfer['src'],dst_shard=transfer['dst'],
                 amount_wei=transfer['amount'],burn_block=transfer['burn_block'],cost_wei=fee_wei)
        with path.open('a',newline='',encoding='utf-8') as f:
            csv.DictWriter(f,fieldnames=fields).writerow(row)
    engine._exp042_record_cost=record_cost
