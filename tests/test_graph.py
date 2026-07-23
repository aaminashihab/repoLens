"""Tests for repository call-graph and dependency graph engine."""

import pytest
from app.core.graph import CodeNode, GraphEdge, RepositoryGraph


def test_graph_node_and_edge_management():
    graph = RepositoryGraph()
    node1 = CodeNode(
        node_id="app/auth.py::login",
        file_path="app/auth.py",
        symbol_name="login",
        symbol_type="function",
        start_line=10,
        end_line=25,
        content="def login(): verify_token()",
    )
    node2 = CodeNode(
        node_id="app/tokens.py::verify_token",
        file_path="app/tokens.py",
        symbol_name="verify_token",
        symbol_type="function",
        start_line=5,
        end_line=15,
        content="def verify_token(): pass",
    )

    graph.add_node(node1)
    graph.add_node(node2)
    graph.add_edge("app/auth.py::login", "app/tokens.py::verify_token", "calls")

    assert len(graph.nodes) == 2
    assert len(graph.edges) == 1

    callees = graph.get_callees("app/auth.py::login")
    assert len(callees) == 1
    assert callees[0].symbol_name == "verify_token"

    callers = graph.get_callers("app/tokens.py::verify_token")
    assert len(callers) == 1
    assert callers[0].symbol_name == "login"


def test_graph_n_hop_traversal():
    graph = RepositoryGraph()
    n1 = CodeNode("a.py::f1", "a.py", "f1", "function", 1, 5, "def f1(): f2()")
    n2 = CodeNode("b.py::f2", "b.py", "f2", "function", 1, 5, "def f2(): f3()")
    n3 = CodeNode("c.py::f3", "c.py", "f3", "function", 1, 5, "def f3(): pass")

    graph.add_node(n1)
    graph.add_node(n2)
    graph.add_node(n3)

    graph.add_edge("a.py::f1", "b.py::f2", "calls")
    graph.add_edge("b.py::f2", "c.py::f3", "calls")

    # 1 hop from f1 -> f1, f2
    hops1 = graph.traverse_n_hops(["a.py::f1"], max_depth=1)
    assert {n.symbol_name for n in hops1} == {"f1", "f2"}

    # 2 hops from f1 -> f1, f2, f3
    hops2 = graph.traverse_n_hops(["a.py::f1"], max_depth=2)
    assert {n.symbol_name for n in hops2} == {"f1", "f2", "f3"}


def test_graph_serialization_roundtrip():
    graph = RepositoryGraph()
    node = CodeNode("main.py::run", "main.py", "run", "function", 1, 10, "def run(): pass")
    graph.add_node(node)

    as_dict = graph.to_dict()
    reconstructed = RepositoryGraph.from_dict(as_dict)

    assert "main.py::run" in reconstructed.nodes
    assert reconstructed.nodes["main.py::run"].symbol_name == "run"
