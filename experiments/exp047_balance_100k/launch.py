"""Run isolated 4-arm smoke validation, then the new 280-task formal experiment."""
from pathlib import Path
import subprocess,sys,json
HERE=Path(__file__).resolve().parent
if __name__=='__main__':
    for smoke in (True,False):
        out=HERE/'out'/('smoke_2000' if smoke else 'formal_100k')
        cmd=[sys.executable,'-u',str(HERE/'run.py')]
        if out.exists():cmd+=['--resume',str(out)]
        elif smoke:cmd+=['--smoke']
        print('PHASE='+('SMOKE' if smoke else 'FORMAL_100K'),flush=True)
        result=subprocess.run(cmd)
        if result.returncode:raise SystemExit(result.returncode)
        report=json.loads((out/'report.json').read_text())
        if not report['complete']:raise SystemExit('Plan stopped; formal start withheld')
