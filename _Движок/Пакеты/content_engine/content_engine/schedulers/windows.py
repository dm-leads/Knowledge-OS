"""Планировщик Windows: `schtasks /Create /XML` — задание из XML (UTF-16), чтобы задать «запуск при включении, если
пропущен» (`StartWhenAvailable`); подтверждение — `schtasks /Query /TN … /XML` (чтение обратно: команда и время)."""
from __future__ import annotations

import re
import tempfile
from pathlib import Path
from xml.sax.saxutils import escape

from .base import PREFIX, Job, run_program

DAYS = {"mon": "Monday", "tue": "Tuesday", "wed": "Wednesday", "thu": "Thursday", "fri": "Friday",
        "sat": "Saturday", "sun": "Sunday"}


def xml(job: Job, on_boot: bool) -> str:
    exe, _, args = job.command.partition(" ")
    if job.every == "week":
        schedule = (f"<ScheduleByWeek><WeeksInterval>1</WeeksInterval><DaysOfWeek><{DAYS[job.weekday]} />"
                    "</DaysOfWeek></ScheduleByWeek>")
    else:
        schedule = "<ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay>"
    return ('<?xml version="1.0" encoding="UTF-16"?>\n'
            '<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">'
            f"<RegistrationInfo><Description>Контент-машина: {escape(job.name)}</Description></RegistrationInfo>"
            f"<Triggers><CalendarTrigger><StartBoundary>2026-01-01T{job.at}:00</StartBoundary>"
            f"<Enabled>true</Enabled>{schedule}</CalendarTrigger></Triggers>"
            "<Settings><MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>"
            "<DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>"
            "<StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>"
            f"<StartWhenAvailable>{'true' if on_boot else 'false'}</StartWhenAvailable>"
            "<ExecutionTimeLimit>PT2H</ExecutionTimeLimit><Enabled>true</Enabled></Settings>"
            f"<Actions><Exec><Command>{escape(exe)}</Command><Arguments>{escape(args)}</Arguments>"
            f"<WorkingDirectory>{escape(job.workdir)}</WorkingDirectory></Exec></Actions></Task>")


class WindowsScheduler:
    kind = "windows"

    def __init__(self, runner=None, on_boot: bool = True):
        # schtasks пишет в OEM-кодировке консоли (cp866): прочитанный как UTF-8 путь с кириллицей не совпал бы
        self.ps_runner = runner or (lambda args, input_text=None: run_program(args, input_text=input_text))
        runner = runner or (lambda args, input_text=None: run_program(args, input_text=input_text, encoding="oem"))
        self.runner = runner
        self.on_boot = on_boot

    def list(self) -> list[str]:
        code, out = self.runner(["schtasks", "/Query", "/FO", "CSV", "/NH"])
        if code != 0:
            from ..core.errors import EngineError
            raise EngineError(f"schtasks /Query: код {code} — список заданий не прочитан: {out.strip()[:200]}")
        names = []
        for line in out.splitlines():
            m = re.match(r'^"\\?([^"]+)"', line.strip())
            if m and m.group(1).startswith(PREFIX):
                names.append(m.group(1))
        return sorted(set(names))

    def read(self, name: str) -> dict | None:
        # PowerShell отдаёт XML задания в UTF-8: в OEM-кодировке консоли нет длинного тире и части знаков пути
        code, out = self.ps_runner(["powershell", "-NoProfile", "-Command",
                                    "[Console]::OutputEncoding=[Text.Encoding]::UTF8; "
                                    f"Export-ScheduledTask -TaskName '{name}'"])
        if code != 0 or "<Task" not in out:
            code, out = self.runner(["schtasks", "/Query", "/TN", name, "/XML"])
        if code != 0:
            return None
        get = lambda tag: (re.search(rf"<{tag}>(.*?)</{tag}>", out, re.S) or [None, ""])[1]   # noqa: E731
        return {"command": f"{get('Command')} {get('Arguments')}".strip(), "start": get("StartBoundary"),
                "start_when_available": get("StartWhenAvailable") == "true", "workdir": get("WorkingDirectory")}

    def install(self, job: Job) -> tuple[int, str]:
        folder = Path(tempfile.mkdtemp(prefix="kos-schedule-"))
        path = folder / "task.xml"
        path.write_text(xml(job, self.on_boot), encoding="utf-16")
        try:
            return self.runner(["schtasks", "/Create", "/TN", job.name, "/XML", str(path), "/F"])
        finally:
            path.unlink(missing_ok=True)
            folder.rmdir()

    def matches(self, job: Job, got: dict | None) -> bool:
        from xml.sax.saxutils import unescape
        command = unescape(got["command"], {"&quot;": '"', "&apos;": "'"}) if got else ""
        return bool(got) and command == job.command and got["start"].endswith(f"T{job.at}:00")

    def remove(self, name: str) -> tuple[int, str]:
        return self.runner(["schtasks", "/Delete", "/TN", name, "/F"])
