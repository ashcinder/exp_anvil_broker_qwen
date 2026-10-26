"""Create a private backend copy; never edit the source backend."""
import shutil
from pathlib import Path
from design import sha,save

def once(text,old,new):
    if text.count(old)!=1:raise RuntimeError('Unsupported backend logging anchor: '+old[:100])
    return text.replace(old,new)

def stage(root,destination,home):
    original={}
    for package in ['brokerlab','experiments']:
        for f in (root/package).rglob('*.py'):
            rel=f.relative_to(root)
            # Skip old result snapshots and generated backend copies.
            if any(v in {'out','output','results','__pycache__','.git'} for v in rel.parts):continue
            if package=='experiments' and len(rel.parts)>3:continue
            target=destination/rel;target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(f,target);original[str(rel)]=sha(f)
    shutil.copy2(home/'accounting_logger.py',destination/'brokerlab/exp042_accounting.py')
    p=destination/'experiments/exp025_regime_shift_sensitivity/pipeline.py';s=p.read_text(encoding='utf-8')
    s=once(s,'        ctx_qs = [ctxm.Queue() for _ in range(nb)]','        exp042_counter = ctxm.Value("q", 0)\n        ctx_qs = [ctxm.Queue() for _ in range(nb)]')
    s=once(s,'            if my_tdr:                        #','            payload["exp042_counter"] = exp042_counter\n            payload["exp042_directory"] = str(arm_dir / "accounting")\n            payload["exp042_fee"] = int(cfg.exp["profit_tdr_cost_wei"])\n            if my_tdr:                        #')
    s=once(s,'        if payload.get("relay_probe"):','        from brokerlab.exp042_accounting import install\n        install(eng, payload["exp042_counter"], payload["exp042_directory"], b, payload["exp042_fee"])\n        if payload.get("relay_probe"):')
    p.write_text(s,encoding='utf-8')
    p=destination/'brokerlab/tdr_hook.py';s=p.read_text(encoding='utf-8')
    s=once(s,'                    tr["first_confirmed"] = True','                    tr["first_confirmed"] = True\n                    self._exp042_record_cost(tr)')
    p.write_text(s,encoding='utf-8')
    frozen={str(f.relative_to(destination)):sha(f) for f in destination.rglob('*.py')}
    save(destination/'snapshot.json',dict(source=str(root),original_hashes=original,patched_hashes=frozen,logging_only=True))
    return frozen
