"""Isolated, matched TDR-OFF additions to Fig5b/c; never edit running experiments."""
from __future__ import annotations
import argparse
import copy
import csv
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import threading
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta
import statistics

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[1]
PROTOCOL = 'exp045-plain-matched-v1'
DEFAULT_C = Path('C:/Users/hzy/Downloads/out/out/20261016_003612_099146')
DROOT = Path('D:/BaiduNetdiskDownload/exp_anvil_broker_qwen/exp_anvil_broker_qwen')
DEFAULT_B = DROOT / 'experiments/exp040_liquidity_scale/out/20261016_080212_748363'
DEFAULT_REUSE = DROOT / 'experiments/exp041_fig1_plain_repeats/out/20261015_225055_032527'
DEFAULT_OUTPUT = Path('C:/Users/hzy/Downloads/out/exp045_plain_sensitivity')


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def save(path, obj):
    path = Path(path)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.replace(path)


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def rows(path):
    with Path(path).open(encoding='utf-8-sig', newline='') as f:
        return list(csv.DictReader(f))


def csv_write(path, data):
    if not data:
        return
    path = Path(path)
    tmp = path.with_suffix('.csv.tmp')
    with tmp.open('w', encoding='utf-8', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(data[0]))
        w.writeheader()
        w.writerows(data)
    tmp.replace(path)


def fingerprint(root):
    return {p.relative_to(root).as_posix(): sha(p) for p in root.rglob('*.py')}


def check(root, hashes):
    for name, expected in hashes.items():
        if sha(root / name) != expected:
            raise ValueError('Frozen/input evidence changed: ' + str(root / name))


def source_map(m):
    result = {}
    for name, digest in m['sources'].items():
        parts = name.replace('\\', '/').split('/')
        pos = next((i for i, x in enumerate(parts) if x in ('brokerlab', 'experiments')), None)
        if pos is not None:
            result['/'.join(parts[pos:])] = digest
    return result


def plain_config(c, t, manifest):
    c = copy.deepcopy(c)
    c['tdr']['enabled'] = False
    c['broker']['initial_balance_eth'] = t['balance_eth']
    c['exp'].update(arms='plain', balances_eth=t['balance_eth'], route_seed=t['route_seed'],
                    ctx_per_broker=t['transactions'] // c['scale']['num_brokers'],
                    shift_to_shard=-1, sota_training_ctx=0)
    c['chain'].update(base_port=manifest['base_port'], anvil_bin=manifest['anvil_bin'])
    if manifest['smoke']:
        c['exp'].update(drain_tail_s=240, curve_points=20)
    return c


def flatten(c, prefix=''):
    result = {}
    for key, value in c.items():
        name = prefix + '.' + key if prefix else key
        if isinstance(value, dict):
            result.update(flatten(value, name))
        else:
            result[name] = value
    return result


def compatible_plain(expected, actual):
    # Administrative plan metadata and RPC endpoint do not affect the saved replay.
    ignored = {'label', 'exp.experiment_kind', 'exp.balances_grid_eth', 'exp.transactions_grid',
               'chain.base_port', 'chain.anvil_bin'}
    a, b = flatten(expected), flatten(actual)
    different = [k for k in a.keys() | b.keys() if k not in ignored and a.get(k) != b.get(k)]
    if different:
        raise ValueError('Plain reuse has incompatible execution parameters: ' + repr(different))
    if actual['tdr']['enabled'] or actual['exp']['arms'] != 'plain':
        raise ValueError('Reuse is not TDR OFF')


def load_base(path, sweep):
    m = read(path / 'manifest.json')
    tasks = m['tasks']
    if m.get('smoke') or len(tasks) != (100 if sweep == 'c' else 80):
        raise ValueError('Not the expected formal completed sweep: ' + sweep)
    if any(t['status'] != 'complete' or t['policy'] != 'topup' for t in tasks):
        raise ValueError('Original TDR sweep is incomplete')
    runtime = read(HERE / 'runtime' / sweep / 'provenance.json')
    expected = source_map(m)
    if any(expected.get(n) != h for n, h in runtime['original_files'].items()):
        raise ValueError('Runtime/base historical source mismatch: ' + sweep)
    check(HERE / 'runtime' / sweep, runtime['hashes'])
    configs = {}
    for t in tasks:
        d = read(path / t['attempts'][-1]['path'] / 'summary.json')
        if d['task_id'] != t['id'] or d['workload_sha256'] != m['workload_hashes'][str(t['transactions'])]:
            raise ValueError('Original task identity/input mismatch')
        configs[t['id']] = d['effective_config']
    return m, configs


