import os
from pathlib import Path
import sys
import tempfile

os.environ['PANEL_DATA'] = tempfile.mkdtemp(prefix='servercp-tests-')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
