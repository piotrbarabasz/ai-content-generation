"""D002 one-shot echo child, deliberately not the production job protocol."""

import json
import os
import sys


def main() -> int:
    try:
        request = json.loads(sys.stdin.readline(4096))
        if not isinstance(request, dict) or request.get("op") != "ping":
            raise ValueError("Expected a ping object.")
        value = request.get("value")
        if not isinstance(value, str) or len(value) > 256:
            raise ValueError("Ping value must be a short string.")
        print(json.dumps({"ok": True, "echo": value, "pid": os.getpid()}), flush=True)
        return 0
    except (ValueError, TypeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}), flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
