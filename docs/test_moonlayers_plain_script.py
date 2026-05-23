from __future__ import annotations

from pathlib import Path

import moonlayers
from moonlayers import MoonMap


def main() -> None:
    print("moonlayers module:", moonlayers.__file__)
    print("MoonMap class:", MoonMap.__module__ + "." + MoonMap.__name__)
    print("cwd:", Path.cwd())


if __name__ == "__main__":
    main()
