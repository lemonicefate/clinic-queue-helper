"""Command-line entry point for the Windows startup script."""

import sys

from .config import ConfigurationError, load_config


def main() -> int:
    try:
        config = load_config()
    except ConfigurationError as exc:
        print(f"Clinic Queue Helper startup error: {exc}", file=sys.stderr)
        return 2

    from .startup import start_server

    return start_server(config)


if __name__ == "__main__":
    raise SystemExit(main())
