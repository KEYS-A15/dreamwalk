"""Opt-in Python execution recording. Static analysis never imports this path."""

import dis
import hashlib
import json
import runpy
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .analyzer import IGNORED, PythonAnalyzer

RUN_SCHEMA_VERSION = "1.0"
RECORDER_VERSION = "0.5.0"


class _TraceRecorder:
    def __init__(self, root: Path, max_events: int):
        self.root = root
        self.max_events = max_events
        self.events = []
        self.files = {}
        self.frames = {}
        self.stacks = {}
        self.thread_numbers = {}
        self.next_call_id = 1
        self.truncated = False
        self.lock = threading.Lock()

    def trace(self, frame, event, arg):
        path = self.relative_path(frame.f_code.co_filename)
        if path is None:
            return None
        thread_ident = threading.get_ident()
        with self.lock:
            thread = self.thread_numbers.setdefault(thread_ident, len(self.thread_numbers) + 1)
            stack = self.stacks.setdefault(thread_ident, [])
            key = (thread_ident, id(frame))
            call_id = self.frames.get(key)
            if event == "call":
                if call_id is None:
                    call_id = self.next_call_id
                    self.next_call_id += 1
                    self.frames[key] = call_id
                    parent_call_id = stack[-1] if stack else None
                    kind = "call"
                else:
                    parent_call_id = stack[-1] if stack else None
                    kind = "resume"
                stack.append(call_id)
                self.append(kind, frame, path, thread, call_id, parent_call_id)
            elif call_id is not None and event == "line":
                self.append("line", frame, path, thread, call_id)
            elif call_id is not None and event == "exception":
                exception_type = arg[0]
                self.append("exception", frame, path, thread, call_id,
                            exception_type=f"{exception_type.__module__}.{exception_type.__qualname__}")
            elif call_id is not None and event == "return":
                opcode = frame.f_code.co_code[frame.f_lasti] if frame.f_lasti >= 0 else -1
                kind = "yield" if dis.opname[opcode] in {"YIELD_VALUE", "YIELD_FROM"} else "return"
                self.append(kind, frame, path, thread, call_id)
                if stack and stack[-1] == call_id:
                    stack.pop()
                if kind == "return":
                    self.frames.pop(key, None)
        return self.trace

    def relative_path(self, filename: str) -> str | None:
        try:
            path = Path(filename).resolve(strict=True)
        except (OSError, RuntimeError):
            return None
        if not path.is_file() or not path.is_relative_to(self.root):
            return None
        relative = path.relative_to(self.root)
        if any(part in IGNORED for part in relative.parts):
            return None
        name = relative.as_posix()
        if name not in self.files:
            try:
                self.files[name] = hashlib.sha256(path.read_bytes()).hexdigest()
            except OSError:
                self.files[name] = None
        return name

    def append(self, kind, frame, path, thread, call_id, parent_call_id=None,
               exception_type=None):
        if len(self.events) >= self.max_events:
            self.truncated = True
            return
        event = {
            "sequence": len(self.events) + 1,
            "kind": kind,
            "thread": thread,
            "call_id": call_id,
            "path": path,
            "line": frame.f_lineno,
            "function": frame.f_code.co_name,
            "qualified_name": frame.f_code.co_qualname,
        }
        if parent_call_id is not None:
            event["parent_call_id"] = parent_call_id
        if exception_type:
            event["exception_type"] = exception_type
        self.events.append(event)


