#!/usr/bin/env python3
"""Link and run a tiny build-host LLVM client before bootstrapping rustc."""
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile


def main():
    llvm_config = os.environ["SCARLET_LLVM_CONFIG"]
    version = subprocess.check_output([llvm_config, "--version"], text=True).strip()
    # rustc_llvm defaults to static LLVM and asks llvm-config for system libs.
    # A successful llvm-config invocation alone does not prove -lxml2 etc. can
    # actually be found by the build-host compiler's linker.
    flags = shlex.split(subprocess.check_output(
        [llvm_config, "--link-static", "--ldflags", "--libs", "--system-libs"], text=True))
    compiler = shlex.split(os.environ.get("CXX", "c++"))
    with tempfile.TemporaryDirectory(prefix="scarlet-host-llvm-") as tmp:
        source = Path(tmp) / "version.cpp"
        binary = Path(tmp) / "version"
        source.write_text('''#include <cstdio>
extern "C" void LLVMGetVersion(unsigned *, unsigned *, unsigned *);
int main() {
    unsigned major, minor, patch;
    LLVMGetVersion(&major, &minor, &patch);
    std::printf("%u.%u.%u\\n", major, minor, patch);
}
''')
        command = [*compiler, str(source), "-o", str(binary), *flags]
        print("Checking build-host LLVM linkage: " + shlex.join(command), flush=True)
        subprocess.run(command, check=True)
        actual = subprocess.check_output([str(binary)], text=True).strip()
    expected = re.match(r"\d+\.\d+\.\d+", version)
    if expected is None or actual != expected.group():
        raise SystemExit(f"LLVM version mismatch: llvm-config={version}, linked={actual}")
    print(f"Build-host LLVM {actual} link and execution check passed.")


if __name__ == "__main__":
    main()
