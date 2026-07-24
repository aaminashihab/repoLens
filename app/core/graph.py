"""In-memory Code Graph (Call Graph & Dependency Graph) built during AST chunking."""

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class CodeNode:
    """Represents a symbol or file node in the repository graph."""

    node_id: str  # Format: "file_path::symbol_name" or "file_path"
    file_path: str
    symbol_name: str
    symbol_type: str  # "function", "method", "class", "module"
    start_line: int
    end_line: int
    content: str
    docstring: str = ""


@dataclass
class GraphEdge:
    """Represents a directional relationship edge (caller->callee, module->imports, class->inherits)."""

    source_id: str
    target_id: str
    relation_type: str  # "calls", "imports", "contains", "inherits"


class RepositoryGraph:
    """A graph structure tracking symbol relationships across a repository."""

    def __init__(self) -> None:
        self.nodes: dict[str, CodeNode] = {}
        self.edges: list[GraphEdge] = []
        self._adjacency_out: dict[str, list[tuple[str, str]]] = {}  # source -> list of (target, relation_type)
        self._adjacency_in: dict[str, list[tuple[str, str]]] = {}   # target -> list of (source, relation_type)
        self._symbol_lookup: dict[str, list[str]] = {}             # symbol_name -> list of node_ids

    def add_node(self, node: CodeNode) -> None:
        """Add a CodeNode to the repository graph."""
        self.nodes[node.node_id] = node
        if node.symbol_name:
            existing = self._symbol_lookup.setdefault(node.symbol_name, [])
            # BUG-10 FIX: Deduplicate node_ids in symbol_lookup — calling add_node twice
            # with the same node_id previously caused duplicate lookup entries.
            if node.node_id not in existing:
                existing.append(node.node_id)

    def add_edge(self, source_id: str, target_id: str, relation_type: str) -> None:
        """Add a directional relation edge between two nodes."""
        edge = GraphEdge(source_id=source_id, target_id=target_id, relation_type=relation_type)
        self.edges.append(edge)
        self._adjacency_out.setdefault(source_id, []).append((target_id, relation_type))
        self._adjacency_in.setdefault(target_id, []).append((source_id, relation_type))

    def get_node(self, node_id: str) -> CodeNode | None:
        """Retrieve node by node_id."""
        return self.nodes.get(node_id)

    def find_nodes_by_symbol(self, symbol_name: str) -> list[CodeNode]:
        """Lookup nodes matching a symbol name."""
        node_ids = self._symbol_lookup.get(symbol_name, [])
        return [self.nodes[nid] for nid in node_ids if nid in self.nodes]

    def get_callees(self, source_id: str) -> list[CodeNode]:
        """Return nodes called directly by `source_id`."""
        outgoing = self._adjacency_out.get(source_id, [])
        callee_ids = [target for target, rel in outgoing if rel == "calls"]
        return [self.nodes[cid] for cid in callee_ids if cid in self.nodes]

    def get_callers(self, target_id: str) -> list[CodeNode]:
        """Return nodes that call `target_id`."""
        incoming = self._adjacency_in.get(target_id, [])
        caller_ids = [src for src, rel in incoming if rel == "calls"]
        return [self.nodes[cid] for cid in caller_ids if cid in self.nodes]

    def traverse_n_hops_with_depth(
        self, start_node_ids: list[str], max_depth: int = 2
    ) -> list[tuple[CodeNode, int]]:
        """Traverse outbound and inbound relations up to `max_depth` hops, returning (node, depth)."""
        visited: set[str] = set()
        queue: list[tuple[str, int]] = [(nid, 0) for nid in start_node_ids if nid in self.nodes]
        result: list[tuple[CodeNode, int]] = []

        while queue:
            curr_id, depth = queue.pop(0)
            if curr_id in visited:
                continue
            visited.add(curr_id)
            if curr_id in self.nodes:
                result.append((self.nodes[curr_id], depth))

            if depth < max_depth:
                out_neighbors = [t for t, _ in self._adjacency_out.get(curr_id, [])]
                in_neighbors = [s for s, _ in self._adjacency_in.get(curr_id, [])]
                for nxt in out_neighbors + in_neighbors:
                    if nxt not in visited:
                        queue.append((nxt, depth + 1))

        return result

    def traverse_n_hops(self, start_node_ids: list[str], max_depth: int = 2) -> list[CodeNode]:
        """Traverse outbound and inbound relations up to `max_depth` hops."""
        return [node for node, _ in self.traverse_n_hops_with_depth(start_node_ids, max_depth)]

    def to_dict(self) -> dict[str, Any]:
        """Serialize graph to dictionary for storage."""
        return {
            "nodes": [
                {
                    "node_id": n.node_id,
                    "file_path": n.file_path,
                    "symbol_name": n.symbol_name,
                    "symbol_type": n.symbol_type,
                    "start_line": n.start_line,
                    "end_line": n.end_line,
                    "content": n.content,
                    "docstring": n.docstring,
                }
                for n in self.nodes.values()
            ],
            "edges": [
                {
                    "source_id": e.source_id,
                    "target_id": e.target_id,
                    "relation_type": e.relation_type,
                }
                for e in self.edges
            ],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RepositoryGraph":
        """Deserialize repository graph from dictionary."""
        graph = cls()
        for ndata in data.get("nodes", []):
            node = CodeNode(
                node_id=ndata["node_id"],
                file_path=ndata["file_path"],
                symbol_name=ndata["symbol_name"],
                symbol_type=ndata["symbol_type"],
                start_line=ndata["start_line"],
                end_line=ndata["end_line"],
                content=ndata["content"],
                docstring=ndata.get("docstring", ""),
            )
            graph.add_node(node)

        for edata in data.get("edges", []):
            graph.add_edge(
                source_id=edata["source_id"],
                target_id=edata["target_id"],
                relation_type=edata["relation_type"],
            )
        return graph
