"""Plot Fig1 TDR-off stacked counts with real repeated-run SD caps."""
from pathlib import Path
import argparse
import sys

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE.parent/'exp041_tdr_on_sensitivity'))
from analyze import analyze

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--allow-partial',action='store_true')
    args=parser.parse_args()
    analyze(args.source,args.allow_partial)
