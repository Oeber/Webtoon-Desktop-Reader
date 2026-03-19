from __future__ import annotations

import argparse
import ctypes
import os
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path


WAIT_TIMEOUT_SECONDS = 120
WAIT_INTERVAL_SECONDS = 0.5
COPY_RETRY_COUNT = 40
COPY_RETRY_DELAY_SECONDS = 0.5
STILL_ACTIVE = 259
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
CHUNK_SIZE = 1024 * 256


def _trace_path(install_dir: Path) -> Path:
    return install_dir / "data" / "last_update_trace.txt"


def _error_path(install_dir: Path) -> Path:
    return install_dir / "data" / "last_update_error.txt"


def _write_trace(install_dir: Path, message: str) -> None:
    path = _trace_path(install_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")
    print(line, flush=True)


def _pid_is_running_windows(pid: int) -> bool:
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return False
    try:
        exit_code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return False
        return exit_code.value == STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def _pid_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        return _pid_is_running_windows(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return True
    return True


def _wait_for_parent_exit(pid: int, install_dir: Path) -> None:
    cycles = int(WAIT_TIMEOUT_SECONDS / WAIT_INTERVAL_SECONDS)
    for attempt in range(cycles):
        if not _pid_is_running(pid):
            _write_trace(install_dir, f"Parent process {pid} exited after {attempt} wait cycle(s)")
            return
        if attempt and attempt % 20 == 0:
            _write_trace(install_dir, f"Still waiting for parent process {pid} to exit ({attempt} cycle(s))")
        time.sleep(WAIT_INTERVAL_SECONDS)
    raise RuntimeError(f"Parent process {pid} did not exit before the installer timeout.")


def _copy_file_with_retry(source: Path, destination: Path, install_dir: Path) -> None:
    last_error: Exception | None = None
    for attempt in range(1, COPY_RETRY_COUNT + 1):
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                destination.unlink()
            shutil.copy2(source, destination)
            _write_trace(install_dir, f"Copied file '{source}' -> '{destination}'")
            return
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            _write_trace(install_dir, f"Copy attempt {attempt} failed for '{source}': {exc}")
            time.sleep(COPY_RETRY_DELAY_SECONDS)
    raise RuntimeError(str(last_error) if last_error else f"Failed to copy {source}")


def _copy_directory_contents_with_retry(source_dir: Path, destination_dir: Path, install_dir: Path) -> None:
    destination_dir.mkdir(parents=True, exist_ok=True)
    for child in source_dir.iterdir():
        destination = destination_dir / child.name
        if child.is_dir():
            _copy_directory_contents_with_retry(child, destination, install_dir)
        else:
            _copy_file_with_retry(child, destination, install_dir)


def _copy_root(extract_dir: Path, install_dir: Path) -> None:
    entries = list(extract_dir.iterdir())
    copy_root = extract_dir
    if len(entries) == 1 and entries[0].is_dir():
        copy_root = entries[0]
        _write_trace(install_dir, f"Using single top-level directory '{copy_root}' as copy root")
    else:
        _write_trace(install_dir, f"Using archive extraction root '{copy_root}' as copy root")

    for child in copy_root.iterdir():
        destination = install_dir / child.name
        if child.is_dir():
            _copy_directory_contents_with_retry(child, destination, install_dir)
        else:
            _copy_file_with_retry(child, destination, install_dir)


def _extract_archive(zip_path: Path, install_dir: Path) -> Path:
    extract_dir = Path(tempfile.mkdtemp(prefix="webtoon-reader-update-"))
    _write_trace(install_dir, f"Expanding archive to '{extract_dir}'")
    with zipfile.ZipFile(zip_path) as archive:
        archive.extractall(extract_dir)
    return extract_dir


def _launch_app(exe_path: Path, install_dir: Path) -> None:
    if not exe_path.exists():
        raise RuntimeError(f"Updated executable was not found at '{exe_path}' after copy.")
    _write_trace(install_dir, f"Launching updated executable '{exe_path}'")
    subprocess.Popen([str(exe_path)], cwd=str(install_dir), close_fds=True)
    _write_trace(install_dir, "Updated executable launched successfully")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--zip-path", required=True)
    parser.add_argument("--install-dir", required=True)
    parser.add_argument("--exe-path", required=True)
    parser.add_argument("--parent-pid", required=True, type=int)
    args = parser.parse_args()

    zip_path = Path(args.zip_path).resolve()
    install_dir = Path(args.install_dir).resolve()
    exe_path = Path(args.exe_path).resolve()
    error_path = _error_path(install_dir)
    extract_dir: Path | None = None

    try:
        error_path.unlink(missing_ok=True)
    except OSError:
        pass

    _write_trace(install_dir, f"Installer starting. Zip='{zip_path}' InstallDir='{install_dir}' Exe='{exe_path}' ParentPid={args.parent_pid}")

    try:
        _wait_for_parent_exit(args.parent_pid, install_dir)
        extract_dir = _extract_archive(zip_path, install_dir)
        _copy_root(extract_dir, install_dir)
        _launch_app(exe_path, install_dir)
        return 0
    except Exception as exc:  # noqa: BLE001
        error_path.parent.mkdir(parents=True, exist_ok=True)
        error_path.write_text(f"{exc}\n", encoding="utf-8")
        _write_trace(install_dir, f"Installer failed: {exc}")
        return 1
    finally:
        _write_trace(install_dir, "Installer cleanup starting")
        time.sleep(2)
        if extract_dir is not None:
            shutil.rmtree(extract_dir, ignore_errors=True)
        zip_path.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())