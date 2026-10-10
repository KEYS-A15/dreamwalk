"""Parse Python source without importing it. Preserve uncertainty."""

import ast
import hashlib
import io
import os
import sys
import tokenize
from pathlib import Path

from .model import Edge, Graph, Location, Node

DEFINITIONS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
SCOPES = (ast.Module, *DEFINITIONS, ast.Lambda)
BLOCKS = (ast.If, ast.For, ast.AsyncFor, ast.While, ast.Try, ast.TryStar,
          ast.With, ast.AsyncWith, ast.Match, ast.ExceptHandler, ast.IfExp,
          ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp, ast.Lambda)
IGNORED = {".git", ".venv", "venv", "__pycache__", "node_modules", ".mypy_cache",
           ".pytest_cache", "dist", "build", ".tox", ".ruff_cache"}


class PythonAnalyzer:
    language = "python"

    def analyze(self, root: Path, *, include_source: bool = False,
                max_files: int = 5000, max_bytes: int = 2_000_000) -> Graph:
        root = root.resolve(strict=True)
        if not root.is_dir():
            raise ValueError("Repository root must be a directory")
        if max_files < 1 or max_bytes < 1:
            raise ValueError("Limits must be positive")
        graph = Graph()
        seen = 0
        # Do not traverse symlink directories or read symlink files.
        for directory, dirs, files in os.walk(root, followlinks=False):
            dirs[:] = sorted(d for d in dirs if d not in IGNORED
                             and not (Path(directory) / d).is_symlink()
                             and (Path(directory) / d).resolve().is_relative_to(root))
            for name in sorted(files):
                path = Path(directory) / name
                if path.suffix != ".py" or path.is_symlink():
                    continue
                relative = path.relative_to(root).as_posix()
                if not path.resolve().is_relative_to(root):
                    continue
                seen += 1
                if seen > max_files:
                    graph.diagnostics.append({"code": "file_limit", "message": "File limit reached"})
                    _link_imports(graph)
                    _link_calls(graph)
                    return graph
                try:
                    raw = path.read_bytes() if path.stat().st_size <= max_bytes else None
                    if raw is None or len(raw) > max_bytes:
                        graph.diagnostics.append({"path": relative, "code": "file_size",
                                                  "message": "File exceeds byte limit"})
                        continue
                    # tokenize honors Python encoding cookies, including Latin-1.
                    encoding, _ = tokenize.detect_encoding(io.BytesIO(raw).readline)
                    source = raw.decode(encoding)
                    tree = ast.parse(source, filename=relative)
                    graph.files.append({"path": relative, "sha256": hashlib.sha256(raw).hexdigest(),
                                        "language": "python", "parser_python": sys.version.split()[0]})
                    _Visitor(graph, relative, source, include_source).visit(tree)
                    _ControlFlow(graph, relative, source, include_source).build(tree)
                except (OSError, UnicodeError, SyntaxError, RecursionError, ValueError) as error:
                    graph.diagnostics.append({"path": relative, "code": "parse_error",
                                              "message": str(error), "line": getattr(error, "lineno", None)})
        if not graph.files and not graph.diagnostics:
            graph.diagnostics.append({"code": "no_python_files", "message": "No Python files found"})
        _link_imports(graph)
        _link_calls(graph)
        return graph


def _module_index(graph: Graph) -> dict[str, set[str]]:
    modules = {}
    for node in graph.nodes:
        if node.kind != "module":
            continue
        parts = node.location.path.removesuffix(".py").split("/")
        variants = [parts, parts[1:]] if parts[0] == "src" else [parts]
        for variant in variants:
            key = ".".join(variant[:-1] if variant[-1] == "__init__" else variant)
            modules.setdefault(key, set()).add(node.id)
    return modules


