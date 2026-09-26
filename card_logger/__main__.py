import argparse

from .db import DEFAULT_DB_PATH


def main() -> None:
    parser = argparse.ArgumentParser(description="Log and browse your card collection.")
    parser.add_argument(
        "--db", default=str(DEFAULT_DB_PATH), help=f"database file (default: {DEFAULT_DB_PATH})"
    )
    args = parser.parse_args()

    from .gui import main as run_gui

    run_gui(args.db)


if __name__ == "__main__":
    main()
