import argparse
import os

import uvicorn


def main() -> None:
    if not os.environ.get("FLOWLENS_TOKEN"):
        raise SystemExit("FLOWLENS_TOKEN is required; start this service through the FlowLens extension.")
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    uvicorn.run("flowlens.api:app", host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
