import argparse
import json
import sys
from pathlib import Path

from .analyzer import PythonAnalyzer
from .server import create_server


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Dreamwalk: inspect Python code without running it")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("analyze", "explain", "focus", "serve"):
        command = sub.add_parser(name)
        command.add_argument("root", type=Path)
        command.add_argument("--include-source", action="store_true",
                             help="Embed source snippets; review exports before publishing")
        command.add_argument("--max-files", type=int, default=5000)
        if name == "analyze":
            command.add_argument("--output", type=Path)
            command.add_argument("--strict", action="store_true", help="Exit 1 if analysis has diagnostics")
        if name in {"explain", "focus"}:
            command.add_argument("--id", required=True)
        if name == "focus":
            command.add_argument("--depth", type=int, default=1)
        if name == "serve":
            command.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    try:
        graph = PythonAnalyzer().analyze(args.root, include_source=args.include_source,
                                         max_files=args.max_files)
        if args.command == "serve":
            with create_server(graph, args.port) as server:
                print(f"Dreamwalk API: http://127.0.0.1:{server.server_port}/graph", flush=True)
                print("Graph is a startup snapshot. Restart after code changes.", flush=True)
                server.serve_forever()
            return 0
        result = (graph.explain(args.id) if args.command == "explain" else
                  graph.focus(args.id, args.depth) if args.command == "focus" else graph.to_dict())
        content = json.dumps(result, indent=2, ensure_ascii=False) + "\n"
        if args.command == "analyze" and args.output:
            # Exclusive create avoids overwriting repository files or earlier exports.
            with args.output.open("x", encoding="utf-8") as handle:
                handle.write(content)
            print(f"Wrote {args.output}: {len(graph.nodes)} nodes, {len(graph.edges)} edges, "
                  f"{len(graph.diagnostics)} diagnostics")
        else:
            print(content)
        return int(args.command == "analyze" and args.strict and bool(graph.diagnostics))
    except (OSError, ValueError, KeyError) as error:
        print(f"Dreamwalk: {error}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
