import argparse
import atexit
from dataclasses import dataclass
from datetime import datetime
from importlib import import_module
from pathlib import Path

from core.app_paths import data_path


DEFAULT_LIMIT = 75
DEFAULT_CLOCK = "wall"
DEFAULT_SORT = "ttot"
MAX_PROFILE_RUNS = 5
CLOCK_CHOICES = ("wall", "cpu")
SORT_CHOICES = ("name", "ncall", "ttot", "tsub", "tavg")


@dataclass(frozen=True)
class ProfileConfig:
    enabled: bool
    name: str = ""
    clock_type: str = DEFAULT_CLOCK
    sort_key: str = DEFAULT_SORT
    limit: int = DEFAULT_LIMIT
    builtins: bool = False
    output_dir: Path | None = None


class SessionProfiler:
    def __init__(self, config: ProfileConfig, logger):
        self._config = config
        self._logger = logger
        self._dumped = False
        self._yappi = None

    @property
    def enabled(self) -> bool:
        return self._config.enabled

    def start(self) -> None:
        if not self.enabled:
            return

        yappi = self._load_yappi()
        yappi.set_clock_type(self._config.clock_type)
        yappi.start(builtins=self._config.builtins, profile_threads=True)
        self._logger.info(
            "Profiler enabled: name=%s clock=%s sort=%s limit=%s builtins=%s",
            self._config.name,
            self._config.clock_type,
            self._config.sort_key,
            self._config.limit,
            self._config.builtins,
        )

    def stop(self) -> None:
        if not self.enabled or self._dumped:
            return

        yappi = self._load_yappi()
        self._dumped = True

        if not yappi.is_running():
            return

        yappi.stop()

        assert self._config.output_dir is not None
        self._config.output_dir.mkdir(parents=True, exist_ok=True)

        func_stats = yappi.get_func_stats()
        func_stats.sort(self._config.sort_key, "desc")
        func_stats.save(str(self._config.output_dir / f"{self._config.name}.callgrind"), type="callgrind")
        func_stats.save(str(self._config.output_dir / f"{self._config.name}.pstat"), type="pstat")
        self._write_function_summary(func_stats, self._config.output_dir / f"{self._config.name}.functions.txt")

        thread_stats = yappi.get_thread_stats()
        thread_stats.sort("ttot", "desc")
        self._write_thread_summary(thread_stats, self._config.output_dir / f"{self._config.name}.threads.txt")
        self._trim_old_runs()

        yappi.clear_stats()
        self._logger.info("Profiler results written to %s", self._config.output_dir)

    def _load_yappi(self):
        if self._yappi is None:
            self._yappi = import_module("yappi")
        return self._yappi

    def _write_function_summary(self, func_stats, output_path: Path) -> None:
        lines = [
            f"Clock type: {self._config.clock_type}",
            f"Ordered by: {self._config.sort_key} desc",
            f"Showing top: {self._config.limit}",
            "",
            f"{'Function':70} {'Calls':>10} {'Self(s)':>12} {'Total(s)':>12} {'Avg(s)':>12}",
            "-" * 120,
        ]

        for stat in list(func_stats)[: self._config.limit]:
            location = f"{stat.module}:{stat.lineno}"
            label = f"{location}::{stat.name}"
            lines.append(
                f"{label[:70]:70} {stat.ncall:>10} {stat.tsub:>12.6f} {stat.ttot:>12.6f} {stat.tavg:>12.6f}"
            )

        output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _write_thread_summary(self, thread_stats, output_path: Path) -> None:
        lines = [
            f"Clock type: {self._config.clock_type}",
            "Ordered by: ttot desc",
            "",
            f"{'Thread':40} {'Id':>8} {'Sched':>8} {'Total(s)':>12}",
            "-" * 72,
        ]

        for stat in list(thread_stats):
            lines.append(f"{stat.name[:40]:40} {stat.id:>8} {stat.sched_count:>8} {stat.ttot:>12.6f}")

        output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _trim_old_runs(self) -> None:
        assert self._config.output_dir is not None

        suffixes = (".functions.txt", ".threads.txt", ".callgrind", ".pstat")
        runs: dict[str, list[Path]] = {}

        for path in self._config.output_dir.iterdir():
            if not path.is_file():
                continue
            for suffix in suffixes:
                if path.name.endswith(suffix):
                    run_name = path.name[: -len(suffix)]
                    runs.setdefault(run_name, []).append(path)
                    break

        if len(runs) <= MAX_PROFILE_RUNS:
            return

        sorted_runs = sorted(
            runs.items(),
            key=lambda item: max(file.stat().st_mtime for file in item[1]),
            reverse=True,
        )

        for _, files in sorted_runs[MAX_PROFILE_RUNS:]:
            for file in files:
                try:
                    file.unlink()
                except FileNotFoundError:
                    continue


def parse_profile_args(argv: list[str]) -> tuple[ProfileConfig, list[str]]:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--profile", action="store_true")
    parser.add_argument("--profile-clock", choices=CLOCK_CHOICES, default=DEFAULT_CLOCK)
    parser.add_argument("--profile-sort", choices=SORT_CHOICES, default=DEFAULT_SORT)
    parser.add_argument("--profile-limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument("--profile-name")
    parser.add_argument("--profile-builtins", action="store_true")
    parsed, remaining = parser.parse_known_args(argv[1:])

    if not parsed.profile:
        return ProfileConfig(enabled=False), [argv[0], *remaining]

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    name = _sanitize_profile_name(parsed.profile_name or f"session-{stamp}")

    return (
        ProfileConfig(
            enabled=True,
            name=name,
            clock_type=parsed.profile_clock,
            sort_key=parsed.profile_sort,
            limit=max(1, parsed.profile_limit),
            builtins=parsed.profile_builtins,
            output_dir=data_path("profiles"),
        ),
        [argv[0], *remaining],
    )


def create_session_profiler(argv: list[str], logger) -> tuple[SessionProfiler, list[str]]:
    config, remaining_argv = parse_profile_args(argv)
    profiler = SessionProfiler(config, logger)
    if profiler.enabled:
        atexit.register(profiler.stop)
    return profiler, remaining_argv


def _sanitize_profile_name(value: str) -> str:
    cleaned = "".join(char if char.isalnum() or char in ("-", "_") else "-" for char in value.strip())
    cleaned = cleaned.strip("-_")
    return cleaned or "session"