def _absolute_module(graph: Graph, source_id: str, expression: str) -> str | None:
    if not expression.startswith("."):
        return expression
    level = len(expression) - len(expression.lstrip("."))
    parts = graph.node(source_id).location.path.removesuffix(".py").split("/")
    if parts[0] == "src":
        parts = parts[1:]
    package = parts[:-1]
    if level > len(package):
        return None
    prefix = package[:len(package) - level + 1]
    return ".".join(prefix + ([expression[level:]] if expression[level:] else []))


def _link_imports(graph: Graph):
    """Connect unambiguous local import modules, including conventional src layouts."""
    modules = _module_index(graph)
    for edge in graph.edges:
        if edge.kind != "imports":
            continue
        target = _absolute_module(graph, edge.source, edge.expression)
        if target is None:
            edge.reason = "Relative import escapes the analyzed package"
            continue
        candidates = modules.get(target, set())
        if len(candidates) == 1:
            edge.target = next(iter(candidates))
            edge.status = "static_candidate"
            edge.reason = "Local module path candidate; runtime import search path may differ"


def _link_calls(graph: Graph):
    """Resolve only unambiguous calls through statically linked local imports."""
    modules = _module_index(graph)
    exports = {}
    for node in graph.nodes:
        if node.parent_id and node.kind in {"function", "class"}:
            parent = graph.node(node.parent_id)
            if parent.kind == "module":
                exports.setdefault((parent.id, node.name), set()).add(node.id)
    for _ in graph.nodes:
        changed = False
        for edge in graph.edges:
            if (edge.kind != "imports" or not edge.target or
                    graph.node(edge.source).kind != "module"):
                continue
            symbol = edge.details.get("symbol")
            binding = edge.details.get("binding")
            if not symbol or symbol == "*" or not binding:
                continue
            candidates = exports.get((edge.target, symbol), set())
            destination = exports.setdefault((edge.source, binding), set())
            before = len(destination)
            destination.update(candidates)
            changed |= len(destination) != before
        if not changed:
            break
    for edge in graph.edges:
        binding = edge.details.get("import_binding") if edge.kind == "calls" else None
        if not binding:
            continue
        module_name = _absolute_module(graph, edge.source, binding["module"])
        members = edge.details.get("member_path", [])
        symbol = binding.get("symbol")
        if binding["kind"] == "module":
            tail = binding.get("tail", [])
            if members[:len(tail)] != tail:
                continue
            members = members[len(tail):]
            if len(members) != 1:
                continue
            symbol, members = members[0], []
        elif members:
            base_modules = modules.get(module_name, set()) if module_name else set()
            if any(exports.get((module_id, symbol)) for module_id in base_modules):
                continue
            module_name = f"{module_name}.{symbol}" if module_name else None
            symbol, members = members[0], members[1:]
        if module_name is None or members or not symbol:
            continue
        module_candidates = modules.get(module_name, set())
        candidates = {target for module_id in module_candidates
                      for target in exports.get((module_id, symbol), set())}
        if len(module_candidates) == 1 and len(candidates) == 1:
            edge.target = next(iter(candidates))
            edge.status = "static_candidate"
            edge.reason = ("Unambiguous local imported definition; control flow, runtime rebinding, "
                           "decorators and import search paths may alter dispatch")


