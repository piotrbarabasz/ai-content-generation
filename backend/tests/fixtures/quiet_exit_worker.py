"""Exercise production serve() with slow interpreter teardown, without CUDA."""
import atexit
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from app.runtime.worker import serve

atexit.register(lambda: time.sleep(.4))
raise SystemExit(serve(quiet_handler=True, initialize=lambda: None))
