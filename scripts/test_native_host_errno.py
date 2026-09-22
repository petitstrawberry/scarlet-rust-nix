"""Check the committed native TLS overlay; no Rust checkout or guest is needed.

The host test compiles production helpers with only architectural TLS/syscalls
stubbed. Aborts become a typed panic so invalid headers can be tested in process.
This checks the shared-slot contract, not native scheduling or signal safety.
"""
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest


PATCH = Path(__file__).resolve().parents[1] / "native-host/patches/rust-native-host.patch"
STD = "library/std/src/sys/"


def postimages(patch):
    """Read actual added/context lines, retaining gaps instead of inventing code."""
    files = {}
    current = None
    line_number = None
    for line in patch.splitlines():
        if line.startswith("diff --git "):
            current = None
            line_number = None
        elif line.startswith("+++ b/"):
            current = files.setdefault(line[6:], {})
        elif line.startswith("@@ "):
            line_number = int(re.search(r"\+(\d+)", line).group(1))
        elif current is not None and line_number is not None and (not line or line[:1] in ("+", " ")):
            # Git also accepts empty context lines without their leading space.
            current[line_number] = line[1:] if line else ""
            line_number += 1
    result = {}
    for name, lines in files.items():
        output = []
        previous = 0
        for number, line in sorted(lines.items()):
            if number != previous + 1:
                output.append("/* omitted patch context */")
            output.append(line)
            previous = number
        result[name] = "\n".join(output)
    return result


def item(source, signature):
    start = source.index(signature)
    opening = source.index("{", start)
    depth = 1
    end = opening + 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    result = source[start:end]
    if "/* omitted patch context */" in result:
        raise AssertionError(f"retain complete patch context for {signature}")
    return result


class NativeErrnoAbiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        patch = PATCH.read_bytes()
        cls.patch_sha256 = hashlib.sha256(patch).hexdigest()
        cls.files = postimages(patch.decode())
        cls.tls = cls.files[STD + "thread_local/key/scarlet.rs"]
        cls.thread = cls.files[STD + "thread/scarlet.rs"]
        cls.header = cls.files["library/scarlet-abi/src/tls.rs"]

    def test_production_helpers_share_preserve_and_isolate_errno(self):
        compiler = shutil.which(os.environ.get("RUSTC", "rustc"))
        self.assertIsNotNone(compiler, "host Rust compiler required: set RUSTC to an executable "
                             "host rustc path or provide rustc on PATH")
        print(f"Native errno overlay: {PATCH}\nSHA-256: {self.patch_sha256}", flush=True)
        helpers = "\n".join(item(self.tls, signature) for signature in (
            "pub(crate) fn ensure_native_tls(", "pub(crate) fn native_errno_location(",
            "fn checked_native_tls(", "fn main_tls_base("))
        helpers += "\n" + item(self.thread, "fn allocate_thread_tls(")
        helpers += "\n" + item(self.files[STD + "pal/scarlet/os.rs"], "pub fn errno(")
        # Same failure branch, observable without aborting the whole Rust test process.
        helpers = helpers.replace("core::intrinsics::abort()", "host_abort()")
        production = "mod tls {\n" + self.header + "\n}\n" + HOST + helpers + REGRESSION
        with tempfile.TemporaryDirectory(prefix="scarlet-errno-regression-") as tmp:
            source = Path(tmp) / "errno.rs"
            binary = Path(tmp) / "errno-tests"
            source.write_text(production)
            build = subprocess.run([compiler, "--edition=2024", "--test", str(source),
                                    "-o", str(binary)], text=True, capture_output=True)
            self.assertEqual(build.returncode, 0, build.stdout + build.stderr)
            run = subprocess.run([str(binary)], text=True, capture_output=True)
            print(run.stdout, end="", flush=True)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)


HOST = r'''
use std::{cell::{Cell, RefCell}, io, mem, ptr};
use tls::{NativeTlsHeader, NATIVE_TLS_MAGIC, NATIVE_TLS_MAPPING_SIZE as TLS_MAPPING_SIZE};
thread_local! {
    static TP: Cell<usize> = const { Cell::new(0) };
    static MAPS: RefCell<Vec<Box<[usize]>>> = const { RefCell::new(Vec::new()) };
    static ALLOW_MAP: Cell<bool> = const { Cell::new(true) };
}
#[derive(Debug)]
struct InvalidTls;
fn host_abort() -> ! { std::panic::panic_any(InvalidTls) }
fn arch_tls_pointer() -> usize { TP.get() }
mod abi {
    pub mod mmap {
        pub const PROT_READ: usize = 1;
        pub const PROT_WRITE: usize = 2;
        pub const MAP_PRIVATE: usize = 4;
        pub const MAP_ANONYMOUS: usize = 8;
    }
    pub fn memory_map(_: usize, _: usize, size: usize, _: usize, _: usize, _: usize)
        -> Result<usize, ()> {
        assert!(super::ALLOW_MAP.get(), "errno accessor attempted lazy allocation");
        let mut mapping = vec![usize::MAX; size / size_of::<usize>()].into_boxed_slice();
        let base = mapping.as_mut_ptr() as usize;
        super::MAPS.with_borrow_mut(|maps| maps.push(mapping));
        Ok(base)
    }
    pub fn memory_unmap(base: usize, _: usize) -> Result<(), ()> {
        super::MAPS.with_borrow_mut(|maps| maps.retain(|m| m.as_ptr() as usize != base));
        Ok(())
    }
}
mod scarlet_sys {
    pub enum Syscall { SetTls }
    pub fn syscall1(_: Syscall, base: usize) -> isize {
        let header = unsafe { &*(base as *const super::NativeTlsHeader) };
        assert_eq!(header.magic, super::NATIVE_TLS_MAGIC, "published before initialization");
        assert_eq!(header.errno, 0);
        assert_eq!(header.namespace_head, 0);
        super::TP.set(base);
        0
    }
}
mod sys { pub mod thread_local { pub mod key {
    pub(crate) use crate::native_errno_location;
}}}
'''


