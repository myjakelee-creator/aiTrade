import sys

from stockboard_ranking_runtime_patch import install_stockboard_ranking_engine_patch

install_stockboard_ranking_engine_patch()

from stockboard_server import main


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1)
