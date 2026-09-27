import argparse

from .db import DEFAULT_DB_PATH


def main() -> None:
    parser = argparse.ArgumentParser(description="Log and browse your card collection.")
    parser.add_argument(
        "--db", default=str(DEFAULT_DB_PATH), help=f"database file (default: {DEFAULT_DB_PATH})"
    )
    parser.add_argument(
        "--check", action="store_true", help="test every live data source and save a report, then exit"
    )
    args = parser.parse_args()

    if args.check:
        from .selfcheck import run

        raise SystemExit(0 if run() else 1)

    from .gui import main as run_gui

    run_gui(args.db)


if __name__ == "__main__":
    main()
