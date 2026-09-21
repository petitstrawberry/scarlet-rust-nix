//! Execute the native linker in Scarlet, then execute each ELF it produced.
use std::fs;
use std::path::Path;
use std::process::{Command, Output};

type Result<T> = std::result::Result<T, Box<dyn std::error::Error>>;

fn record(directory: &Path, name: &str, output: &Output) -> Result<()> {
    fs::write(directory.join(format!("{name}.stdout")), &output.stdout)?;
    fs::write(directory.join(format!("{name}.stderr")), &output.stderr)?;
    fs::write(directory.join(format!("{name}.status")), format!("exit={:?}\n", output.status.code()))?;
    Ok(())
}

fn check_elf(path: &Path) -> Result<()> {
    let data = fs::read(path)?;
    if data.len() < 64 || &data[..8] != b"\x7fELF\x02\x01\x01\x53" {
        return Err("linker output is not native Scarlet ELF64".into());
    }
    let machine = if cfg!(target_arch = "aarch64") { 183 } else { 243 };
    if u16::from_le_bytes([data[16], data[17]]) != 2
        || u16::from_le_bytes([data[18], data[19]]) != machine
    {
        return Err("linker produced the wrong ELF type or architecture".into());
    }
    Ok(())
}

fn run() -> Result<()> {
    let args: Vec<_> = std::env::args().skip(1).collect();
    if !args.is_empty() && args.len() != 3 {
        return Err("usage: native-linker-probe [LINKER FIXTURES OUTPUT]".into());
    }
    let linker = args.first().map(String::as_str).unwrap_or("/system/bin/wild");
    let fixtures = Path::new(args.get(1).map(String::as_str).unwrap_or("/opt/native-linker/fixtures"));
    let output = Path::new(args.get(2).map(String::as_str).unwrap_or("/tmp/native-linker-output"));
    if output.exists() {
        return Err("refusing to reuse an existing evidence directory".into());
    }
    fs::create_dir_all(output)?;
    let version = Command::new(linker).arg("--version").output()?;
    record(output, "version", &version)?;
    if !version.status.success() {
        return Err("native linker --version failed".into());
    }
    let emulation = if cfg!(target_arch = "aarch64") { "aarch64elf" } else { "elf64lriscv" };
    let make_command = || {
        let mut command = Command::new(linker);
        command.args(["-m", emulation, "-static", "--threads=1", "--gc-sections",
                      "-z", "max-page-size=4096", "-e", "_start"]);
        command.arg(fixtures.join("main.o"));
        command
    };
    for case in ["direct", "archive"] {
        let executable = output.join(format!("hello-{case}"));
        let mut command = make_command();
        if case == "archive" {
            command.arg(fixtures.join("libanswer.a"));
        } else {
            command.arg(fixtures.join("answer.o")).arg(fixtures.join("bias.o"));
        }
        println!("NATIVE_LINKER linking {case}");
        let linked = command.arg("-o").arg(&executable).output()?;
        record(output, &format!("link-{case}"), &linked)?;
        if !linked.status.success() {
            return Err(format!("{case} link failed: {}", String::from_utf8_lossy(&linked.stderr)).into());
        }
        check_elf(&executable)?;
        let executed = Command::new(&executable).output()?;
        record(output, &format!("run-{case}"), &executed)?;
        if executed.status.code() != Some(37) {
            return Err(format!("{case} executable returned {:?}, expected 37", executed.status.code()).into());
        }
        println!("NATIVE_LINKER {case} executable returned 37");
    }
    let missing = make_command().arg("-o").arg(output.join("unresolved")).output()?;
    record(output, "missing-symbol", &missing)?;
    if missing.status.success() || !String::from_utf8_lossy(&missing.stderr).contains("answer") {
        return Err("missing strong symbol was not diagnosed".into());
    }
    let rust = fixtures.join("rust");
    let arguments = fs::read_to_string(rust.join("link.args"))?;
    let executable = output.join("hello-rust");
    println!("NATIVE_LINKER linking Rust std");
    let linked = Command::new(linker).current_dir(&rust).args(arguments.lines())
        .args(["--threads=1", "-o"]).arg(&executable).output()?;
    record(output, "link-rust", &linked)?;
    if !linked.status.success() {
        return Err(format!("Rust std link failed: {}", String::from_utf8_lossy(&linked.stderr)).into());
    }
    check_elf(&executable)?;
    let executed = Command::new(&executable).output()?;
    record(output, "run-rust", &executed)?;
    if executed.status.code() != Some(37) || executed.stdout != b"SCARLET_NATIVE_LINKER_RUST_OK\n" {
        return Err(format!("Rust std executable failed: status={}, stdout={:?}, stderr={:?}",
                           executed.status, executed.stdout, executed.stderr).into());
    }
    println!("NATIVE_LINKER Rust std executable printed expected text and returned 37");
    fs::write(output.join("PASS"), "Guest linker created and executed object, archive and Rust std ELFs; exit status 37.\n")?;
    println!("NATIVE_LINKER FULL PASS");
    Ok(())
}

fn main() {
    if let Err(error) = run() {
        eprintln!("NATIVE_LINKER FAIL: {error}");
        std::process::exit(1);
    }
}
