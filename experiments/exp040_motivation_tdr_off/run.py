"""Independent Fig1 TDR-off experiment; reuse the verified chain execution layer."""
from pathlib import Path
import sys

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE.parent/'exp041_tdr_on_sensitivity'))
from run import main

if __name__=='__main__':
    raise SystemExit(main(home=HERE))
