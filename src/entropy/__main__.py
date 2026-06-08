from __future__ import annotations

from entropy.app import AppConfig
from entropy.ui.app import EntropyApp


def main() -> None:
    EntropyApp(AppConfig()).run()


if __name__ == "__main__":
    main()
