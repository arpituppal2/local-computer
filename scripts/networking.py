"""Local networking helpers for the Locus dashboard."""
from __future__ import annotations

import argparse
import socket


def port_is_available(host: str, port: int) -> bool:
    """Return True when host:port can be bound by the current process."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, int(port)))
        except OSError:
            return False
    return True


def find_available_port(host: str = "127.0.0.1", preferred: int = 8765, attempts: int = 80) -> int:
    """Find a local dashboard port without disturbing unrelated processes."""
    preferred = max(1024, min(int(preferred), 65535))
    attempts = max(1, min(int(attempts), 200))
    for offset in range(attempts):
        port = preferred + offset
        if port > 65535:
            break
        if port_is_available(host, port):
            return port
    raise RuntimeError(f"no free localhost port found from {preferred} within {attempts} attempts")


def main() -> None:
    parser = argparse.ArgumentParser(description="Find a free local dashboard port")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--preferred", type=int, default=8765)
    parser.add_argument("--attempts", type=int, default=80)
    args = parser.parse_args()
    print(find_available_port(args.host, args.preferred, args.attempts))


if __name__ == "__main__":
    main()
