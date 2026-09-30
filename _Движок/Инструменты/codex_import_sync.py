"""Local, isolated diagnostics for native Codex external-session imports.

Never deletes source sessions. Probe writes only into a new diagnostic directory.
No conversation text is printed. No model turns or paid API calls are started.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import queue
import shutil
import sqlite3
import subprocess
import threading
import time
from contextlib import contextmanager
import csv
import io


@contextmanager
def database(*args, **kwargs):
    connection = sqlite3.connect(*args, **kwargs)
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def path_identity(path: Path) -> str:
    value = str(path.resolve())
    if value.startswith("\\\\?\\"):
        value = value[4:]
    return os.path.normcase(value)


class Server:
    def __init__(self, executable: Path, home: Path):
        if not executable.is_file():
            raise FileNotFoundError("Native Codex executable is missing: " + str(executable)
                                    + "; resolve the current installed package before running")
        env = dict(os.environ)
        env["CODEX_HOME"] = str(home)
        self.process = subprocess.Popen(
            [str(executable), "app-server"], env=env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self.queue: queue.Queue = queue.Queue()
        self.sequence = 0
        self.notifications = []
        threading.Thread(target=self._read, daemon=True).start()
        try:
            self.request("initialize", {
                "clientInfo": {"name": "codex-import-sync-diagnostic", "version": "0.1"},
                "capabilities": {"experimentalApi": True},
            })
        except BaseException:
            self.close()
            raise

    def _read(self):
        for line in self.process.stdout:
            try:
                self.queue.put(json.loads(line))
            except json.JSONDecodeError:
                continue

    def request(self, method: str, params: dict, timeout: float = 60) -> dict:
        self.sequence += 1
        request_id = self.sequence
        self.process.stdin.write(json.dumps({
            "id": request_id, "method": method, "params": params,
        }) + "\n")
        self.process.stdin.flush()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            message = self.queue.get(timeout=max(0.1, deadline - time.monotonic()))
            if message.get("id") == request_id:
                if "error" in message:
                    raise RuntimeError(f"{method}: {message['error']}")
                return message.get("result", {})
            self.notifications.append(message)
        raise TimeoutError(method)

    def import_item(self, item: dict) -> dict:
        result = self.request("externalAgentConfig/import", {
            "migrationItems": [item], "providerId": "claude-code", "source": "app",
        })
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            message = (self.notifications.pop(0) if self.notifications else
                       self.queue.get(timeout=max(0.1, deadline - time.monotonic())))
            if (message.get("method") == "externalAgentConfig/import/completed"
                    and message.get("params", {}).get("importId") == result["importId"]):
                rows = message["params"]["itemTypeResults"]
                return {
                    "successes": sum(len(row["successes"]) for row in rows),
                    "failures": sum(len(row["failures"]) for row in rows),
                    "failure_stages": [failure.get("failureStage")
                                       for row in rows for failure in row["failures"]],
                }
        raise TimeoutError("import completion")

    def close(self):
        self.process.terminate()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=5)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def backup_database(source: Path, target: Path):
    with database(source.as_uri() + "?mode=ro", uri=True) as src:
        with database(target) as dst:
            src.backup(dst)


def clone_target(home: Path, target: Path, record: dict, legacy: bool):
    """Redirect ALL cloned rollout paths before starting a server on the copy."""
    target.mkdir(parents=True, exist_ok=False)
    backup_database(home / "state_5.sqlite", target / "state_5.sqlite")
    thread_id = record["imported_thread_id"]
    with database(target / "state_5.sqlite") as conn:
        row = conn.execute("SELECT rollout_path FROM threads WHERE id=?", (thread_id,)).fetchone()
        if not row:
            raise ValueError("Imported destination is missing")
        source_rollout = Path(row[0])
        for tid, in conn.execute("SELECT id FROM threads").fetchall():
            destination = target / "sessions" / (tid + ".jsonl")
            conn.execute("UPDATE threads SET rollout_path=? WHERE id=?", (str(destination), tid))
        rollout = target / "sessions" / (thread_id + ".jsonl")
        rollout.parent.mkdir()
        shutil.copy2(source_rollout, rollout)
        if legacy:
            conn.execute("UPDATE threads SET history_mode='legacy' WHERE id=?", (thread_id,))
            lines = rollout.read_text(encoding="utf-8").splitlines()
            changed = []
            for line in lines:
                data = json.loads(line)
                if data.get("type") == "session_meta":
                    data["payload"]["history_mode"] = "legacy"
                    line = json.dumps(data, ensure_ascii=False)
                changed.append(line)
            rollout.write_text("\n".join(changed) + "\n", encoding="utf-8")
        conn.commit()
    # Ledger references a read-only original source, but only a copied destination.
    (target / "external_agent_session_imports.json").write_text(
        json.dumps({"records": [record], "detected_connector_records": []}), encoding="utf-8")
    return source_rollout, rollout


def target_record(home: Path, thread_id: str) -> dict:
    rows = json.loads((home / "external_agent_session_imports.json").read_text(encoding="utf-8"))["records"]
    rows = [r for r in rows if r["imported_thread_id"] == thread_id]
    if len(rows) != 1:
        raise ValueError("Need exactly one source mapping")
    return rows[0]


def target_rollout(home: Path, thread_id: str) -> Path:
    with database((home / "state_5.sqlite").as_uri() + "?mode=ro", uri=True) as conn:
        row = conn.execute("SELECT rollout_path FROM threads WHERE id=?", (thread_id,)).fetchone()
    if not row:
        raise ValueError("Thread missing")
    return Path(row[0])


def response_items(path: Path) -> list:
    return [row["payload"] for line in path.read_text(encoding="utf-8").splitlines()
            if (row := json.loads(line)).get("type") == "response_item"]


@contextmanager
def writer_lock(home: Path, thread_id: str):
    if os.name != "nt":
        raise RuntimeError("This lock adapter is verified only on Windows")
    import msvcrt
    lock = home / "thread-writer-locks" / (thread_id + ".lock")
    lock.parent.mkdir(exist_ok=True)
    with lock.open("a+b") as handle:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        try:
            yield
        finally:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


def desktop_or_cli_running() -> bool:
    if os.name != "nt":
        raise RuntimeError("Offline process gate is verified only on Windows")
    result = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True,
                            text=True, encoding="utf-8", errors="replace", check=True,
                            creationflags=subprocess.CREATE_NO_WINDOW)
    return any(row and row[0].lower() in {"codex.exe", "codex"}
               for row in csv.reader(io.StringIO(result.stdout)))


def probe(args):
    home = args.home.resolve()
    output = args.output.resolve()
    if output.exists():
        raise ValueError("Diagnostic output must be a NEW directory")
    if output == home or home in output.parents:
        raise ValueError("Diagnostic output must be outside the real Codex home")
    record = target_record(home, args.thread)
    source = Path(record["source_path"])
    before = digest(source)
    original_rollout = target_rollout(home, args.thread)
    original_hash = digest(original_rollout)
    original_items = response_items(original_rollout)
    output.mkdir(parents=True)
    # Even detection runs against an isolated home; no real-home server is started.
    detection_home = output / "detection"
    detection_home.mkdir()
    with Server(args.exe, detection_home) as server:
        detected = server.request("externalAgentConfig/detect", {"includeHome": True})
    candidates = []
    for raw in detected.get("items", []):
        item = copy.deepcopy(raw)
        sessions = [s for s in (item.get("details") or {}).get("sessions", [])
                    if path_identity(Path(s["path"])) == path_identity(source)]
        if sessions:
            item["details"]["sessions"] = sessions
            candidates.append(item)
    if len(candidates) != 1:
        raise ValueError("Source not detected uniquely")
    result = {"thread_id": args.thread, "source_sha256_before": before, "variants": {}}
    for name, legacy in [("baseline", False), ("legacy_copy", True)]:
        copied_home = output / name
        original_rollout, copied_rollout = clone_target(home, copied_home, record, legacy)
        with Server(args.exe, copied_home) as server:
            first = server.import_item(candidates[0])
            second = server.import_item(candidates[0])
        lines = [json.loads(line) for line in copied_rollout.read_text(encoding="utf-8").splitlines()]
        with database(copied_home / "state_5.sqlite") as conn:
            mode = conn.execute("SELECT history_mode FROM threads WHERE id=?", (args.thread,)).fetchone()[0]
        result["variants"][name] = {
            "first_import": first, "second_import": second,
            "response_items": sum(line.get("type") == "response_item" for line in lines),
            "history_mode_after": mode,
            "ledger_matches_source": json.loads((copied_home / "external_agent_session_imports.json").read_text(encoding="utf-8"))["records"][0]["content_sha256"] == before,
            "old_response_prefix_preserved": response_items(copied_rollout)[:len(original_items)] == original_items,
        }
        if digest(source) != before or digest(original_rollout) != original_hash:
            raise RuntimeError("Original data changed during isolated probe; stop")
    result["original_source_unchanged"] = digest(source) == before
    result["original_rollout_unchanged"] = digest(original_rollout) == original_hash
    (output / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


def write_report(output: Path, report: dict):
    temporary = output / "report.json.new"
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, output / "report.json")
    print("Status: " + report["status"], flush=True)


def repair(args):
    """One offline, guarded repair. Existing thread/source IDs are retained."""
    if not args.exe.is_file():
        raise FileNotFoundError("Native Codex executable is missing: " + str(args.exe))
    home, output = args.home.resolve(), args.output.resolve()
    if output.exists() or output == home or home in output.parents:
        raise ValueError("Repair output must be a new directory outside the real home")
    output.mkdir(parents=True)
    report = {"thread_id": args.thread, "status": "waiting_for_codex_exit", "home": str(home)}
    write_report(output, report)
    deadline = time.monotonic() + args.wait_seconds
    next_notice = time.monotonic()
    while desktop_or_cli_running():
        if time.monotonic() >= deadline:
            report["status"] = "cancelled_without_changes"
            write_report(output, report)
            return
        if time.monotonic() >= next_notice:
            print("Waiting for all Codex processes to exit, including the Codex extension in Cursor. "
                  "No chat changes have started. Remaining seconds: "
                  + str(max(0, int(deadline - time.monotonic()))), flush=True)
            next_notice = time.monotonic() + 15
        try:
            time.sleep(3)
        except KeyboardInterrupt:
            report["status"] = "cancelled_without_changes"
            write_report(output, report)
            return
    try:
        with writer_lock(home, args.thread):
            record = target_record(home, args.thread)
            source = Path(record["source_path"])
            rollout = target_rollout(home, args.thread)
            old_data = rollout.read_bytes()
            old_hash, source_hash = digest(rollout), digest(source)
            rows = [json.loads(line) for line in old_data.decode("utf-8").splitlines()]
            if not rows or rows[0].get("type") != "session_meta":
                raise ValueError("No initial session metadata")
            meta = rows[0]["payload"]
            if meta.get("id") != args.thread or meta.get("forked_from_id") or meta.get("history_base"):
                raise ValueError("Only one complete non-forked imported rollout is supported")
            if any(r.get("type") in {"turn_context", "compacted", "inter_agent_communication"} for r in rows):
                raise ValueError("Destination has native work or compacted/branched history")
            with database((home / "state_5.sqlite").as_uri() + "?mode=ro", uri=True) as conn:
                metadata = conn.execute("SELECT history_mode, archived FROM threads WHERE id=?", (args.thread,)).fetchone()
                columns = [column[1] for column in conn.execute("PRAGMA table_info(threads)")]
                row_backup = conn.execute("SELECT * FROM threads WHERE id=?", (args.thread,)).fetchone()
            if not metadata or metadata[1] or metadata[0] != meta.get("history_mode"):
                raise ValueError("Metadata mismatch or archived destination")
            (output / "rollout.before.jsonl").write_bytes(old_data)
            report.update({"rollout": str(rollout), "source_sha256": source_hash,
                           "rollout_sha256_before": old_hash, "history_mode_before": metadata[0],
                           "ledger_record_before": record, "metadata_row_before": dict(zip(columns, row_backup)),
                           "status": "validating_isolated_copy"})
            write_report(output, report)
            staged_args = copy.copy(args)
            staged_args.output = output / "staging"
            probe(staged_args)
            staged_result = json.loads((staged_args.output / "result.json").read_text(encoding="utf-8"))
            verified = staged_result["variants"]["legacy_copy"]
            if (verified["first_import"]["successes"] != 1 or verified["first_import"]["failures"]
                    or not verified["old_response_prefix_preserved"] or not verified["ledger_matches_source"]
                    or verified["second_import"]["successes"] or verified["second_import"]["failures"]):
                raise ValueError("Isolated update did not pass all checks")
            expected_rollout = staged_args.output / "legacy_copy" / "sessions" / (args.thread + ".jsonl")
            expected_items = response_items(expected_rollout)
            if desktop_or_cli_running() or digest(source) != source_hash or digest(rollout) != old_hash:
                raise ValueError("Codex restarted or source/destination changed; no original changes allowed")
            # Preserve every original byte after the first metadata line.
            first_line, remainder = old_data.split(b"\n", 1)
            first = json.loads(first_line)
            first["payload"]["history_mode"] = "legacy"
            converted = json.dumps(first, ensure_ascii=False).encode("utf-8") + b"\n" + remainder
            temporary = rollout.with_name(rollout.name + ".import-sync-new")
            temporary.write_bytes(converted)
            report.update({"status": "converting_metadata", "converted_sha256": hashlib.sha256(converted).hexdigest()})
            write_report(output, report)
            with database(home / "state_5.sqlite", timeout=10) as conn:
                conn.execute("BEGIN IMMEDIATE")
                count = conn.execute("UPDATE threads SET history_mode='legacy' WHERE id=? AND history_mode=?",
                                     (args.thread, metadata[0])).rowcount
                if count != 1:
                    raise ValueError("Concurrent metadata change")
                os.replace(temporary, rollout)
                report["status"] = "rollout_metadata_replaced_sql_pending"
                write_report(output, report)
                conn.commit()
            report["status"] = "metadata_converted"
            write_report(output, report)
        # All other Codex processes must remain absent until the native import is verified.
        if desktop_or_cli_running():
            raise RuntimeError("Codex restarted during offline maintenance; use the recovery report")
        with Server(args.exe, home) as server:
            detected = server.request("externalAgentConfig/detect", {"includeHome": True})
            chosen = []
            for raw in detected.get("items", []):
                item = copy.deepcopy(raw)
                found = [s for s in (item.get("details") or {}).get("sessions", [])
                         if path_identity(Path(s["path"])) == path_identity(source)]
                if found:
                    item["details"]["sessions"] = found
                    chosen.append(item)
            if len(chosen) != 1:
                raise RuntimeError("Source detection changed after validated conversion")
            report["status"] = "native_import_running"
            write_report(output, report)
            outcome = server.import_item(chosen[0])
            repeat = server.import_item(chosen[0])
        with writer_lock(home, args.thread):
            actual_items = response_items(rollout)
            actual_record = target_record(home, args.thread)
            first_after, remainder_after = rollout.read_bytes().split(b"\n", 1)
            with database((home / "state_5.sqlite").as_uri() + "?mode=ro", uri=True) as conn:
                mode_after = conn.execute("SELECT history_mode FROM threads WHERE id=?", (args.thread,)).fetchone()[0]
            if (outcome["successes"] != 1 or outcome["failures"] or repeat["successes"] or repeat["failures"]
                    or actual_items != expected_items or digest(source) != source_hash
                    or actual_record["content_sha256"] != source_hash
                    or mode_after != "legacy" or json.loads(first_after)["payload"].get("history_mode") != "legacy"
                    or not remainder_after.startswith(remainder) or desktop_or_cli_running()):
                raise RuntimeError("Final verification failed; backup retained, no blind rollback")
            report.update({"status": "completed", "native_import": outcome, "repeat_import": repeat,
                           "added_response_items": len(actual_items) - len(response_items(output / "rollout.before.jsonl")),
                           "rollout_sha256_after": digest(rollout), "response_items_match_staging": True,
                           "all_original_lines_after_metadata_preserved": True,
                           "source_unchanged": True, "thread_id_preserved": True})
            write_report(output, report)
    except Exception as error:
        report["failed_phase"] = report["status"]
        report["status"] = "stopped_requires_inspection"
        report["error"] = str(error)
        write_report(output, report)
        raise


def recover(args):
    """Recover only an interrupted metadata switch, never an unknown completed import."""
    output, home = args.output.resolve(), args.home.resolve()
    report = json.loads((output / "report.json").read_text(encoding="utf-8"))
    phase = report.get("failed_phase", report["status"])
    if phase not in {"converting_metadata", "rollout_metadata_replaced_sql_pending", "metadata_converted"}:
        raise ValueError("No automatic recovery for this phase; no data was changed by recovery")
    if path_identity(Path(report["home"])) != path_identity(home) or report["thread_id"] != args.thread:
        raise ValueError("Recovery target mismatch")
    if desktop_or_cli_running():
        raise ValueError("Exit Codex and all Codex CLI processes before recovery")
    with writer_lock(home, args.thread):
        path = target_rollout(home, args.thread)
        current_hash = digest(path)
        if current_hash not in {report["rollout_sha256_before"], report["converted_sha256"]}:
            raise ValueError("Rollout changed after snapshot; recovery refused")
        if target_record(home, args.thread) != report["ledger_record_before"]:
            raise ValueError("Import ledger changed; recovery refused")
        desired = "legacy" if current_hash == report["converted_sha256"] else report["history_mode_before"]
        if json.loads(path.read_text(encoding="utf-8").splitlines()[0])["payload"].get("history_mode") != desired:
            raise ValueError("Unexpected rollout metadata")
        with database(home / "state_5.sqlite") as conn:
            conn.execute("BEGIN IMMEDIATE")
            mode = conn.execute("SELECT history_mode FROM threads WHERE id=?", (args.thread,)).fetchone()[0]
            if mode not in {"legacy", report["history_mode_before"]}:
                raise ValueError("Metadata changed; recovery refused")
            conn.execute("UPDATE threads SET history_mode=? WHERE id=?", (desired, args.thread))
            conn.commit()
        report["status"] = "recovered_metadata_consistency_import_pending"
        write_report(output, report)
    print(json.dumps({"status": report["status"], "history_mode": desired}))


def arm(args):
    """Start the one-off offline repair hidden; never closes apps itself."""
    home, output = args.home.resolve(), args.output.resolve()
    if output.exists() or output == home or home in output.parents:
        raise ValueError("Worker output must be a new directory outside the real home")
    target_record(home, args.thread)
    target_rollout(home, args.thread)
    if not args.exe.is_file():
        raise ValueError("Native Codex executable is missing")
    command = [__import__("sys").executable, str(Path(__file__).resolve()), "repair",
               "--home", str(args.home), "--exe", str(args.exe), "--thread", args.thread,
               "--output", str(args.output), "--wait-seconds", str(args.wait_seconds)]
    child = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    deadline = time.monotonic() + 10
    report_path = args.output / "report.json"
    while not report_path.exists() and time.monotonic() < deadline and child.poll() is None:
        time.sleep(0.1)
    if not report_path.exists():
        print(json.dumps({"status": "worker_state_unknown", "pid": child.pid, "report": str(report_path)}))
        return
    published = json.loads(report_path.read_text(encoding="utf-8"))
    print(json.dumps({"status": published["status"], "pid": child.pid, "report": str(report_path)}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["probe", "repair", "arm", "recover"])
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument("--exe", type=Path, required=True)
    parser.add_argument("--thread", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--wait-seconds", type=int, default=3600)
    args = parser.parse_args()
    {"probe": probe, "repair": repair, "arm": arm, "recover": recover}[args.command](args)


if __name__ == "__main__":
    main()
