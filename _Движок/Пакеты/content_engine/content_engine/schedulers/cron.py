"""cron на сервере: свои строки в `crontab -l` помечены в конце именем задания `# KOS-content-engine-<id>-<job>`;
чужие строки не трогаются. Подтверждение — повторное `crontab -l`. Пропущенный запуск cron не догоняет — режим «по включению»
только у планировщика Windows (на сервере устройство не выключают)."""
from __future__ import annotations

from .base import PREFIX, Job, run_program

DOW = {"mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6, "sun": 0}


def line(job: Job) -> str:
    hh, mm = job.at.split(":")
    dow = str(DOW[job.weekday]) if job.every == "week" else "*"
    return f"{int(mm)} {int(hh)} * * {dow} cd '{job.workdir}' && {job.command}  # {job.name}"


class CronScheduler:
    kind = "cron"

    def __init__(self, runner=run_program, on_boot: bool = True):
        self.runner = runner

    def _table(self) -> list[str]:
        code, out = self.runner(["crontab", "-l"])
        return out.splitlines() if code == 0 else []

    def list(self) -> list[str]:
        return sorted(l.rsplit("# ", 1)[1] for l in self._table() if f"# {PREFIX}-" in l)

    def read(self, name: str) -> dict | None:
        row = next((l for l in self._table() if l.endswith(f"# {name}")), None)
        return {"line": row} if row else None

    def _write(self, lines: list[str]) -> tuple[int, str]:
        return self.runner(["crontab", "-"], input_text="\n".join(lines) + "\n")

    def install(self, job: Job) -> tuple[int, str]:
        rows = [l for l in self._table() if not l.endswith(f"# {job.name}")]
        return self._write(rows + [line(job)])

    def matches(self, job: Job, got: dict | None) -> bool:
        return bool(got) and got["line"] == line(job)

    def remove(self, name: str) -> tuple[int, str]:
        return self._write([l for l in self._table() if not l.endswith(f"# {name}")])
