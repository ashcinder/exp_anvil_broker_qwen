"""Create two hash-verified historical runtimes without writing to live sources."""
from pathlib import Path
import ast
import hashlib
import json

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[1]
OLD = PROJECT / 'experiments/exp040_liquidity_scale/sota_supplement'
DROOT = Path('D:/BaiduNetdiskDownload/exp_anvil_broker_qwen/exp_anvil_broker_qwen')
CBASE = Path('C:/Users/hzy/Downloads/out/out/20261016_003612_099146')
BBASE = DROOT / 'experiments/exp040_liquidity_scale/out/20261016_080212_748363'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def source_map(manifest):
    result = {}
    for name, digest in manifest['sources'].items():
        parts = name.replace('\\', '/').split('/')
        index = next((i for i, p in enumerate(parts) if p in ('brokerlab', 'experiments')), None)
        if index is not None:
            result['/'.join(parts[index:])] = digest
    return result


def freeze():
    # Undo the three baseline-only patches, and require byte-for-byte original hashes.
    # No Git checkout or live runtime is changed.
    tree = ast.parse((OLD / 'freeze_backend.py').read_text(encoding='utf-8'))
    reverse, target = {}, None
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'freeze')
    for n in function.body:
        if not isinstance(n, ast.Assign):
            continue
        names = [x.id for x in n.targets if isinstance(x, ast.Name)]
        if names == ['p'] and isinstance(n.value, ast.BinOp):
            target = ast.literal_eval(n.value.right)
        if names == ['s'] and isinstance(n.value, ast.Call) and isinstance(n.value.func, ast.Name) and n.value.func.id == 'once':
            reverse.setdefault(target, []).append(tuple(ast.literal_eval(a) for a in n.value.args[1:]))
    provenance = json.loads((OLD / 'runtime/provenance.json').read_text(encoding='utf-8'))
    for sweep, base in [('c', CBASE), ('b', BBASE)]:
        expected = source_map(json.loads((base / 'manifest.json').read_text(encoding='utf-8-sig')))
        names = list(provenance['original_hashes']) if sweep == 'c' else [n for n in expected if n.startswith('brokerlab/') and n.count('/') == 1 and n.endswith('.py')]
        if sweep == 'b':
            names += ['experiments/exp025_regime_shift_sensitivity/pipeline.py', 'experiments/exp040_liquidity_scale/design.py']
        dest = HERE / 'runtime' / sweep
        if (dest / 'provenance.json').exists():
            raise FileExistsError(dest)
        hashes = {}
        for name in names:
            rel = 'exp040_accounting.py' if name.endswith('/design.py') else name
            data = ((OLD / 'runtime' / rel) if sweep == 'c' else (DROOT / name)).read_bytes()
            if sweep == 'c' and name in reverse:
                source = data.decode('utf-8')
                for old, new in reversed(reverse[name]):
                    if not new:
                        anchor = '            if self._tracks_inflow and src_shard is not None:'
                        if source.count(anchor) != 1:
                            raise ValueError('Cannot restore deleted observe block')
                        source = source.replace(anchor, old + anchor, 1)
                        continue
                    if source.count(new) != 1:
                        raise ValueError('Cannot reverse saved patch: ' + name)
                    source = source.replace(new, old, 1)
                data = source.encode('utf-8')
            if sha(data) != expected[name]:
                raise ValueError('Historical source hash mismatch: ' + name)
            p = dest / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
            hashes[rel] = sha(data)
        for rel in ('experiments/__init__.py', 'experiments/exp025_regime_shift_sensitivity/__init__.py'):
            (dest / rel).write_bytes(b'')
            hashes[rel] = sha(b'')
        (dest / 'provenance.json').write_text(json.dumps(dict(base_run=str(base), original_files={n: expected[n] for n in names}, hashes=hashes, changes='Only empty namespace __init__.py files added; all execution sources match the corresponding original run'), indent=2), encoding='utf-8')
        print(sweep, len(names), 'original sources verified')


if __name__ == '__main__':
    freeze()