def record_python(root: Path, script: Path, arguments: list[str],
                  max_events: int = 100_000) -> tuple[dict, int]:
    root = root.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("Repository root must be a directory")
    if not 1 <= max_events <= 1_000_000:
        raise ValueError("max-events must be between 1 and 1000000")
    script = script if script.is_absolute() else root / script
    if script.is_symlink():
        raise ValueError("Recorded script must not be a symlink")
    script = script.resolve(strict=True)
    if not script.is_file() or script.suffix.lower() != ".py" or not script.is_relative_to(root):
        raise ValueError("Recorded script must be a .py file inside the repository root")

    graph = PythonAnalyzer().analyze(root)
    started_at = datetime.now(timezone.utc).isoformat()
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="dreamwalk-record-") as temporary:
        raw_path = Path(temporary) / "events.json"
        command = [sys.executable, "-m", "dreamwalk.recording", "--child", str(root),
                   str(raw_path), str(max_events), str(script), *arguments]
        completed = subprocess.run(command, cwd=root, check=False)
        if not raw_path.is_file():
            raise RuntimeError("Recorded process ended before producing trace data")
        raw = json.loads(raw_path.read_text(encoding="utf-8"))

    run = {
        "kind": "observed_run",
        "schema_version": RUN_SCHEMA_VERSION,
        "recorder_version": RECORDER_VERSION,
        "language": "python",
        "evidence_kind": "observed",
        "run_id": str(uuid.uuid4()),
        "started_at": started_at,
        "duration_ms": round((time.perf_counter() - started) * 1000),
        "target": script.relative_to(root).as_posix(),
        "argument_count": len(arguments),
        "exit_code": completed.returncode,
        "truncated": raw["truncated"],
        "static_snapshot": {
            "schema_version": graph.schema_version,
            "files": graph.files,
            "diagnostics": graph.diagnostics,
        },
        "observed_files": raw["files"],
        "events": raw["events"],
        "limitations": [
            "Only Python frames whose files resolve inside the repository root are recorded.",
            "Arguments, local variables, return values and exception messages are not recorded.",
            "Trace events show one observed run and do not prove every possible path.",
            "Target code can replace the Python trace function and make a recording incomplete.",
            "Abrupt process termination may prevent a recording from being written.",
        ],
    }
    _link_events(run, graph)
    return run, completed.returncode


def _link_events(run: dict, graph):
    static_hashes = {item["path"]: item["sha256"] for item in graph.files}
    nodes_by_path = {}
    for node in graph.nodes:
        nodes_by_path.setdefault(node.location.path, []).append(node)
    for event in run["events"]:
        path = event["path"]
        if static_hashes.get(path) != run["observed_files"].get(path):
            event["resolution"] = "source_mismatch"
            continue
        nodes = nodes_by_path.get(path, [])
        if event["function"] == "<module>":
            symbols = [node for node in nodes if node.kind == "module"]
        else:
            qualified = event["qualified_name"].replace(".<locals>", "")
            symbols = [node for node in nodes if node.kind in {"function", "class"}
                       and node.qualified_name.endswith(f".{qualified}")]
        if len(symbols) == 1:
            event["symbol_id"] = symbols[0].id
            event["resolution"] = "exact_static_symbol"
        else:
            event["resolution"] = "unresolved_static_symbol"
        if event["kind"] == "line":
            candidates = [node for node in nodes
                          if node.kind in {"statement", "block"}
                          and node.location.start_line == event["line"]]
            if len(candidates) == 1:
                event["line_node_id"] = candidates[0].id
            elif candidates:
                event["line_node_candidates"] = sorted(node.id for node in candidates)


def _child(root: Path, output: Path, max_events: int, script: Path, arguments: list[str]) -> int:
    recorder = _TraceRecorder(root.resolve(strict=True), max_events)
    exit_code = 0
    sys.argv = [str(script), *arguments]
    sys.path.insert(0, str(root))
    sys.settrace(recorder.trace)
    threading.settrace(recorder.trace)
    try:
        runpy.run_path(str(script), run_name="__main__")
    except SystemExit as error:
        exit_code = error.code if isinstance(error.code, int) else int(bool(error.code))
    except KeyboardInterrupt:
        exit_code = 130
    except BaseException:
        traceback.print_exc()
        exit_code = 1
    finally:
        sys.settrace(None)
        threading.settrace(None)
        output.write_text(json.dumps({"files": recorder.files, "events": recorder.events,
                                      "truncated": recorder.truncated}, ensure_ascii=False),
                          encoding="utf-8")
    return exit_code


if __name__ == "__main__":
    if len(sys.argv) < 6 or sys.argv[1] != "--child":
        raise SystemExit("Dreamwalk recording is started through the 'dreamwalk record' command")
    raise SystemExit(_child(Path(sys.argv[2]), Path(sys.argv[3]), int(sys.argv[4]),
                            Path(sys.argv[5]), sys.argv[6:]))