class _ControlFlow:
    """Add conservative possible control transfers without claiming execution."""

    def __init__(self, graph: Graph, path: str, source: str, include_source: bool):
        self.graph, self.path, self.source = graph, path, source
        self.include_source = include_source

    def location(self, node: ast.AST) -> Location:
        return Location(self.path, node.lineno, node.end_lineno,
                        node.col_offset, node.end_col_offset)

    def build(self, tree: ast.AST):
        for definition in ast.walk(tree):
            if not isinstance(definition, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            owner = self.graph.node(self.node_id(definition, "function"))
            exit_node = self.add_exit(owner)
            entry = self.sequence(definition.body, owner, exit_node.id)
            if entry:
                self.flow(owner.id, entry, owner.location, "entry")
        statements = [node for node in self.graph.nodes
                      if node.kind == "statement" and node.location.path == self.path]
        for edge in self.graph.edges:
            if edge.kind != "calls" or edge.evidence.path != self.path:
                continue
            candidates = [node for node in statements
                          if (node.location.start_line, node.location.start_column)
                          <= (edge.evidence.start_line, edge.evidence.start_column)
                          and (edge.evidence.end_line, edge.evidence.end_column)
                          <= (node.location.end_line, node.location.end_column)]
            if candidates:
                edge.source = min(candidates,
                                  key=lambda node: node.location.end_line - node.location.start_line).id

    def sequence(self, statements: list[ast.stmt], parent: Node, successor: str | None,
                 *, break_target: str | None = None, continue_target: str | None = None,
                 exception_targets: tuple[str, ...] = (), finalizer: str | None = None) -> str | None:
        entry = successor
        for statement in reversed(statements):
            if isinstance(statement, DEFINITIONS):
                continue
            if (isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant)
                    and isinstance(statement.value.value, str)):
                continue
            if isinstance(statement, ast.If):
                entry = self.if_flow(statement, entry, break_target, continue_target,
                                     exception_targets, finalizer)
                continue
            if isinstance(statement, (ast.For, ast.AsyncFor, ast.While)):
                entry = self.loop_flow(statement, entry, break_target, continue_target,
                                       exception_targets, finalizer)
                continue
            if isinstance(statement, (ast.Try, ast.TryStar)):
                entry = self.try_flow(statement, entry, break_target, continue_target,
                                      exception_targets, finalizer)
                continue
            if isinstance(statement, (ast.With, ast.AsyncWith)):
                entry = self.with_flow(statement, entry, break_target, continue_target,
                                       exception_targets, finalizer)
                continue
            if isinstance(statement, ast.Match):
                entry = self.match_flow(statement, entry, break_target, continue_target,
                                        exception_targets, finalizer)
                continue
            if isinstance(statement, BLOCKS):
                block_id = self.node_id(statement, "block")
                if entry:
                    self.flow(block_id, entry, self.location(statement), "fallthrough", "unresolved",
                              f"{type(statement).__name__} flow is outside the documented CFG subset")
                entry = block_id
                continue
            node = self.add_statement(statement, parent)
            if isinstance(statement, ast.Break):
                if finalizer or break_target:
                    self.flow(node.id, finalizer or break_target, node.location,
                              "break_via_finally" if finalizer else "break",
                              "unresolved" if finalizer else "static_candidate",
                              "Finally executes before break; the resumed destination is unresolved"
                              if finalizer else "Possible static break transfer; not observed")
            elif isinstance(statement, ast.Continue):
                if finalizer or continue_target:
                    self.flow(node.id, finalizer or continue_target, node.location,
                              "continue_via_finally" if finalizer else "continue",
                              "unresolved" if finalizer else "static_candidate",
                              "Finally executes before continue; the resumed destination is unresolved"
                              if finalizer else "Possible static continue transfer; not observed")
            elif isinstance(statement, ast.Return):
                if finalizer:
                    self.flow(node.id, finalizer, node.location, "return_via_finally", "unresolved",
                              "Finally executes before return; the resumed destination is unresolved")
            elif isinstance(statement, ast.Raise):
                for target in exception_targets:
                    self.flow(node.id, target, node.location, "raise")
                if finalizer:
                    self.flow(node.id, finalizer, node.location, "unhandled_exception_via_finally",
                              "unresolved", "Finally executes before an unmatched exception propagates")
            elif entry:
                self.flow(node.id, entry, node.location, "next")
            entry = node.id
        return entry

    def if_flow(self, statement: ast.If, successor: str | None,
                break_target: str | None, continue_target: str | None,
                exception_targets: tuple[str, ...], finalizer: str | None) -> str:
        block_id = self.node_id(statement, "block")
        block = self.graph.node(block_id)
        for label, body in (("if_true", statement.body), ("if_false", statement.orelse)):
            branch = self.branch(block_id, label)
            if branch:
                entry = self.sequence(body, branch, successor, break_target=break_target,
                                      continue_target=continue_target,
                                      exception_targets=exception_targets, finalizer=finalizer)
                self.flow(block_id, branch.id, branch.location, label)
                if entry:
                    self.flow(branch.id, entry, branch.location, "branch_entry")
            elif successor:
                self.flow(block_id, successor, block.location, label)
        return block_id

    def loop_flow(self, statement: ast.For | ast.AsyncFor | ast.While, successor: str | None,
                  break_target: str | None, continue_target: str | None,
                  exception_targets: tuple[str, ...], finalizer: str | None) -> str:
        block_id = self.node_id(statement, "block")
        block = self.graph.node(block_id)
        body = self.branch(block_id, "loop_body")
        otherwise = self.branch(block_id, "loop_else")
        if body:
            entry = self.sequence(statement.body, body, block_id, break_target=successor,
                                  continue_target=block_id, exception_targets=exception_targets,
                                  finalizer=finalizer)
            self.flow(block_id, body.id, body.location, "loop_body")
            self.flow(body.id, entry or block_id, body.location, "branch_entry")
        if otherwise:
            entry = self.sequence(statement.orelse, otherwise, successor,
                                  break_target=break_target, continue_target=continue_target,
                                  exception_targets=exception_targets, finalizer=finalizer)
            self.flow(block_id, otherwise.id, otherwise.location, "loop_complete")
            if entry:
                self.flow(otherwise.id, entry, otherwise.location, "branch_entry")
        elif successor:
            self.flow(block_id, successor, block.location, "loop_exit")
        return block_id

    def try_flow(self, statement: ast.Try | ast.TryStar, successor: str | None,
                 break_target: str | None, continue_target: str | None,
                 exception_targets: tuple[str, ...], finalizer: str | None) -> str:
        block_id = self.node_id(statement, "block")
        block = self.graph.node(block_id)
        body = self.branch(block_id, "try_body")
        handlers = tuple(branch.id for index in range(len(statement.handlers))
                         if (branch := self.branch(block_id, f"except_{index}")))
        otherwise = self.branch(block_id, "try_else")
        final = self.branch(block_id, "finally")

        if final:
            entry = self.sequence(statement.finalbody, final, successor,
                                  break_target=break_target, continue_target=continue_target,
                                  exception_targets=exception_targets, finalizer=finalizer)
            if entry:
                self.flow(final.id, entry, final.location, "branch_entry")

        normal_target = otherwise.id if otherwise else final.id if final else successor
        if body:
            entry = self.sequence(statement.body, body, normal_target,
                                  break_target=break_target, continue_target=continue_target,
                                  exception_targets=handlers or exception_targets,
                                  finalizer=final.id if final else finalizer)
            self.flow(block_id, body.id, body.location, "try_body")
            if entry:
                self.flow(body.id, entry, body.location, "branch_entry")

        if otherwise:
            entry = self.sequence(statement.orelse, otherwise, final.id if final else successor,
                                  break_target=break_target, continue_target=continue_target,
                                  exception_targets=exception_targets,
                                  finalizer=final.id if final else finalizer)
            if entry:
                self.flow(otherwise.id, entry, otherwise.location, "branch_entry")

        for index, handler in enumerate(statement.handlers):
            branch = self.branch(block_id, f"except_{index}")
            if not branch:
                continue
            entry = self.sequence(handler.body, branch, final.id if final else successor,
                                  break_target=break_target, continue_target=continue_target,
                                  exception_targets=exception_targets,
                                  finalizer=final.id if final else finalizer)
            self.flow(block_id, branch.id, branch.location, "exception_candidate")
            if entry:
                self.flow(branch.id, entry, branch.location, "branch_entry")

        if final:
            self.flow(block_id, final.id, final.location, "unhandled_exception_via_finally",
                      "unresolved", "An unmatched exception executes finally before propagating")
        elif exception_targets:
            for target in exception_targets:
                self.flow(block_id, target, block.location, "exception_propagation")
        elif not handlers:
            self.flow(block_id, None, block.location, "unhandled_exception", "unresolved",
                      "Possible exception leaves the analyzed function")
        return block_id

    def with_flow(self, statement: ast.With | ast.AsyncWith, successor: str | None,
                  break_target: str | None, continue_target: str | None,
                  exception_targets: tuple[str, ...], finalizer: str | None) -> str:
        block_id = self.node_id(statement, "block")
        block = self.graph.node(block_id)
        entry = self.sequence(statement.body, block, successor, break_target=break_target,
                              continue_target=continue_target,
                              exception_targets=exception_targets, finalizer=finalizer)
        if entry:
            self.flow(block_id, entry, block.location, "with_body")
        self.flow(block_id, None, block.location, "context_manager_exception", "unresolved",
                  "Context manager exit may suppress or propagate an exception")
        return block_id

    def match_flow(self, statement: ast.Match, successor: str | None,
                   break_target: str | None, continue_target: str | None,
                   exception_targets: tuple[str, ...], finalizer: str | None) -> str:
        block_id = self.node_id(statement, "block")
        block = self.graph.node(block_id)
        for index, case in enumerate(statement.cases):
            branch = self.branch(block_id, f"case_{index}")
            if not branch:
                continue
            entry = self.sequence(case.body, branch, successor, break_target=break_target,
                                  continue_target=continue_target,
                                  exception_targets=exception_targets, finalizer=finalizer)
            self.flow(block_id, branch.id, branch.location, f"case_{index}")
            if entry:
                self.flow(branch.id, entry, branch.location, "branch_entry")
        last = statement.cases[-1] if statement.cases else None
        if successor and not (last and last.guard is None and self.irrefutable(last.pattern)):
            self.flow(block_id, successor, block.location, "no_match")
        return block_id

    def branch(self, block_id: str, name: str) -> Node | None:
        return next((node for node in self.graph.nodes
                     if node.parent_id == block_id and node.kind == "branch" and node.name == name), None)

    def irrefutable(self, pattern: ast.pattern) -> bool:
        return ((isinstance(pattern, ast.MatchAs) and pattern.pattern is None) or
                (isinstance(pattern, ast.MatchOr) and any(self.irrefutable(item)
                                                          for item in pattern.patterns)))

    def add_statement(self, statement: ast.stmt, parent: Node) -> Node:
        location = self.location(statement)
        name = f"{type(statement).__name__}@{statement.lineno}"
        facts = {"syntax_kind": type(statement).__name__,
                 "terminal": isinstance(statement, (ast.Return, ast.Raise,
                                                       ast.Break, ast.Continue))}
        if self.include_source:
            facts["source"] = ast.get_source_segment(self.source, statement) or ""
        node = Node(self.node_id(statement, "statement"), "statement", name,
                    f"{parent.qualified_name}.{name}", "python", location, parent.id, facts)
        self.graph.nodes.append(node)
        self.graph.edges.append(Edge(parent.id, node.id, "contains", location))
        return node

    def add_exit(self, owner: Node) -> Node:
        location = Location(self.path, owner.location.end_line, owner.location.end_line,
                            owner.location.end_column, owner.location.end_column)
        node = Node(f"{self.path}:{owner.location.start_line}:{owner.location.start_column}:exit", "exit",
                    "implicit_return", f"{owner.qualified_name}.implicit_return", "python",
                    location, owner.id, {"syntax_kind": "implicit_return"})
        self.graph.nodes.append(node)
        self.graph.edges.append(Edge(owner.id, node.id, "contains", location))
        return node

    def flow(self, source: str, target: str | None, evidence: Location, expression: str,
             status: str = "static_candidate", reason: str = "Possible static control transfer; not observed"):
        self.graph.edges.append(Edge(source, target, "flows_to", evidence, status, expression, reason))

    def node_id(self, node: ast.AST, kind: str) -> str:
        return f"{self.path}:{node.lineno}:{node.col_offset}:{kind}"


