#!/usr/bin/env python3
"""Preserve rustc's actual link inputs before its temporary files disappear."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


def main():
    root = Path(os.environ["SCARLET_RUST_LINK_CAPTURE"])
    root.mkdir()
    original = sys.argv[1:]
    portable = []
    arguments = iter(original)
    for argument in arguments:
        if argument == "-o":
            next(arguments)
            continue
        if argument == "-L":
            # All rlibs are passed as explicit files; raw-dylibs is empty on
            # this target. Do not retain the build machine's search paths.
            directory = Path(next(arguments))
            if directory.is_dir() and any(directory.iterdir()):
                raise ValueError(f"unexpected nonempty library search directory: {directory}")
            continue
        if argument.startswith("-l") or argument.startswith("@"):
            raise ValueError(f"unhandled indirect input: {argument}")
        path = Path(argument)
        if not argument.startswith("-") and path.is_file():
            name = f"{len(portable):02d}-{path.name}"
            shutil.copy2(path, root / name)
            portable.append(name)
        elif path.is_absolute():
            raise ValueError(f"unhandled absolute path: {argument}")
        else:
            portable.append(argument)
    if any(any(char in argument for char in "\r\n\0") for argument in portable):
        raise ValueError("link argument cannot be represented on one line")
    (root / "link.args").write_text("\n".join(portable) + "\n")
    (root.parent / "rust-link-capture.json").write_text(json.dumps({"original": original, "portable": portable}, indent=2) + "\n")
    return subprocess.call([os.environ["SCARLET_HOST_WILD"], *original, "--threads=1"])


if __name__ == "__main__":
    sys.exit(main())