def build_tasks(bases, smoke=False, only='both'):
    out = []
    for sweep in ('c', 'b'):
        if only != 'both' and only != sweep:
            continue
        chosen = bases[sweep]['tasks']
        if smoke:
            chosen = [next(t for t in chosen if t['round'] == 1 and t['transactions'] == 40000
                           and t['balance_eth'] == (15 if sweep == 'c' else 45))]
        for i, t in enumerate(chosen):
            n = 2000 if smoke else t['transactions']
            out.append(dict(id=f'{sweep}_r{t["round"]:02d}_b{t["balance_eth"]:g}_n{n}_plain',
                sweep=sweep, source_task_id=t['id'], round=t['round'], route_seed=t['route_seed'],
                balance_eth=t['balance_eth'], transactions=n, source_transactions=t['transactions'],
                order=i, policy='plain', origin='new', status='pending', attempts=[]))
    # Alternate the two sweeps within each round; only one new chain runs at a time.
    return sorted(out, key=lambda t: (t['round'], t['order'], t['sweep']))


def prepare(args):
    paths = {'c': args.fig5c_base.resolve(), 'b': args.fig5b_base.resolve()}
    loaded = {s: load_base(p, s) for s, p in paths.items()}
    bases = {s: v[0] for s, v in loaded.items()}
    tasks = build_tasks(bases, args.smoke, args.only)
    reused = {}
    reuse = args.reuse_plain.resolve()
    if not args.smoke and not args.no_reuse and args.only != 'c':
        rm, rc = read(reuse / 'manifest.json'), read(reuse / 'config.json')
        if rm.get('smoke') or len(rm['tasks']) != 10 or any(t['status'] != 'complete' for t in rm['tasks']):
            raise ValueError('Expected completed ten-round Exp041')
        own, old = source_map(bases['b']), source_map(rm)
        originals = read(HERE / 'runtime/b/provenance.json')['original_files']
        if any(own.get(n) != old.get(n) for n in originals):
            raise ValueError('Exp041 does not use identical Fig5b execution sources')
        for t in tasks:
            if t['sweep'] != 'b' or (t['balance_eth'], t['transactions']) != (45, 40000):
                continue
            r = next(x for x in rm['tasks'] if x['round'] == t['round'] and x['route_seed'] == t['route_seed'])
            if r['policy'] != 'plain' or (r['balance_eth'], r['transactions']) != (45, 40000):
                raise ValueError('Exp041 reuse cell mismatch')
            if rm['workload_hashes']['40000'] != bases['b']['workload_hashes']['40000']:
                raise ValueError('Exp041 workload differs')
            t['origin'] = 'reused_exp041'
            reused[t['id']] = (r, reuse / r['attempts'][-1]['path'])
    plan = dict(protocol=PROTOCOL, smoke=args.smoke, observations=len(tasks),
        new_tasks=sum(t['origin'] == 'new' for t in tasks), reused=len(reused),
        fig5c=sum(t['sweep'] == 'c' for t in tasks), fig5b=sum(t['sweep'] == 'b' for t in tasks),
        new_user_ctx=sum(t['transactions'] for t in tasks if t['origin'] == 'new'),
        methods=['plain'], fee_per_served_ctx_wei=2100000, internal_transfers_expected=0,
        on_results_rerun=0, topology=dict(shards=16, brokers=50, users=200),
        original_runs={s: str(p) for s, p in paths.items()})
    print(json.dumps(plan, ensure_ascii=False, indent=2), flush=True)
    if args.dry_run:
        return None
    run = args.output_root.resolve() / (('smoke_' if args.smoke else '') + datetime.now().strftime('%Y%m%d_%H%M%S_%f'))
    run.mkdir(parents=True)
    (run / 'code').mkdir()
    for name in ('run.py', 'plot.py'):
        shutil.copy2(HERE / name, run / 'code' / name)
    for s in ('b', 'c'):
        shutil.copytree(HERE / 'runtime' / s, run / 'backend' / s, ignore=shutil.ignore_patterns('__pycache__'))
        ref = run / 'reference' / s
        ref.mkdir(parents=True)
        for name in ('manifest.json', 'config.json', 'per_broker.csv'):
            shutil.copy2(paths[s] / name, ref / name)
        save(ref / 'task_configs.json', loaded[s][1])
        dest = run / 'w' / s
        dest.mkdir(parents=True)
        counts = {t['source_transactions'] for t in tasks if t['sweep'] == s}
        for n in counts:
            src = paths[s] / 'w' / f'{n}.csv'
            if sha(src) != bases[s]['workload_hashes'][str(n)]:
                raise ValueError('Original saved workload changed')
            if args.smoke:
                csv_write(dest / '2000.csv', rows(src)[:2000])
            else:
                shutil.copy2(src, dest / src.name)
    anvil = args.anvil_bin or shutil.which('anvil') or str(Path.home() / '.foundry/bin/anvil.exe')
    if not Path(anvil).is_file():
        raise FileNotFoundError('Specify --anvil-bin')
    m = dict(protocol=PROTOCOL, plan=plan, tasks=tasks, smoke=args.smoke, created_at=datetime.now().isoformat(),
             base_port=args.base_port, lock_port=args.lock_port, anvil_bin=str(Path(anvil).resolve()),
             reference_hashes={p.relative_to(run / 'reference').as_posix(): sha(p) for p in (run / 'reference').rglob('*') if p.is_file()},
             backend_hashes=fingerprint(run / 'backend'), code_hashes=fingerprint(run / 'code'),
             workload_hashes={p.relative_to(run / 'w').as_posix(): sha(p) for p in (run / 'w').rglob('*.csv')},
             sota_results={'b': str(args.fig5b_sota.resolve()), 'c': str(args.fig5c_sota.resolve())})
    save(run / 'manifest.json', m)
    for t in tasks:
        if t['origin'] == 'new':
            continue
        r, src = reused[t['id']]
        dest = run / 'reuse' / t['id']
        (dest / 'plain').mkdir(parents=True)
        doc = read(src / 'summary.json')
        check(src / 'plain', doc['artifacts'])
        check(src, doc['derived'])
        compatible_plain(plain_config(loaded['b'][1][t['source_task_id']], t, m), doc['effective_config'])
        for name in ('ctx_rows.csv', 'tdr_moves.csv'):
            shutil.copy2(src / 'plain' / name, dest / 'plain' / name)
        for name in ('per_broker.csv', 'revenue_curve.csv', 'summary.json'):
            shutil.copy2(src / name, dest / ('original_summary.json' if name == 'summary.json' else name))
        save(dest / 'reuse_provenance.json', dict(source_attempt=str(src), source_task=r, summary_sha256=sha(src / 'summary.json')))
        subprocess.run([sys.executable, str(run / 'code/run.py'), '--verify-reuse', str(run), t['id'], str(dest)], check=True, env=dict(os.environ, PYTHONIOENCODING='utf-8'))
        t['status'] = 'complete'
        t['attempts'] = [dict(path=dest.relative_to(run).as_posix(), status='complete', reused=True,
            summary_sha256=sha(dest / 'summary.json'), ended_at=datetime.now().isoformat())]
        save(run / 'manifest.json', m)
    aggregate(run, m)
    save(run / 'environment.json', dict(python=sys.version, executable=sys.executable, anvil_sha256=sha(anvil)))
    save(args.output_root.resolve() / ('latest_smoke.json' if args.smoke else 'latest_formal.json'), dict(run=str(run)))
    print('RUN_DIRECTORY=' + str(run), flush=True)
    return run


