"""Generate the public monochrome app mark."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from build_assets import prepare_assets
if __name__ == "__main__":
    root=Path(__file__).resolve().parent
    prepare_assets(root,root,"public")
