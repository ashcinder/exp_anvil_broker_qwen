"""Build the vendored runtime once; experiment users do not need Git or D: access."""
from pathlib import Path
import argparse
import hashlib
import json
import subprocess

HERE = Path(__file__).resolve().parent
COMMIT = '3e1dd1e2c0de6cc49f05c7fc6c57d23156fa91e6'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def once(s, old, new):
    if s.count(old) != 1:
        raise ValueError('Unsupported historical source anchor: ' + old[:100])
    return s.replace(old, new)


def freeze(repo, base):
    dest = HERE / 'runtime'
    if dest.exists():
        raise FileExistsError(dest)
    manifest = json.loads((base / 'manifest.json').read_text(encoding='utf-8-sig'))
    expected = {}
    for name, digest in manifest['sources'].items():
        parts = name.replace('\\', '/').split('/')
        i = next((i for i, x in enumerate(parts) if x in ('brokerlab', 'experiments')), None)
        if i is not None:
            expected['/'.join(parts[i:])] = digest
    names = subprocess.check_output(['git', '-C', str(repo), 'ls-tree', '-r', '--name-only', COMMIT]).decode().splitlines()
    wanted = [n for n in names if n.startswith('brokerlab/') and n.endswith('.py') and n.count('/') == 1]
    wanted += ['experiments/exp025_regime_shift_sensitivity/pipeline.py', 'experiments/exp040_liquidity_scale/design.py']
    originals = {}
    for name in wanted:
        data = subprocess.check_output(['git', '-C', str(repo), 'show', COMMIT + ':' + name])
        if sha(data) != expected[name]:
            raise ValueError('Runtime does not match completed Exp040: ' + name)
        target = dest / ('exp040_accounting.py' if name.endswith('/design.py') else name)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        originals[name] = sha(data)
    # The native AutoLoop rule is exactly the one used by the existing Exp043 baseline.
    native = repo / 'brokerlab/sota_native.py'
    (dest / 'brokerlab/sota_native.py').write_bytes(native.read_bytes())
    p = dest / 'brokerlab/tdr_engine.py'
    s = p.read_text(encoding='utf-8')
    s = once(s, 'class TdrAgent:', 'from .sota_native import autoloop_plans\n\nclass TdrAgent:')
    s = once(s, 'overpass_theta: str = "uniform") -> None:', 'overpass_theta: str = "uniform",\n                 sota_capacity_wei: int = 0, sota_check_blocks: int = 10) -> None:')
    s = once(s, '        self.overpass_theta = str(overpass_theta)', '        self.overpass_theta = str(overpass_theta)\n        self.sota_capacity_wei = int(sota_capacity_wei or 2 * valve_init_wei)\n        self.sota_check_blocks = int(sota_check_blocks)\n        self._last_policy_check = None')
    s = once(s, '            if self._tracks_flow:\n                self._flowed_wei += int(value)\n', '')
    s = once(s, '    def notify_shortfall(', '    def record_success(self, value, block=0, dst_shard=None, src_shard=None):\n        if self.policy == self.POLICY_SPIDER and value > 0:\n            self._flowed_wei += int(value)\n\n    def notify_shortfall(')
    s = once(s, '        av = [int(a) for a in available]', '        av = [int(a) for a in available]\n        if self.policy == self.POLICY_AUTOLOOP:\n            if self._last_policy_check is not None and block < self._last_policy_check + self.sota_check_blocks:\n                return []\n            self._last_policy_check = block')
    s = once(s, '/ 2 * self.valve_init_wei)] * self.num_shards\n            plans = autoloop_plans(av, self.valve_init_wei,', '/ 2 * self.sota_capacity_wei)] * self.num_shards\n            plans = autoloop_plans(av, self.sota_capacity_wei,')
    p.write_text(s, encoding='utf-8', newline='\n')
    p = dest / 'brokerlab/tdr_hook.py'
    s = p.read_text(encoding='utf-8')
    s = once(s, '    def tick_once(self) -> None:', '    def _emit(self, row):\n        if self.tdr is not None and row["route"] == "broker" and row["ok"]:\n            self.tdr.record_success(int(row["amount_wei"]), int(row["submit_block"]), int(row["dst"]), int(row["src"]))\n        super()._emit(row)\n\n    def tick_once(self) -> None:')
    s = once(s, 'overpass_theta=str(tp.get("overpass_theta", "uniform")))', 'overpass_theta=str(tp.get("overpass_theta", "uniform")),\n                     sota_capacity_wei=int(tp.get("sota_capacity_wei", 0)),\n                     sota_check_blocks=int(tp.get("sota_check_blocks", 10)))')
    p.write_text(s, encoding='utf-8', newline='\n')
    p = dest / 'experiments/exp025_regime_shift_sensitivity/pipeline.py'
    s = p.read_text(encoding='utf-8')
    s = once(s, '            tdr_params.update(extra_pol)', '            if engine in ("autoloop", "spider"):\n                tdr_params.update(chi_blocks=0, sota_capacity_wei=int(fund_eth * ETH * cfg.exp["sota_capacity_mult"]), sota_check_blocks=int(cfg.exp["sota_check_blocks"]))\n            tdr_params.update(extra_pol)')
    s = once(s, 'dict(my_tdr, offset_blocks=b % 5)', 'dict(my_tdr, offset_blocks=0 if engine in ("autoloop", "spider") else b % 5)')
    p.write_text(s, encoding='utf-8', newline='\n')
    # Namespace packages must win over the user's live working-tree experiments.
    for rel in ('experiments/__init__.py', 'experiments/exp025_regime_shift_sensitivity/__init__.py'):
        (dest / rel).write_text('', encoding='utf-8')
    record = dict(commit=COMMIT, original_hashes=originals,
        native_policy_source=str(native), native_policy_sha256=sha(native.read_bytes()),
        changes=['AutoLoop: native per-account band requests, C=2B, check every 10 blocks',
                 'Spider: count successful user principal only, not submitted/relay demand',
                 'Baseline-only chi=0 and startup offset=0; same as Exp043',
                 'Routing coordinator, broker engine, funding, ledger, TxService unchanged from original Exp040'],
        hashes={p.relative_to(dest).as_posix(): sha(p.read_bytes()) for p in dest.rglob('*.py')})
    (dest / 'provenance.json').write_text(json.dumps(record, indent=2), encoding='utf-8')
    print(json.dumps(dict(runtime=str(dest), verified_original_files=len(originals), patched_files=3), indent=2))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repo', type=Path, required=True)
    p.add_argument('--base-run', type=Path, required=True)
    a = p.parse_args()
    freeze(a.repo.resolve(), a.base_run.resolve())