def workload(run, t):
    data = rows(run / 'w' / t['sweep'] / f'{t["transactions"]}.csv')
    for r in data:
        for key in ('src_shard', 'dst_shard', 'amount_wei', 'sender_idx', 'receiver_idx', 'arrival_pos', 'original_dst_shard'):
            r[key] = int(r[key])
    return data


def free_ports(preferred, count):
    for base in range(preferred, 45000 - count, count):
        probes = []
        try:
            for port in range(base, base + count):
                s = socket.socket()
                probes.append(s)
                if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
                    s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                s.bind(('127.0.0.1', port))
            return base
        except OSError:
            pass
        finally:
            for s in probes:
                s.close()
    raise RuntimeError('No free RPC range; no other process was stopped')


def worker(run, task_id, attempt, reuse=False):
    import yaml
    from dataclasses import replace
    m = read(run / 'manifest.json')
    check(run / 'backend', m['backend_hashes'])
    check(run / 'w', m['workload_hashes'])
    t = next(t for t in m['tasks'] if t['id'] == task_id)
    if t['policy'] != 'plain':
        raise ValueError('This experiment only runs Plain/TDR OFF')
    sys.path.insert(0, str(run / 'backend' / t['sweep']))
    from exp040_accounting import verify_and_account
    c = plain_config(read(run / 'reference' / t['sweep'] / 'task_configs.json')[t['source_task_id']], t, m)
    data = workload(run, t)
    if reuse:
        old = read(attempt / 'original_summary.json')
        compatible_plain(c, old['effective_config'])
        if old['workload_sha256'] != sha(run / 'w/b/40000.csv'):
            raise ValueError('Reuse raw input hash mismatch')
        c = old['effective_config']
        stats, resolved = old['stats'], old['resolved_params']
    else:
        from brokerlab.config import load_config, to_params_dict
        from brokerlab.procmon import RSSWatchdog
        from experiments.exp025_regime_shift_sensitivity import pipeline
        path = attempt / 'params.yaml'
        path.write_text(yaml.safe_dump(c, sort_keys=False), encoding='utf-8')
        cfg = load_config(path)
        cfg = replace(cfg, chain=replace(cfg.chain, base_port=free_ports(cfg.chain.base_port, cfg.chain.num_shards)))
        watchdog = RSSWatchdog(float(c['exp']['rss_cap_mb']))
        watchdog.start()
        try:
            stats = pipeline.run_arm(cfg, 'plain', data, sum(r['amount_wei'] for r in data), attempt, watchdog,
                engine='plain', fund_eth=t['balance_eth'], eps=c['exp']['tdr_epsilon'], chi=c['exp']['tdr_chi_blocks'],
                hl=c['exp']['tdr_ewma_half_life'], qmin=c['exp']['tdr_q_min'])
        finally:
            watchdog.stop()
        resolved = to_params_dict(cfg)
    raw, moves = rows(attempt / 'plain/ctx_rows.csv'), rows(attempt / 'plain/tdr_moves.csv')
    finals, curve = verify_and_account(c, t, stats, raw, moves, data)
    if moves or stats['tdr']['transfers_done'] or stats['tdr']['events_opened']:
        raise ValueError('TDR OFF unexpectedly rebalanced')
    if any(int(r['net_profit_wei']) != int(r['served']) * 2100000 or int(r['modeled_cost_wei']) for r in finals):
        raise ValueError('Plain fixed-fee profit mismatch')
    if reuse:
        old_rows = rows(attempt / 'per_broker.csv')
        if [{k: str(v) for k, v in r.items()} for r in finals] != old_rows:
            raise ValueError('Recomputed original Plain per-broker results differ')
    csv_write(attempt / 'per_broker.csv', finals)
    csv_write(attempt / 'revenue_curve.csv', curve)
    save(attempt / 'summary.json', dict(protocol=PROTOCOL, task_id=t['id'], source_task_id=t['source_task_id'],
        sweep=t['sweep'], origin=t['origin'], effective_config=c, resolved_params=resolved, stats=stats,
        workload_sha256=sha(run / 'w' / t['sweep'] / f'{t["transactions"]}.csv'),
        artifacts={n: sha(attempt / 'plain' / n) for n in ('ctx_rows.csv', 'tdr_moves.csv')},
        derived={n: sha(attempt / n) for n in ('per_broker.csv', 'revenue_curve.csv')}))
    print('TASK_VERIFIED=' + task_id, flush=True)


