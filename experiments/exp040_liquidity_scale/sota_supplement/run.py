"""Append ONLY AutoLoop/Spider trials to completed Exp040 balance data."""
from __future__ import annotations
import argparse
import copy
import csv
import hashlib
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROTOCOL = 'exp040-sota-append-v1'
METHODS = ('autoloop', 'spider')
KNOBS = dict(sota_autoloop_low=.3, sota_autoloop_high=.7,
             sota_capacity_mult=2., sota_check_blocks=10, sota_spider_vol_mult=.25)
DEFAULT_BASE = Path('C:/Users/hzy/Downloads/out/out/20261016_003612_099146')


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def save(path, value):
    path = Path(path)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding='utf-8')
    tmp.replace(path)


def read_csv(path):
    with Path(path).open(encoding='utf-8-sig', newline='') as f:
        return list(csv.DictReader(f))


def write_csv(path, rows):
    rows = list(rows)
    if not rows:
        return
    path = Path(path)
    tmp = path.with_suffix('.csv.tmp')
    with tmp.open('w', encoding='utf-8', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    tmp.replace(path)


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda: f.read(1024 * 1024), b''):
            h.update(b)
    return h.hexdigest()


def fingerprint(root):
    return {p.relative_to(root).as_posix(): sha(p) for p in root.rglob('*.py')}


def verify_hashes(root, hashes):
    for rel, expected in hashes.items():
        if sha(root / rel) != expected:
            raise ValueError(f'Frozen/input file changed: {root / rel}')


def load_base(base):
    manifest = read_json(base / 'manifest.json')
    config = read_json(base / 'config.json')
    report = read_json(base / 'report.json')
    if manifest.get('smoke') or not report['complete'] or report['completed_tasks'] != 100:
        raise ValueError('Requires the completed formal 100-task Exp040 balance sweep')
    tasks = manifest['tasks']
    if len(tasks) != 100 or any(t['status'] != 'complete' or t['policy'] != 'topup'
                               or t['transactions'] != 40000 or 'balance' not in t['sweeps'] for t in tasks):
        raise ValueError('Base tasks are not the expected balance sweep')
    if sha(base / 'w/40000.csv') != manifest['workload_hashes']['40000']:
        raise ValueError('Saved 40k workload hash mismatch; do not rebuild it')
    if config['scale']['num_brokers'] != 50 or config['chain']['num_shards'] != 16:
        raise ValueError('Expected 50 brokers and 16 shards')
    # Read task-specific configurations, rather than the currently installed YAML.
    configs = {}
    for t in tasks:
        doc = read_json(base / t['attempts'][-1]['path'] / 'summary.json')
        if doc['task_id'] != t['id'] or doc['workload_sha256'] != manifest['workload_hashes']['40000']:
            raise ValueError('Base task identity/workload mismatch')
        configs[t['id']] = doc['effective_config']
    rows = read_csv(base / 'per_broker.csv')
    if len(rows) != 5000:
        raise ValueError('Missing original per-broker results')
    for r in rows:
        if int(r['income_wei']) - int(r['modeled_cost_wei']) != int(r['net_profit_wei']):
            raise ValueError('Original fee accounting mismatch')
    expected_sources = {n.replace('\\', '/'): h for n, h in manifest['sources'].items()}
    provenance = read_json(HERE / 'runtime/provenance.json')
    for rel, h in provenance['original_hashes'].items():
        matches = [v for k, v in expected_sources.items() if k.endswith('/' + rel)]
        if matches != [h]:
            raise ValueError('Vendored runtime belongs to a different base run: ' + rel)
    return manifest, config, configs


