import subprocess
import sys


def run_black(target_path: str) -> None:
    subprocess.run(["black", target_path], check=True)


def main() -> None:
    target_path = sys.argv[1] if len(sys.argv) > 1 else ""
    if target_path:
        run_black(target_path)


if __name__ == "__main__":
    main()