def validate_task(run, m, t):
    a = t['attempts'][-1]
    p = run / a['path']
    if a.get('summary_sha256') and sha(p / 'summary.json') != a['summary_sha256']:
        raise ValueError('Completed summary changed')
    d = read(p / 'summary.json')
    if d['task_id'] != t['id'] or d['protocol'] != PROTOCOL or d['sweep'] != t['sweep']:
        raise ValueError('Task identity mismatch')
    expected = plain_config(read(run / 'reference' / t['sweep'] / 'task_configs.json')[t['source_task_id']], t, m)
    if t['origin'] == 'new':
        if expected != d['effective_config']:
            raise ValueError('Completed task parameters differ')
    else:
        compatible_plain(expected, d['effective_config'])
    if d['workload_sha256'] != m['workload_hashes'][f'{t["sweep"]}/{t["transactions"]}.csv']:
        raise ValueError('Completed task input mismatch')
    check(p / 'plain', d['artifacts'])
    check(p, d['derived'])
    if d['stats']['tdr']['transfers_done'] or d['stats']['tdr']['events_opened']:
        raise ValueError('Plain task contains rebalancing')


def aggregate(run, m):
    data = []
    for t in m['tasks']:
        if t['status'] == 'complete':
            for r in rows(run / t['attempts'][-1]['path'] / 'per_broker.csv'):
                data.append(dict(task_id=t['id'], source_task_id=t['source_task_id'], sweep=t['sweep'], origin=t['origin'],
                    round=t['round'], route_seed=t['route_seed'], balance_eth=t['balance_eth'], transactions=t['transactions'], **r))
    csv_write(run / 'per_broker.csv', data)
    save(run / 'report.json', dict(protocol=PROTOCOL, smoke=m['smoke'], complete=all(t['status'] == 'complete' for t in m['tasks']),
        total=len(m['tasks']), complete_observations=sum(t['status'] == 'complete' for t in m['tasks']),
        new_complete=sum(t['status'] == 'complete' and t['origin'] == 'new' for t in m['tasks']),
        reused_complete=sum(t['status'] == 'complete' and t['origin'] != 'new' for t in m['tasks']),
        failed=[t['id'] for t in m['tasks'] if t['status'] == 'failed'], figure_status='Use code/plot.py to merge verified completed cells'))