REGRESSION = r'''
#[test]
fn abi_layout_retains_existing_cleanup_region() {
    assert_eq!(mem::offset_of!(NativeTlsHeader, namespace_head), 0);
    assert_eq!(mem::offset_of!(NativeTlsHeader, magic), size_of::<usize>());
    assert_eq!(mem::offset_of!(NativeTlsHeader, errno), size_of::<usize>() + 4);
    assert_eq!(NATIVE_TLS_MAGIC, 0x53435401);
    assert_eq!(tls::NATIVE_TLS_CLEANUP_OFFSET, 1024 * size_of::<usize>());
    assert_eq!(TLS_MAPPING_SIZE, tls::NATIVE_TLS_CLEANUP_OFFSET + 4096);
    assert!(size_of::<NativeTlsHeader>() < tls::NATIVE_TLS_CLEANUP_OFFSET);
}
#[test]
fn loader_then_executable_preserves_slot_and_namespace() {
    let base = ensure_native_tls();
    let header = base as *mut NativeTlsHeader;
    assert_eq!(unsafe { (*header).errno }, 0);
    let slot = native_errno_location();
    assert_eq!(slot, unsafe { &raw mut (*header).errno });
    unsafe { *slot = 123; (*header).namespace_head = 0x1000; }
    ALLOW_MAP.set(false);
    assert_eq!(ensure_native_tls(), base);
    assert_eq!(native_errno_location(), slot);
    assert_eq!(errno(), 123);
    assert_eq!(unsafe { (*header).namespace_head }, 0x1000);
    assert_eq!(MAPS.with_borrow(|m| m.len()), 1);
}
#[test]
fn child_allocation_initializes_before_any_child_code() {
    let parent = ensure_native_tls();
    unsafe { *native_errno_location() = 37; }
    let child = allocate_thread_tls().unwrap();
    assert_ne!(parent, child);
    assert_eq!(TP.get(), parent);
    let header = unsafe { &*(child as *const NativeTlsHeader) };
    assert_eq!((header.namespace_head, header.magic, header.errno), (0, NATIVE_TLS_MAGIC, 0));
    TP.set(child);
    assert_eq!(ensure_native_tls(), child);
    assert_eq!(errno(), 0);
    unsafe { *native_errno_location() = 91; }
    TP.set(parent);
    assert_eq!(errno(), 37);
}
#[test]
fn errno_never_lazily_initializes_absent_or_incompatible_tls() {
    ALLOW_MAP.set(false);
    for magic in [0, NATIVE_TLS_MAGIC ^ 1] {
        let mut header = NativeTlsHeader { magic, ..NativeTlsHeader::INITIAL };
        for base in [0, &raw mut header as usize] {
            TP.set(base);
            let error = std::panic::catch_unwind(native_errno_location).unwrap_err();
            assert!(error.is::<InvalidTls>());
            assert!(MAPS.with_borrow(|m| m.is_empty()));
        }
    }
}
#[test]
fn incompatible_existing_mapping_is_not_reinitialized() {
    let mut header = NativeTlsHeader { magic: 0, namespace_head: 0x1234, errno: 99 };
    TP.set(&raw mut header as usize);
    ALLOW_MAP.set(false);
    assert!(std::panic::catch_unwind(ensure_native_tls).unwrap_err().is::<InvalidTls>());
    assert_eq!((header.namespace_head, header.magic, header.errno), (0x1234, 0, 99));
}
#[test]
fn concurrent_threads_keep_independent_slots() {
    let barrier = std::sync::Arc::new(std::sync::Barrier::new(8));
    let threads: Vec<_> = (1..=8).map(|value| {
        let barrier = barrier.clone();
        std::thread::spawn(move || {
            ensure_native_tls();
            unsafe { *native_errno_location() = value; }
            barrier.wait();
            assert_eq!(errno(), value);
        })
    }).collect();
    for thread in threads { thread.join().unwrap(); }
}
'''


if __name__ == "__main__":
    unittest.main()
