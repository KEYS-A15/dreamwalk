"""Language-neutral graph contract. All locations use relative paths."""

from dataclasses import asdict, dataclass, field


@dataclass
class Location:
    path: str
    start_line: int
    end_line: int
    start_column: int = 0
    end_column: int = 0
    column_unit: str = "utf8_bytes"


@dataclass
class Node:
    id: str
    kind: str
    name: str
    qualified_name: str
    language: str
    location: Location
    parent_id: str | None
    facts: dict = field(default_factory=dict)


@dataclass
class Edge:
    source: str
    target: str | None
    kind: str
    evidence: Location
    status: str = "syntax"
    expression: str = ""
    reason: str = ""
    details: dict = field(default_factory=dict)


@dataclass
class Graph:
    schema_version: str = "1.1"
    analyzer_version: str = "0.2.0"
    languages: list[str] = field(default_factory=lambda: ["python"])
    files: list[dict] = field(default_factory=list)
    nodes: list[Node] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    diagnostics: list[dict] = field(default_factory=list)
    limitations: list[str] = field(default_factory=lambda: [
        "Structural blocks and source order are not a complete control-flow graph.",
        "Call targets are lexical candidates, not proof of runtime dispatch.",
        "No target code is imported or executed; there are no recorded events.",
        "Dynamic dispatch, inheritance and external call targets remain unresolved.",
        "Decorators, defaults and annotations are attributed to their enclosing scope.",
        "Source snippets and docstrings may contain sensitive data; review before sharing.",
    ])

    def to_dict(self) -> dict:
        return asdict(self)

    def node(self, node_id: str) -> Node:
        for node in self.nodes:
            if node.id == node_id:
                return node
        raise KeyError(node_id)

    def focus(self, node_id: str, depth: int = 1) -> dict:
        self.node(node_id)
        if not 0 <= depth <= 5:
            raise ValueError("depth must be between 0 and 5")
        selected = {node_id}
        for _ in range(depth):
            expanded = set(selected)
            for edge in self.edges:
                if edge.source in selected and edge.target:
                    expanded.add(edge.target)
                if edge.target in selected:
                    expanded.add(edge.source)
            selected = expanded
        result = self.to_dict()
        result["nodes"] = [asdict(n) for n in self.nodes if n.id in selected]
        result["edges"] = [asdict(e) for e in self.edges
                           if e.source in selected and (e.target is None or e.target in selected)]
        return result

    def explain(self, node_id: str) -> dict:
        node = self.node(node_id)
        calls = [asdict(e) for e in self.edges if e.source == node_id and e.kind == "calls"]
        children = [n.id for n in self.nodes if n.parent_id == node_id]
        return {
            "node_id": node_id,
            "provenance": "deterministic_ast_facts",
            "summary": f"{node.kind.capitalize()} {node.qualified_name} spans lines "
                       f"{node.location.start_line}–{node.location.end_line} in {node.location.path}.",
            "author_docstring": node.facts.get("docstring"),
            "facts": node.facts,
            "evidence": asdict(node.location),
            "children": children,
            "calls": calls,
            "limitations": self.limitations,
        }