def build_tasks(base_tasks, methods, balances=None, rounds=None, smoke=False):
    if not methods or set(methods) - set(METHODS) or len(methods) != len(set(methods)):
        raise ValueError('Only autoloop and spider can be scheduled; TDR/plain cannot run here')
    chosen = [t for t in base_tasks if (balances is None or t['balance_eth'] in balances)
              and (rounds is None or t['round'] <= rounds)]
    if balances and set(balances) - {t['balance_eth'] for t in base_tasks}:
        raise ValueError('Balance selector must select existing Exp040 cells')
    if rounds is not None and not 1 <= rounds <= 10:
        raise ValueError('--rounds must be between 1 and 10 (original rounds, same seeds)')
    if smoke:
        chosen = chosen[:1]
    tasks = []
    for i, t in enumerate(chosen):
        order = methods if i % 2 == 0 else list(reversed(methods))
        for method in order:
            b, n = (t['balance_eth'], 2000) if smoke else (t['balance_eth'], t['transactions'])
            tasks.append(dict(id=f'r{t["round"]:02d}_b{b:g}_n{n}_{method}',
                source_task_id=t['id'], round=t['round'], route_seed=t['route_seed'],
                balance_eth=b, transactions=n, policy=method, sweeps=['balance'],
                status='pending', attempts=[]))
    if not tasks:
        raise ValueError('Empty task selection')
    return tasks


def prepare(args):
    base = args.base_run.resolve()
    original, config, configs = load_base(base)
    balances = [float(x) for x in args.balances.split(',')] if args.balances else None
    tasks = build_tasks(original['tasks'], args.methods.split(','), balances, args.rounds, args.smoke)
    plan = dict(protocol=PROTOCOL, base_run=str(base), smoke=args.smoke, tasks=len(tasks),
        methods=sorted({t['policy'] for t in tasks}),
        balances_eth=sorted({t['balance_eth'] for t in tasks}),
        route_seeds=sorted({t['route_seed'] for t in tasks}),
        transactions_per_task=tasks[0]['transactions'],
        new_user_ctx=sum(t['transactions'] for t in tasks),
        existing_tdr_runs_reused=100, tdr_runs_to_execute=0, baseline_parameters=KNOBS,
        fee_income_per_user_ctx_gwei=.0021, cost_per_confirmed_transfer_gwei=.021,
        num_brokers=50, num_shards=16,
        source_workload_sha256=original['workload_hashes']['40000'])
    print(json.dumps(plan, ensure_ascii=False, indent=2), flush=True)
    if args.dry_run:
        return None
    output = args.output_root.resolve()
    run = output / (('smoke_' if args.smoke else '') + datetime.now().strftime('%Y%m%d_%H%M%S_%f'))
    run.mkdir(parents=True)
    (run / 'reference').mkdir()
    names = ['manifest.json', 'config.json', 'report.json', 'results.csv', 'per_broker.csv']
    hashes = {}
    for name in names:
        shutil.copy2(base / name, run / 'reference' / name)
        hashes[name] = sha(base / name)
    save(run / 'reference/task_configs.json', configs)
    hashes['task_configs.json'] = sha(run / 'reference/task_configs.json')
    (run / 'w').mkdir()
    dest = run / 'w/40000.csv'
    shutil.copy2(base / 'w/40000.csv', dest)
    if args.smoke:
        workload = read_csv(dest)[:2000]
        write_csv(run / 'w/smoke.csv', workload)
    shutil.copytree(HERE / 'runtime', run / 'backend', ignore=shutil.ignore_patterns('__pycache__'))
    (run / 'code').mkdir()
    for name in ('run.py', 'plot.py'):
        shutil.copy2(HERE / name, run / 'code' / name)
    manifest = dict(protocol=PROTOCOL, plan=plan, base_run=str(base), smoke=args.smoke,
        tasks=tasks, reference_hashes=hashes, backend_hashes=fingerprint(run / 'backend'),
        code_hashes=fingerprint(run / 'code'), workload_sha256=sha(dest),
        evaluated_workload_sha256=sha(run / ('w/smoke.csv' if args.smoke else 'w/40000.csv')),
        anvil_bin=str(Path(args.anvil_bin or shutil.which('anvil')).resolve())
            if (args.anvil_bin or shutil.which('anvil')) else None,
        lock_port=args.lock_port, base_port=args.base_port)
    save(run / 'manifest.json', manifest)
    import importlib.metadata
    versions = {}
    for name in ('web3', 'eth-account', 'eth-utils', 'PyYAML', 'psutil', 'numpy', 'matplotlib'):
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = None
    save(run / 'environment.json', dict(python=sys.version, executable=sys.executable,
        platform=platform.platform(), packages=versions, anvil_bin=manifest['anvil_bin'],
        anvil_sha256=sha(manifest['anvil_bin']) if manifest['anvil_bin'] else None))
    aggregate(run, manifest)
    print('RUN_DIRECTORY=' + str(run), flush=True)
    return run