def progress(run, m, state):
    now = datetime.now().astimezone()
    counts = Counter(t['status'] for t in m['tasks'])
    done_new = sum(t['status'] == 'complete' and t['origin'] == 'new' for t in m['tasks'])
    lines = ['# Fig5b/c：独立 TDR OFF 补跑状态', '', f'更新时间：{now:%Y-%m-%d %H:%M:%S %z}；每30秒更新。',
        f'状态：{state}；PID：{os.getpid()}；正式数据：{not m["smoke"]}',
        f'新增运行已完成：{done_new}/{m["plan"]["new_tasks"]}；另复用实验41：{m["plan"]["reused"]} 次。',
        f'失败：{counts["failed"]}；运行中：{counts["running"]}；待运行：{counts["pending"]}。', '',
        '| 扫描 | 全部观察值完成（含复用） | 新增完成 | 复用完成 |', '|---|---:|---:|---:|']
    samples = defaultdict(list)
    for t in m['tasks']:
        if t['status'] == 'complete' and t['origin'] == 'new':
            a = t['attempts'][-1]
            samples[(t['sweep'], t['transactions'])].append((datetime.fromisoformat(a['ended_at']) - datetime.fromisoformat(a['started_at'])).total_seconds())
    for s in ('c', 'b'):
        group = [t for t in m['tasks'] if t['sweep'] == s]
        lines.append(f'| Fig5{s} | {sum(t["status"] == "complete" for t in group)}/{len(group)} | {sum(t["status"] == "complete" and t["origin"] == "new" for t in group)} | {sum(t["status"] == "complete" and t["origin"] != "new" for t in group)} |')
    for t in m['tasks']:
        if t['status'] == 'running':
            lines += ['', f'当前任务：`{t["id"]}`；开始于 {t["attempts"][-1]["started_at"]}。']
    remaining = [t for t in m['tasks'] if t['status'] != 'complete']
    if remaining and all(samples[(t['sweep'], t['transactions'])] for t in remaining):
        seconds = sum(statistics.median(samples[(t['sweep'], t['transactions'])]) for t in remaining)
        for t in remaining:
            if t['status'] == 'running':
                seconds -= min(statistics.median(samples[(t['sweep'], t['transactions'])]), (datetime.now() - datetime.fromisoformat(t['attempts'][-1]['started_at'])).total_seconds())
        lines += ['', f'粗略剩余：{seconds/3600:.1f} 小时；预计 {(now+timedelta(seconds=seconds)):%m月%d日 %H:%M}（同后端、同交易量耗时估算，可能波动）。']
    else:
        lines += ['', '耗时估计：各交易量档位尚未全部获得本实验耗时样本，暂不提供精确完成时间。' if remaining else '所有 TDR OFF 观察值已完成。']
    lines += ['', f'独占锁：{m["lock_port"]}；RPC起始端口：{m["base_port"]}（自动选择空闲连续段）。',
        '两套历史运行时独立冻结；只串行执行一个新增任务，不修改原实验源码、输入或结果。',
        '利润：broker成功服务用户CTX数 × 0.0021 Gwei；内部转移次数、成本必须为0。Broker19汇总全部子账户。',
        '暂停方式：在本结果目录创建 STOP_AFTER_TASK 空文件；当前任务验证完成后停止。',
        '恢复方式：python code/run.py --resume "本结果目录" --background', '', f'结果目录：`{run}`']
    p = run / '运行状态.md'
    tmp = p.with_suffix('.md.tmp')
    tmp.write_text('\n'.join(lines) + '\n', encoding='utf-8-sig')
    tmp.replace(p)