class _Visitor(ast.NodeVisitor):
    def __init__(self, graph: Graph, path: str, source: str, include_source: bool):
        self.graph, self.path, self.source = graph, path, source
        self.include_source = include_source
        self.parents: list[Node] = []
        self.scopes: list[dict] = []
        self.pending: list[tuple[Edge, list[dict]]] = []

    def location(self, node: ast.AST) -> Location:
        return Location(self.path, getattr(node, "lineno", 1),
                        getattr(node, "end_lineno", len(self.source.splitlines()) or 1),
                        getattr(node, "col_offset", 0), getattr(node, "end_col_offset", 0))

    def add(self, node: ast.AST, kind: str, name: str, facts: dict) -> Node:
        loc = self.location(node)
        parent = self.parents[-1] if self.parents else None
        qualified = f"{parent.qualified_name}.{name}" if parent else name
        node_id = f"{self.path}:{loc.start_line}:{loc.start_column}:{kind}"
        if self.include_source:
            facts["source"] = ast.get_source_segment(self.source, node) or self.source
        result = Node(node_id, kind, name, qualified, "python", loc,
                      parent.id if parent else None, facts)
        self.graph.nodes.append(result)
        if parent:
            self.graph.edges.append(Edge(parent.id, result.id, "contains", loc))
        return result

    def scope(self, node: ast.AST, owner: Node) -> dict:
        symbols: dict[str, list] = {}

        def bind(name: str, target: str | None = None):
            symbols.setdefault(name, []).append(target)

        def scan(item: ast.AST):
            if isinstance(item, DEFINITIONS):
                loc = self.location(item)
                kind = "class" if isinstance(item, ast.ClassDef) else "function"
                bind(item.name, f"{self.path}:{loc.start_line}:{loc.start_column}:{kind}")
                # Defaults/decorators can bind names in the enclosing scope.
                for decorator in item.decorator_list:
                    scan(decorator)
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    for default in item.args.defaults + [x for x in item.args.kw_defaults if x]:
                        scan(default)
                return
            if isinstance(item, ast.Lambda):
                return
            if isinstance(item, ast.Name) and isinstance(item.ctx, (ast.Store, ast.Del)):
                bind(item.id)
            if isinstance(item, ast.Import):
                for alias in item.names:
                    parts = alias.name.split(".")
                    bind(alias.asname or parts[0], {"kind": "module", "module": alias.name,
                                                   "tail": [] if alias.asname else parts[1:]})
            if isinstance(item, ast.ImportFrom):
                for alias in item.names:
                    if alias.name == "*":
                        bind("*")
                    else:
                        bind(alias.asname or alias.name,
                             {"kind": "from", "module": "." * item.level + (item.module or ""),
                              "symbol": alias.name})
            if isinstance(item, ast.ExceptHandler) and item.name:
                bind(item.name)
            if isinstance(item, (ast.Global, ast.Nonlocal)):
                for name in item.names:
                    bind(name)
            if isinstance(item, (ast.MatchAs, ast.MatchStar)) and item.name:
                bind(item.name)
            if isinstance(item, ast.MatchMapping) and item.rest:
                bind(item.rest)
            for child in ast.iter_child_nodes(item):
                scan(child)

        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            for arg in node.args.posonlyargs + node.args.args + node.args.kwonlyargs:
                bind(arg.arg)
            for arg in (node.args.vararg, node.args.kwarg):
                if arg:
                    bind(arg.arg)
        body = node.body if isinstance(node.body, list) else [node.body]
        for statement in body:
            scan(statement)
        return {"symbols": symbols, "owner": owner, "class": isinstance(node, ast.ClassDef)}

    def visit_Module(self, node: ast.Module):
        owner = self.add(node, "module", self.path.removesuffix(".py").replace("/", "."),
                         {"docstring": ast.get_docstring(node)})
        self.parents.append(owner)
        self.scopes.append(self.scope(node, owner))
        self.generic_visit(node)
        self.scopes.pop()
        self.parents.pop()
        known = {n.id for n in self.graph.nodes}
        for edge, scopes in self.pending:
            parts = edge.details["callee_parts"]
            for scope in reversed(scopes):
                if scope["class"]:
                    continue
                if "*" in scope["symbols"]:
                    edge.reason = "Wildcard import may replace local bindings"
                    break
                bindings = scope["symbols"].get(parts[0])
                if bindings:
                    if len(bindings) == 1 and isinstance(bindings[0], str) and bindings[0] in known and len(parts) == 1:
                        edge.target = bindings[0]
                        edge.status = "static_candidate"
                        edge.reason = "Single lexical definition; runtime rebinding/decorators may alter dispatch"
                    elif len(bindings) == 1 and isinstance(bindings[0], dict):
                        edge.details["import_binding"] = bindings[0]
                        edge.details["member_path"] = parts[1:]
                        edge.reason = "Imported binding awaits local module and symbol resolution"
                    else:
                        edge.reason = "Name is shadowed, rebound, imported or ambiguously defined"
                    break

    def visit_definition(self, node):
        is_class = isinstance(node, ast.ClassDef)
        # Definition-time expressions belong to the enclosing execution context.
        for decorator in node.decorator_list:
            self.visit(decorator)
        if is_class:
            for expr in node.bases + [k.value for k in node.keywords]:
                self.visit(expr)
        else:
            self.visit(node.args)
            if node.returns:
                self.visit(node.returns)
        facts = {"docstring": ast.get_docstring(node),
                 "decorators": [ast.unparse(d) for d in node.decorator_list]}
        if is_class:
            facts["bases"] = [ast.unparse(b) for b in node.bases]
        else:
            facts.update({"parameters": ast.unparse(node.args),
                          "return_annotation": ast.unparse(node.returns) if node.returns else None,
                          "async": isinstance(node, ast.AsyncFunctionDef),
                          "returns": [], "raises": [], "assignments": [], "awaits": []})
        owner = self.add(node, "class" if is_class else "function", node.name, facts)
        self.parents.append(owner)
        self.scopes.append(self.scope(node, owner))
        for statement in node.body:
            self.visit(statement)
        self.scopes.pop()
        self.parents.pop()

    visit_FunctionDef = visit_definition
    visit_AsyncFunctionDef = visit_definition
    visit_ClassDef = visit_definition

    def generic_visit(self, node):
        if isinstance(node, BLOCKS):
            facts = {"syntax_kind": type(node).__name__}
            for field in ("test", "iter", "subject", "type"):
                value = getattr(node, field, None)
                if value is not None:
                    facts[field] = ast.unparse(value)
            groups = self.branch_groups(node)
            facts["body_groups"] = [label for label, _ in groups]
            owner = self.add(node, "block", f"{type(node).__name__}@{node.lineno}", facts)
            self.parents.append(owner)
            # ponytail: calls in implicit scopes stay unresolved until scope/closure analysis exists.
            implicit = isinstance(node, (ast.Lambda, ast.ListComp, ast.SetComp,
                                         ast.DictComp, ast.GeneratorExp))
            if implicit:
                self.scopes.append({"symbols": {}, "owner": owner, "class": False, "implicit": True})
            if groups:
                grouped = ({"body", "orelse"} if isinstance(node, ast.IfExp) else
                           {field for field in ("body", "orelse", "handlers", "finalbody", "cases")
                            if hasattr(node, field)})
                for field, value in ast.iter_fields(node):
                    if field in grouped:
                        continue
                    if isinstance(value, ast.AST):
                        self.visit(value)
                    elif isinstance(value, list):
                        for item in value:
                            if isinstance(item, ast.AST):
                                self.visit(item)
                for order, (label, items) in enumerate(groups):
                    branch = self.add_branch(owner, label, order, items)
                    self.parents.append(branch)
                    for item in items:
                        self.visit(item)
                    self.parents.pop()
            else:
                super().generic_visit(node)
            if implicit:
                self.scopes.pop()
            self.parents.pop()
        else:
            super().generic_visit(node)

    def visit_Call(self, node: ast.Call):
        parts = self.call_parts(node.func)
        edge = Edge(self.parents[-1].id, None, "calls", self.location(node), "unresolved",
                    ast.unparse(node.func), "External or dynamic target; no runtime observation",
                    {"callee_parts": parts} if parts else {})
        self.graph.edges.append(edge)
        if parts and not any(s.get("implicit") for s in self.scopes):
            self.pending.append((edge, list(self.scopes)))
        self.generic_visit(node)

    @staticmethod
    def call_parts(node: ast.AST) -> list[str]:
        parts = []
        while isinstance(node, ast.Attribute):
            parts.append(node.attr)
            node = node.value
        return [node.id, *reversed(parts)] if isinstance(node, ast.Name) else []

    def branch_groups(self, node: ast.AST) -> list[tuple[str, list[ast.AST]]]:
        groups = []
        if isinstance(node, ast.If):
            groups = [("if_true", node.body), ("if_false", node.orelse)]
        elif isinstance(node, (ast.For, ast.AsyncFor, ast.While)):
            groups = [("loop_body", node.body), ("loop_else", node.orelse)]
        elif isinstance(node, (ast.Try, ast.TryStar)):
            groups = [("try_body", node.body)]
            groups += [(f"except_{index}", [handler]) for index, handler in enumerate(node.handlers)]
            groups += [("try_else", node.orelse), ("finally", node.finalbody)]
        elif isinstance(node, ast.Match):
            groups = [(f"case_{index}", [case]) for index, case in enumerate(node.cases)]
        elif isinstance(node, ast.IfExp):
            groups = [("if_true", [node.body]), ("if_false", [node.orelse])]
        return [(label, items) for label, items in groups if items]

    def add_branch(self, owner: Node, label: str, order: int, items: list[ast.AST]) -> Node:
        first = items[0].pattern if isinstance(items[0], ast.match_case) else items[0]
        last_item = items[-1]
        last = (last_item.body[-1] if isinstance(last_item, ast.match_case) and last_item.body
                else last_item.pattern if isinstance(last_item, ast.match_case) else last_item)
        start, end = self.location(first), self.location(last)
        loc = Location(self.path, start.start_line, end.end_line,
                       start.start_column, end.end_column)
        branch = Node(f"{self.path}:{loc.start_line}:{loc.start_column}:branch:{order}", "branch", label,
                      f"{owner.qualified_name}.{label}", "python", loc, owner.id,
                      {"role": label, "order": order})
        self.graph.nodes.append(branch)
        self.graph.edges.append(Edge(owner.id, branch.id, "branches", loc, expression=label,
                                     reason="Syntactic branch group; execution is not observed"))
        return branch

    def record_fact(self, node, key):
        owner = self.scopes[-1]["owner"]
        if key in owner.facts:
            owner.facts[key].append({"expression": ast.unparse(node),
                                     "line": node.lineno})
        self.generic_visit(node)

    def visit_Return(self, node):
        self.record_fact(node, "returns")

    def visit_Raise(self, node):
        self.record_fact(node, "raises")

    def visit_Assign(self, node):
        self.record_fact(node, "assignments")

    visit_AnnAssign = visit_Assign
    visit_AugAssign = visit_Assign

    def visit_Await(self, node):
        self.record_fact(node, "awaits")

    def visit_Import(self, node):
        for alias in node.names:
            self.graph.edges.append(Edge(self.parents[-1].id, None, "imports", self.location(node),
                                         "syntax", alias.name, "Import statement; module not loaded",
                                         {"binding": alias.asname or alias.name.split(".")[0]}))

    def visit_ImportFrom(self, node):
        expression = "." * node.level + (node.module or "")
        for alias in node.names:
            self.graph.edges.append(Edge(self.parents[-1].id, None, "imports", self.location(node),
                                         "syntax", expression, "Import statement; module not loaded",
                                         {"binding": alias.asname or alias.name, "symbol": alias.name}))