def effective(config, t, run, manifest):
    c = copy.deepcopy(config)
    c['label'] = 'exp040_sota_supplement'
    c['exp'].update(KNOBS)
    c['exp'].update(arms=t['policy'], route_seed=t['route_seed'], balances_eth=t['balance_eth'],
                    ctx_per_broker=t['transactions'] // c['scale']['num_brokers'])
    c['broker']['initial_balance_eth'] = t['balance_eth']
    c['tdr']['enabled'] = True
    c['chain']['base_port'] = manifest['base_port']
    c['chain']['anvil_bin'] = manifest['anvil_bin'] or shutil.which('anvil')
    if not c['chain']['anvil_bin']:
        raise FileNotFoundError('anvil not found; pass --anvil-bin <anvil.exe>')
    if manifest['smoke']:
        c['exp'].update(ctx_per_broker=40, drain_tail_s=240, curve_points=20)
    return c


def free_ports(preferred, count):
    for base in range(preferred, 49152 - count, count):
        probes = []
        try:
            for port in range(base, base + count):
                sock = socket.socket()
                probes.append(sock)
                if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                sock.bind(('127.0.0.1', port))
            return base
        except OSError:
            pass
        finally:
            for sock in probes:
                sock.close()
    raise RuntimeError('No free contiguous RPC ports; no processes were stopped')


def worker(run, task_id, attempt):
    import yaml
    from dataclasses import replace
    sys.path.insert(0, str(run / 'backend'))
    from brokerlab.config import load_config, to_params_dict
    from brokerlab.procmon import RSSWatchdog
    from experiments.exp025_regime_shift_sensitivity import pipeline
    from exp040_accounting import verify_and_account
    m = read_json(run / 'manifest.json')
    verify_hashes(run / 'backend', m['backend_hashes'])
    t = next(t for t in m['tasks'] if t['id'] == task_id)
    if t['policy'] not in METHODS:
        raise ValueError('Refusing to execute existing TDR/plain arm')
    config = read_json(run / 'reference/task_configs.json')[t['source_task_id']]
    c = effective(config, t, run, m)
    path = attempt / 'params.yaml'
    path.write_text(yaml.safe_dump(c, sort_keys=False), encoding='utf-8')
    cfg = load_config(path)
    cfg = replace(cfg, chain=replace(cfg.chain, base_port=free_ports(cfg.chain.base_port, cfg.chain.num_shards)))
    workload_path = run / ('w/smoke.csv' if m['smoke'] else 'w/40000.csv')
    if sha(workload_path) != m['evaluated_workload_sha256']:
        raise ValueError('Workload changed')
    workload = read_csv(workload_path)
    for row in workload:
        for key in ('src_shard', 'dst_shard', 'amount_wei', 'sender_idx', 'receiver_idx', 'arrival_pos', 'original_dst_shard'):
            row[key] = int(row[key])
    watchdog = RSSWatchdog(float(c['exp']['rss_cap_mb']))
    watchdog.start()
    started = time.monotonic()
    try:
        stats = pipeline.run_arm(cfg, t['policy'], workload, sum(x['amount_wei'] for x in workload),
            attempt, watchdog, engine=t['policy'], fund_eth=t['balance_eth'],
            eps=c['exp']['tdr_epsilon'], chi=c['exp']['tdr_chi_blocks'],
            hl=c['exp']['tdr_ewma_half_life'], qmin=c['exp']['tdr_q_min'])
    finally:
        watchdog.stop()
    save(attempt / 'stats.json', stats)
    arm = attempt / t['policy']
    rows, moves = read_csv(arm / 'ctx_rows.csv'), read_csv(arm / 'tdr_moves.csv')
    finals, curve = verify_and_account(c, t, stats, rows, moves, workload)
    write_csv(attempt / 'per_broker.csv', finals)
    write_csv(attempt / 'revenue_curve.csv', curve)
    artifact_names = ['ctx_rows.csv', 'tdr_moves.csv']
    # Empty transfer files are valid evidence of zero transfers, never manufacture moves.
    save(attempt / 'summary.json', dict(task_id=t['id'], protocol=PROTOCOL,
        source_task_id=t['source_task_id'], effective_config=c, resolved_params=to_params_dict(cfg),
        workload_sha256=sha(workload_path), stats=stats, elapsed_s=time.monotonic() - started,
        artifacts={n: sha(arm / n) for n in artifact_names},
        derived={n: sha(attempt / n) for n in ('per_broker.csv', 'revenue_curve.csv')}))
    print('TASK_VERIFIED=' + t['id'], flush=True)


def validate_task(run, t):
    attempt = run / t['attempts'][-1]['path']
    doc = read_json(attempt / 'summary.json')
    if doc['task_id'] != t['id'] or doc['protocol'] != PROTOCOL:
        raise ValueError('Saved task identity mismatch')
    m = read_json(run / 'manifest.json')
    if doc['workload_sha256'] != m['evaluated_workload_sha256']:
        raise ValueError('Completed task workload mismatch')
    c = read_json(run / 'reference/task_configs.json')[t['source_task_id']]
    if doc['effective_config'] != effective(c, t, run, m):
        raise ValueError('Completed task configuration mismatch')
    verify_hashes(attempt / t['policy'], doc['artifacts'])
    verify_hashes(attempt, doc['derived'])


def aggregate(run, m):
    rows, system = [], []
    for t in m['tasks']:
        if t['status'] != 'complete':
            continue
        path = run / t['attempts'][-1]['path']
        a = read_csv(path / 'per_broker.csv')
        meta = {k: t[k] for k in ('id', 'source_task_id', 'round', 'route_seed', 'balance_eth', 'transactions', 'policy')}
        meta['task_id'] = meta.pop('id')
        rows.extend(dict(**meta, **v) for v in a)
        sums = {k: sum(int(v[k]) for v in a) for k in ('assigned', 'served', 'relay', 'income_wei',
                'upper_wei', 'tdr_transfers', 'modeled_cost_wei', 'net_profit_wei')}
        system.append(dict(**meta, **sums))
    write_csv(run / 'per_broker.csv', rows)
    write_csv(run / 'results.csv', system)
    save(run / 'report.json', dict(protocol=PROTOCOL, smoke=m['smoke'], complete=len(system) == len(m['tasks']),
        completed_tasks=len(system), planned_tasks=len(m['tasks']),
        failed_tasks=[t['id'] for t in m['tasks'] if t['status'] == 'failed'],
        existing_tdr_runs_reused=100, tdr_runs_executed=0,
        new_methods=list(METHODS), fee_per_served_ctx_wei=2100000, modeled_cost_per_transfer_wei=21000000,
        routing='Same historical coordinator; dynamic assignment may differ between methods',
        potential_reference='Original TDR assigned-user fee income; not a universal ceiling across methods'))


def execute_plan(run, max_tasks=None, no_plot=False):
    m = read_json(run / 'manifest.json')
    if m['protocol'] != PROTOCOL:
        raise ValueError('Different supplement protocol')
    verify_hashes(run / 'backend', m['backend_hashes'])
    verify_hashes(run / 'code', m['code_hashes'])
    verify_hashes(run / 'reference', m['reference_hashes'])
    if sha(run / 'w/40000.csv') != m['workload_sha256']:
        raise ValueError('Original saved workload changed')
    for t in m['tasks']:
        if t['status'] == 'complete':
            validate_task(run, t)
    executed = 0
    lock = socket.socket()
    if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
        lock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    try:
        lock.bind(('127.0.0.1', m['lock_port']))
    except OSError as e:
        raise RuntimeError('Another supplement runner holds its lock; do not start a duplicate') from e
    with lock:
        for i, t in enumerate(m['tasks']):
            if t['status'] == 'complete':
                continue
            if (run / 'STOP_AFTER_TASK').exists():
                print('STOP_AFTER_TASK detected; current results saved, resume later.', flush=True)
                break
            if max_tasks is not None and executed >= max_tasks:
                break
            verify_hashes(run / 'backend', m['backend_hashes'])
            path = Path('t') / f'{i:04d}' / f'a{len(t["attempts"]) + 1:03d}'
            attempt = run / path
            attempt.mkdir(parents=True)
            record = dict(path=path.as_posix(), started_at=datetime.now().isoformat(), status='running')
            t['attempts'].append(record)
            t['status'] = 'running'
            save(run / 'manifest.json', m)
            print(f'[{i + 1}/{len(m["tasks"])}] {t["id"]}', flush=True)
            try:
                with (attempt / 'stdout.log').open('w', encoding='utf-8') as out:
                    env = dict(os.environ, PYTHONIOENCODING='utf-8')
                    proc = subprocess.run([sys.executable, '-u', str(run / 'code/run.py'),
                        '--worker', str(run), t['id'], str(attempt)], stdout=out,
                        stderr=subprocess.STDOUT, env=env)
                if proc.returncode:
                    raise RuntimeError(f'Worker exited {proc.returncode}; see {attempt / "stdout.log"}')
                validate_task(run, t)
                t['status'] = record['status'] = 'complete'
                executed += 1
            except BaseException as e:
                t['status'] = record['status'] = 'failed'
                record['error'] = repr(e)
                raise
            finally:
                record['ended_at'] = datetime.now().isoformat()
                save(run / 'manifest.json', m)
                aggregate(run, m)
        if all(t['status'] == 'complete' for t in m['tasks']) and not no_plot and not m['smoke']:
            if {t['policy'] for t in m['tasks']} == set(METHODS):
                subprocess.run([sys.executable, str(run / 'code/plot.py'), str(run)], check=True)
            else:
                print('Selected baseline complete; four-series chart requires both methods in the plan.', flush=True)
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--base-run', type=Path, default=DEFAULT_BASE)
    p.add_argument('--output-root', type=Path, default=HERE / 'out')
    p.add_argument('--methods', default='autoloop,spider')
    p.add_argument('--balances', help='Select existing balances, e.g. 15,45,1000')
    p.add_argument('--rounds', type=int, help='Select first N existing rounds; seeds unchanged')
    p.add_argument('--smoke', action='store_true', help='Original 50 brokers/16 shards, first 2000 CTX; NOT formal data')
    p.add_argument('--dry-run', action='store_true')
    p.add_argument('--prepare-only', action='store_true')
    p.add_argument('--resume', type=Path)
    p.add_argument('--background', action='store_true')
    p.add_argument('--max-tasks', type=int)
    p.add_argument('--no-plot', action='store_true')
    p.add_argument('--anvil-bin')
    p.add_argument('--base-port', type=int, default=36600)
    p.add_argument('--lock-port', type=int, default=45946)
    p.add_argument('--worker', nargs=3, help=argparse.SUPPRESS)
    args = p.parse_args(argv)
    if args.worker:
        worker(Path(args.worker[0]), args.worker[1], Path(args.worker[2]))
        return 0
    if args.max_tasks is not None and args.max_tasks <= 0:
        p.error('--max-tasks must be positive')
    run = args.resume.resolve() if args.resume else prepare(args)
    if run is None or args.prepare_only:
        return 0
    if args.background:
        command = [sys.executable, '-u', str(run / 'code/run.py'), '--resume', str(run)]
        if args.max_tasks:
            command += ['--max-tasks', str(args.max_tasks)]
        if args.no_plot:
            command += ['--no-plot']
        with (run / 'runner.log').open('a', encoding='utf-8') as log:
            proc = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT,
                env=dict(os.environ, PYTHONIOENCODING='utf-8'),
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        save(run / 'launch.json', dict(pid=proc.pid, command=command, started_at=datetime.now().isoformat()))
        print(json.dumps(dict(pid=proc.pid, run_directory=str(run), log=str(run / 'runner.log')), indent=2), flush=True)
        return 0
    print('RUN_DIRECTORY=' + str(run), flush=True)
    return execute_plan(run, args.max_tasks, args.no_plot)


if __name__ == '__main__':
    raise SystemExit(main())
