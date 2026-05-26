import argparse
import os
import socket
import subprocess
import sys


DEFAULT_START_PORT = 18514
DEFAULT_END_PORT = 18650


def is_port_open(port, host="127.0.0.1", timeout=0.25):
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def find_free_port(start_port, end_port):
    for port in range(start_port, end_port + 1):
        if not is_port_open(port):
            return port
    raise RuntimeError(f"No free port found in {start_port}-{end_port}")


def main():
    parser = argparse.ArgumentParser(description="Start a new isolated GenericAgent stapp instance.")
    parser.add_argument("--start-port", type=int, default=DEFAULT_START_PORT)
    parser.add_argument("--end-port", type=int, default=DEFAULT_END_PORT)
    args = parser.parse_args()

    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    launch_path = os.path.join(project_root, "launch.pyw")
    port = find_free_port(args.start_port, args.end_port)

    print(f"[GA Isolated] Starting new isolated stapp instance on port {port}")
    print(f"[GA Isolated] URL: http://localhost:{port}")
    return subprocess.call([sys.executable, launch_path, str(port)])


if __name__ == "__main__":
    raise SystemExit(main())