def execute(run, max_tasks=None):
    m = read(run / 'manifest.json')
    if m['protocol'] != PROTOCOL:
        raise ValueError('Different experiment protocol')
    lock = socket.socket()
    if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
        lock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    lock.bind(('127.0.0.1', m['lock_port']))
    with lock:
        check(run / 'backend', m['backend_hashes'])
        check(run / 'code', m['code_hashes'])
        check(run / 'reference', m['reference_hashes'])
        check(run / 'w', m['workload_hashes'])
        for t in m['tasks']:
            if t['status'] == 'complete':
                validate_task(run, m, t)
        halt = threading.Event()
        state = {'value': '运行中'}
        def reporter():
            while not halt.is_set():
                try:
                    progress(run, copy.deepcopy(m), state['value'])
                except Exception as e:
                    print('STATUS_REPORT_ERROR=' + repr(e), flush=True)
                halt.wait(30)
        thread = threading.Thread(target=reporter, daemon=True)
        thread.start()
        executed = 0
        try:
            for i, t in enumerate(m['tasks']):
                if t['status'] == 'complete':
                    continue
                if (run / 'STOP_AFTER_TASK').exists() or (max_tasks is not None and executed >= max_tasks):
                    state['value'] = '按要求完成当前任务后停止，可续跑'
                    break
                check(run / 'backend', m['backend_hashes'])
                check(run / 'code', m['code_hashes'])
                path = Path('t') / f'{i:04d}' / f'a{len(t["attempts"])+1:03d}'
                attempt = run / path
                attempt.mkdir(parents=True)
                a = dict(path=path.as_posix(), started_at=datetime.now().isoformat(), status='running')
                t['attempts'].append(a)
                t['status'] = 'running'
                save(run / 'manifest.json', m)
                print(f'[{i+1}/{len(m["tasks"])}] {t["id"]}', flush=True)
                try:
                    flags = (subprocess.CREATE_NO_WINDOW | subprocess.BELOW_NORMAL_PRIORITY_CLASS) if os.name == 'nt' else 0
                    with (attempt / 'stdout.log').open('w', encoding='utf-8') as log:
                        result = subprocess.run([sys.executable, '-u', str(run / 'code/run.py'), '--worker', str(run), t['id'], str(attempt)], stdout=log, stderr=subprocess.STDOUT, env=dict(os.environ, PYTHONIOENCODING='utf-8'), creationflags=flags)
                    if result.returncode:
                        raise RuntimeError(f'Worker exit {result.returncode}; see {attempt / "stdout.log"}')
                    check(run / 'backend', m['backend_hashes'])
                    validate_task(run, m, t)
                    a['summary_sha256'] = sha(attempt / 'summary.json')
                    t['status'] = a['status'] = 'complete'
                    executed += 1
                except BaseException as e:
                    t['status'] = a['status'] = 'failed'
                    a['error'] = repr(e)
                    raise
                finally:
                    a['ended_at'] = datetime.now().isoformat()
                    save(run / 'manifest.json', m)
                    aggregate(run, m)
            if all(t['status'] == 'complete' for t in m['tasks']):
                state['value'] = '全部完成'
        except BaseException as e:
            state['value'] = '失败停止：' + repr(e)
            raise
        finally:
            halt.set()
            thread.join()
            progress(run, m, state['value'])
    return 0


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--fig5c-base', type=Path, default=DEFAULT_C)
    p.add_argument('--fig5b-base', type=Path, default=DEFAULT_B)
    p.add_argument('--reuse-plain', type=Path, default=DEFAULT_REUSE)
    p.add_argument('--fig5c-sota', type=Path, default=Path('C:/Users/hzy/Downloads/out/exp040_sota_supplement/20261022_084455_055681'))
    p.add_argument('--fig5b-sota', type=Path, default=PROJECT / 'experiments/exp044_fig5b_baseline_supplement/out/matched_transactions')
    p.add_argument('--output-root', type=Path, default=DEFAULT_OUTPUT)
    p.add_argument('--only', choices=['both', 'b', 'c'], default='both')
    p.add_argument('--no-reuse', action='store_true')
    p.add_argument('--smoke', action='store_true')
    p.add_argument('--dry-run', action='store_true')
    p.add_argument('--prepare-only', action='store_true')
    p.add_argument('--resume', type=Path)
    p.add_argument('--background', action='store_true')
    p.add_argument('--max-tasks', type=int)
    p.add_argument('--base-port', type=int, default=38600)
    p.add_argument('--lock-port', type=int, default=45948)
    p.add_argument('--anvil-bin')
    p.add_argument('--worker', nargs=3, help=argparse.SUPPRESS)
    p.add_argument('--verify-reuse', nargs=3, help=argparse.SUPPRESS)
    a = p.parse_args()
    if a.worker or a.verify_reuse:
        item = a.worker or a.verify_reuse
        worker(Path(item[0]), item[1], Path(item[2]), reuse=bool(a.verify_reuse))
        return 0
    if a.max_tasks is not None and a.max_tasks <= 0:
        p.error('--max-tasks must be positive')
    run = a.resume.resolve() if a.resume else prepare(a)
    if run is None or a.prepare_only:
        return 0
    if a.background:
        cmd = [sys.executable, '-u', str(run / 'code/run.py'), '--resume', str(run)]
        if a.max_tasks:
            cmd += ['--max-tasks', str(a.max_tasks)]
        flags = (subprocess.CREATE_NO_WINDOW | subprocess.BELOW_NORMAL_PRIORITY_CLASS) if os.name == 'nt' else 0
        with (run / 'runner.log').open('a', encoding='utf-8') as log:
            proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, env=dict(os.environ, PYTHONIOENCODING='utf-8'), creationflags=flags)
        save(run / 'launch.json', dict(pid=proc.pid, command=cmd, started_at=datetime.now().isoformat()))
        print(json.dumps(dict(pid=proc.pid, run=str(run)), indent=2), flush=True)
        return 0
    return execute(run, a.max_tasks)


if __name__ == '__main__':
    raise SystemExit(main())
