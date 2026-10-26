"""Run only the independent exp043 protocol; never resumes older experiments."""
from pathlib import Path
import sys
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE.parents[1]))
from runner import main
if __name__=="__main__":raise SystemExit(main(HERE,"exp043"))
