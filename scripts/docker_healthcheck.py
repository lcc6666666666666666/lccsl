"""Read-only container probes; never print credentials or response contents."""
import json
from pathlib import Path
import sys
from urllib.request import Request, urlopen


def main() -> int:
    try:
        role = sys.argv[1]
        endpoints = {"api": "http://127.0.0.1:8000/api/v1/health",
                     "catalog": "http://127.0.0.1:8001/catalog",
                     "billing": "http://127.0.0.1:8002/openapi.json"}
        request = Request(endpoints[role])
        if role == "catalog":
            key = Path("/run/secrets/integration_key").read_text(encoding="utf-8").strip()
            request.add_header("X-Integration-Key", key)
        with urlopen(request, timeout=5) as response:
            if response.status != 200 or not isinstance(json.load(response), dict):
                return 1
        return 0
    except Exception:
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
