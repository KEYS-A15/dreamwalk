import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from dreamwalk.analyzer import PythonAnalyzer
from dreamwalk.cli import main
from dreamwalk.server import create_server


class AnalyzerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)

    def analyze(self, source, **kwargs):
        (self.root / "app.py").write_text(source, encoding="utf-8")
        return PythonAnalyzer().analyze(self.root, **kwargs)

    def test_local_call_and_evidence(self):
        graph = self.analyze("def helper(x):\n    return x + 1\ndef run():\n    return helper(2)\n")
        edge = next(e for e in graph.edges if e.kind == "calls")
        self.assertEqual(edge.status, "static_candidate")
        self.assertEqual(graph.node(edge.target).name, "helper")
        self.assertEqual(edge.evidence.start_line, 4)
        self.assertEqual(len(graph.files[0]["sha256"]), 64)
        self.assertNotIn("source", graph.nodes[0].facts)

    def test_nested_scope_isolation(self):
        graph = self.analyze("def outer():\n    def hidden():\n        return 1\n    return hidden()\ndef other():\n    return hidden()\n")
        calls = [e for e in graph.edges if e.kind == "calls"]
        self.assertEqual([e.status for e in calls], ["static_candidate", "unresolved"])

    def test_parameters_and_rebinding_block_resolution(self):
        graph = self.analyze("def helper(): pass\ndef a(helper): return helper()\ndef b():\n    helper = lambda: 2\n    return helper()\n")
        self.assertTrue(all(e.target is None for e in graph.edges if e.kind == "calls"))

    def test_duplicate_definitions_unresolved(self):
        graph = self.analyze("def helper(): pass\ndef helper(): pass\nhelper()\n")
        self.assertIsNone(next(e for e in graph.edges if e.kind == "calls").target)

    def test_wildcard_import_prevents_false_candidate(self):
        graph = self.analyze("def helper(): pass\nfrom external import *\nhelper()\n")
        self.assertIsNone(next(e for e in graph.edges if e.kind == "calls").target)

    def test_method_names_do_not_leak_into_lexical_scope(self):
        graph = self.analyze("class A:\n    def helper(self): pass\n    def run(self):\n        helper()\n        self.helper()\n")
        self.assertTrue(all(e.target is None for e in graph.edges if e.kind == "calls"))

    def test_definition_time_calls_belong_to_module(self):
        graph = self.analyze("def factory(): return 1\ndef f(x=factory()):\n    return x\n")
        call = next(e for e in graph.edges if e.kind == "calls")
        self.assertEqual(graph.node(call.source).kind, "module")

    def test_nested_returns_and_branches(self):
        graph = self.analyze("def outer(x):\n    def inner(): return 3\n    if x:\n        return 1\n    else:\n        return 2\n")
        outer = next(n for n in graph.nodes if n.name == "outer")
        self.assertEqual(len(outer.facts["returns"]), 2)
        self.assertTrue(any(n.kind == "block" for n in graph.nodes))
        branches = [n for n in graph.nodes if n.kind == "branch"]
        self.assertEqual([n.facts["role"] for n in branches], ["if_true", "if_false"])
        self.assertTrue(all(any(e.kind == "branches" and e.target == branch.id
                                for e in graph.edges) for branch in branches))

    def test_async_and_exception_facts(self):
        graph = self.analyze("async def run(x):\n    await x()\n    raise ValueError('bad')\n")
        node = next(n for n in graph.nodes if n.name == "run")
        self.assertTrue(node.facts["async"])
        self.assertEqual(len(node.facts["awaits"]), 1)
        self.assertEqual(len(node.facts["raises"]), 1)

    def test_try_and_match_branch_roles(self):
        graph = self.analyze("def run(x):\n    try:\n        return x\n    except ValueError:\n        return 0\n    finally:\n        x = 1\n    match x:\n        case 1:\n            return 1\n")
        roles = {node.facts["role"] for node in graph.nodes if node.kind == "branch"}
        self.assertEqual(roles, {"try_body", "except_0", "finally", "case_0"})

    def test_implicit_scope_unresolved(self):
        graph = self.analyze("def helper(): pass\nf = lambda: helper()\nx = [helper() for helper in []]\n")
        self.assertTrue(all(e.target is None for e in graph.edges if e.kind == "calls"))

    def test_syntax_error_does_not_discard_other_files(self):
        (self.root / "bad.py").write_text("def broken(\n", encoding="utf-8")
        graph = self.analyze("x = 1\n")
        self.assertEqual(len(graph.files), 1)
        self.assertEqual(graph.diagnostics[0]["code"], "parse_error")

    def test_no_execution_and_ignored_directories(self):
        ignored = self.root / ".venv"
        ignored.mkdir()
        (ignored / "ignored.py").write_text("x=1", encoding="utf-8")
        marker = self.root / "EXECUTED"
        graph = self.analyze(f"from pathlib import Path\nPath({str(marker)!r}).touch()\n")
        self.assertFalse(marker.exists())
        self.assertEqual(len(graph.files), 1)

    def test_cross_file_import_links_and_relative_src_layout(self):
        package = self.root / "src" / "shop"
        package.mkdir(parents=True)
        (package / "__init__.py").write_text("", encoding="utf-8")
        (package / "helper.py").write_text("def f(): return 1", encoding="utf-8")
        (package / "service.py").write_text("from .helper import f\nf()", encoding="utf-8")
        graph = self.analyze("import shop.service\n")
        imports = [e for e in graph.edges if e.kind == "imports"]
        self.assertTrue(all(e.target is not None for e in imports))
        self.assertEqual({graph.node(e.target).location.path for e in imports},
                         {"src/shop/helper.py", "src/shop/service.py"})
        call = next(e for e in graph.edges if e.kind == "calls")
        self.assertEqual(graph.node(call.target).qualified_name, "src.shop.helper.f")
        self.assertEqual(call.status, "static_candidate")

    def test_cross_file_qualified_call_and_rebinding(self):
        (self.root / "helper.py").write_text("def f(): return 1", encoding="utf-8")
        graph = self.analyze("import helper as h\nh.f()\n")
        call = next(e for e in graph.edges if e.kind == "calls")
        self.assertEqual(graph.node(call.target).qualified_name, "helper.f")
        graph = self.analyze("import helper as h\nh.f()\nh = object()\nh.f()\n")
        calls = [e for e in graph.edges if e.kind == "calls"]
        self.assertTrue(all(edge.target is None for edge in calls))

    def test_encoding_and_unicode_source_span(self):
        (self.root / "latin.py").write_bytes(b"# coding: latin-1\nx = 'caf\xe9'\n")
        graph = self.analyze("def café():\n    return '日本'\n", include_source=True)
        self.assertEqual(len(graph.files), 2)
        self.assertIn("日本", next(n for n in graph.nodes if n.name == "café").facts["source"])

    def test_deterministic_graph_and_focus(self):
        graph = self.analyze("def a(): return b()\ndef b(): return 1\n")
        self.assertEqual(graph.to_dict(), PythonAnalyzer().analyze(self.root).to_dict())
        node = next(n for n in graph.nodes if n.name == "a")
        self.assertEqual(len(graph.focus(node.id, 0)["nodes"]), 1)
        self.assertEqual(graph.explain(node.id)["provenance"], "deterministic_ast_facts")
        with self.assertRaises(ValueError):
            graph.focus(node.id, 6)

    def test_limits_and_invalid_root(self):
        graph = self.analyze("x=1", max_bytes=1)
        self.assertEqual(graph.diagnostics[0]["code"], "file_size")
        with self.assertRaises(ValueError):
            PythonAnalyzer().analyze(self.root / "app.py")

    def test_cli_export_does_not_overwrite(self):
        self.analyze("x=1")
        output = self.root / "graph.json"
        self.assertEqual(main(["analyze", str(self.root), "--output", str(output)]), 0)
        original = output.read_bytes()
        self.assertEqual(main(["analyze", str(self.root), "--output", str(output)]), 2)
        self.assertEqual(original, output.read_bytes())

    def test_api_graph_focus_and_origin(self):
        graph = self.analyze("def f(): return 1")
        server = create_server(graph, 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{server.server_port}"
            with urlopen(base + "/graph", timeout=3) as response:
                self.assertEqual(json.load(response)["schema_version"], "1.1")
            for suffix, status in [("/focus", 404), ("/missing", 404),
                                   ("/focus?id=app.py:1:0:function&depth=10", 400)]:
                with self.assertRaises(HTTPError) as caught:
                    urlopen(base + suffix, timeout=3)
                self.assertEqual(caught.exception.code, status)
            with self.assertRaises(HTTPError) as caught:
                urlopen(Request(base + "/graph", headers={"Origin": "https://example.com"}), timeout=3)
            self.assertEqual(caught.exception.code, 403)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
