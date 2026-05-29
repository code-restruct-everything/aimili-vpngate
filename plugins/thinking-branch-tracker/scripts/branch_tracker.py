#!/usr/bin/env python3
"""
Minimal local JSON/Markdown thinking-branch tracker.

Commands:
- init: create data file
- add-branch: create a branch lane
- add-node: append a thinking node
- add-edge: connect two nodes (split/merge/depends/next/parallel)
- import-timeline: parse timeline markdown into branch-aware JSON graph
- render: generate timeline markdown + mermaid graph markdown
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA_PATH = PLUGIN_ROOT / "data" / "thinking_graph.json"
DEFAULT_TIMELINE_MD = PLUGIN_ROOT / "thinking_timeline.md"
DEFAULT_GRAPH_MD = PLUGIN_ROOT / "thinking_graph.md"
DEFAULT_IMPORT_TIMELINE = Path.cwd() / "research_thinking_timeline.md"

RELATIONS = {"next", "split_from", "merge_into", "parallel_to", "depends_on"}
EVIDENCE_LEVELS = {"verified", "observation", "hypothesis"}


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def read_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, data: Dict[str, Any]) -> None:
    ensure_parent(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    tmp.replace(path)


def empty_graph(project: str) -> Dict[str, Any]:
    return {
        "meta": {
            "project": project,
            "created_at": now_iso(),
            "updated_at": now_iso(),
            "version": "0.1.0",
        },
        "branches": [
            {
                "id": "main",
                "name": "Main",
                "parent_branch_id": None,
                "status": "active",
                "description": "Primary research line.",
                "created_at": now_iso(),
            }
        ],
        "nodes": [],
        "edges": [],
    }


def require_graph(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(
            f"Data file not found: {path}\nRun: init --data-path \"{path}\""
        )
    return read_json(path)


def set_updated_at(graph: Dict[str, Any]) -> None:
    graph["meta"]["updated_at"] = now_iso()


def node_sort_key(node_id: str) -> int:
    m = re.match(r"^N(\d+)$", node_id)
    return int(m.group(1)) if m else 10**9


def next_node_id(nodes: List[Dict[str, Any]]) -> str:
    max_num = 0
    for n in nodes:
        m = re.match(r"^N(\d+)$", n["id"])
        if m:
            max_num = max(max_num, int(m.group(1)))
    return f"N{max_num + 1:03d}"


def branch_exists(graph: Dict[str, Any], branch_id: str) -> bool:
    return any(b["id"] == branch_id for b in graph["branches"])


def node_exists(graph: Dict[str, Any], node_id: str) -> bool:
    return any(n["id"] == node_id for n in graph["nodes"])


def create_edge(from_id: str, to_id: str, relation: str, note: str) -> Dict[str, Any]:
    return {
        "from": from_id,
        "to": to_id,
        "relation": relation,
        "note": note,
        "created_at": now_iso(),
    }


def relation_label(relation: str) -> str:
    return {
        "next": "next",
        "split_from": "split",
        "merge_into": "merge",
        "parallel_to": "parallel",
        "depends_on": "depends",
    }.get(relation, relation)


def sanitize_mermaid(text: str) -> str:
    return text.replace('"', "'").replace("<", "&lt;").replace(">", "&gt;")


def compact_node_title(title: str, max_chars: int = 24) -> str:
    # Strip optional timestamp prefix from imported Chinese timeline headings.
    cleaned = title.strip()
    m = re.match(r"^\d{4}-\d{2}-\d{2}[^，,]*[，,]\s*(.+)$", cleaned)
    if m:
        cleaned = m.group(1).strip()
    if len(cleaned) <= max_chars:
        return cleaned
    return cleaned[: max_chars - 3] + "..."


def parse_parent_ids(raw: str) -> List[str]:
    if not raw:
        return []
    return re.findall(r"N\d{3}", raw)


def parse_heading(line: str, fallback_idx: int) -> tuple[str, str]:
    line = line.strip()
    m1 = re.match(r"^##\s*(N\d+)\s*\|\s*(.+)$", line)
    if m1:
        return m1.group(1), m1.group(2).strip()

    m2 = re.match(r"^##\s*节点\s*([0-9]+)\s*[（(](.+)[）)]\s*$", line)
    if m2:
        node_num = int(m2.group(1))
        title = m2.group(2).strip()
        return f"N{node_num:03d}", title

    m3 = re.match(r"^##\s*节点\s*([0-9]+)\s*$", line)
    if m3:
        node_num = int(m3.group(1))
        return f"N{node_num:03d}", "Untitled"

    return f"N{fallback_idx:03d}", line.lstrip("#").strip()


def evidence_to_en(raw: str) -> str:
    value = raw.strip().lower()
    if value in EVIDENCE_LEVELS:
        return value
    if "已验证" in raw:
        return "verified"
    if "观察" in raw:
        return "observation"
    if "猜想" in raw or "假设" in raw:
        return "hypothesis"
    return "observation"


def split_sections(text: str) -> List[tuple[str, List[str]]]:
    sections: List[tuple[str, List[str]]] = []
    header: str | None = None
    body: List[str] = []
    for line in text.splitlines():
        if line.startswith("## "):
            if header is not None:
                sections.append((header, body))
            header = line
            body = []
            continue
        if header is not None:
            body.append(line)
    if header is not None:
        sections.append((header, body))
    return sections


def parse_field_line(line: str) -> tuple[str, str] | None:
    stripped = line.strip()
    if not stripped.startswith("- "):
        return None
    content = stripped[2:].strip()
    for sep in [":", "："]:
        if sep in content:
            key, value = content.split(sep, 1)
            return key.strip(), value.strip()
    return None


def parse_timeline_markdown(path: Path) -> List[Dict[str, Any]]:
    text = path.read_text(encoding="utf-8")
    sections = split_sections(text)
    nodes: List[Dict[str, Any]] = []

    key_map = {
        "question": "question",
        "当时问题": "question",
        "trigger": "trigger",
        "触发原因（为什么想到这个）": "trigger",
        "hypothesis": "hypothesis",
        "当时假设": "hypothesis",
        "actions": "actions",
        "采取动作（做了什么实验/改了什么）": "actions",
        "observations": "observations",
        "观察结果（事实）": "observations",
        "conclusion": "conclusion",
        "当时结论（解释）": "conclusion",
        "evidence_level": "evidence_level",
        "证据等级（已验证 / 观察 / 猜想）": "evidence_level",
        "next_question": "next_question",
        "引出的下一步问题": "next_question",
        "next_plan": "next_plan",
        "下一步计划": "next_plan",
        "branch_id": "branch_id",
        "parent_node_ids": "parent_node_ids",
        "relation_type": "relation_type",
    }

    fallback_idx = 1
    for header, body in sections:
        node_id, title = parse_heading(header, fallback_idx)
        fallback_idx += 1

        node: Dict[str, Any] = {
            "id": node_id,
            "title": title,
            "branch_id": "main",
            "created_at": now_iso(),
            "question": "",
            "trigger": "",
            "hypothesis": "",
            "actions": "",
            "observations": "",
            "conclusion": "",
            "evidence_level": "observation",
            "next_question": "",
            "next_plan": "",
            "tags": [],
            "parent_node_ids": [],
            "relation_type": "next",
        }

        for line in body:
            parsed = parse_field_line(line)
            if not parsed:
                continue
            raw_key, raw_value = parsed
            key = key_map.get(raw_key, "")
            if not key:
                continue
            if key == "branch_id":
                node["branch_id"] = raw_value.strip("` ")
            elif key == "parent_node_ids":
                node["parent_node_ids"] = parse_parent_ids(raw_value)
            elif key == "relation_type":
                relation = raw_value.strip("` ")
                node["relation_type"] = relation if relation in RELATIONS else "next"
            elif key == "evidence_level":
                node["evidence_level"] = evidence_to_en(raw_value)
            else:
                node[key] = raw_value

        nodes.append(node)

    nodes.sort(key=lambda n: node_sort_key(n["id"]))
    return nodes


def cmd_init(args: argparse.Namespace) -> None:
    data_path = Path(args.data_path)
    if data_path.exists() and not args.force:
        raise FileExistsError(f"Data file already exists: {data_path}\nUse --force to overwrite.")
    graph = empty_graph(project=args.project)
    write_json(data_path, graph)
    print(f"[OK] Initialized: {data_path}")


def cmd_add_branch(args: argparse.Namespace) -> None:
    data_path = Path(args.data_path)
    graph = require_graph(data_path)

    if branch_exists(graph, args.branch_id):
        raise ValueError(f"Branch already exists: {args.branch_id}")

    parent = args.parent_branch_id
    if parent and not branch_exists(graph, parent):
        raise ValueError(f"parent_branch_id does not exist: {parent}")

    graph["branches"].append(
        {
            "id": args.branch_id,
            "name": args.name,
            "parent_branch_id": parent,
            "status": "active",
            "description": args.description,
            "created_at": now_iso(),
        }
    )
    set_updated_at(graph)
    write_json(data_path, graph)
    print(f"[OK] Added branch: {args.branch_id}")


def cmd_add_node(args: argparse.Namespace) -> None:
    data_path = Path(args.data_path)
    graph = require_graph(data_path)

    if not branch_exists(graph, args.branch_id):
        raise ValueError(f"branch_id does not exist: {args.branch_id}")

    evidence_level = args.evidence_level
    if evidence_level not in EVIDENCE_LEVELS:
        raise ValueError(f"Invalid evidence_level: {evidence_level}")

    node_id = args.node_id or next_node_id(graph["nodes"])
    if node_exists(graph, node_id):
        raise ValueError(f"node_id already exists: {node_id}")

    tags = [t.strip() for t in args.tags.split(",") if t.strip()] if args.tags else []

    node = {
        "id": node_id,
        "title": args.title,
        "branch_id": args.branch_id,
        "created_at": now_iso(),
        "question": args.question,
        "trigger": args.trigger,
        "hypothesis": args.hypothesis,
        "actions": args.actions,
        "observations": args.observations,
        "conclusion": args.conclusion,
        "evidence_level": evidence_level,
        "next_question": args.next_question,
        "next_plan": args.next_plan,
        "tags": tags,
        "parent_node_ids": [],
        "relation_type": "next",
    }

    relation_type = args.relation_type
    if relation_type not in RELATIONS:
        raise ValueError(f"Invalid relation_type: {relation_type}")

    parent_ids: List[str] = []
    if args.parent_node_ids:
        parent_ids.extend(parse_parent_ids(args.parent_node_ids))
    if args.parent_node:
        parent_ids.append(args.parent_node)
    unique_parents = list(dict.fromkeys(parent_ids))

    for pid in unique_parents:
        if not node_exists(graph, pid):
            raise ValueError(f"parent node does not exist: {pid}")

    node["parent_node_ids"] = unique_parents
    node["relation_type"] = relation_type
    graph["nodes"].append(node)

    for pid in unique_parents:
        graph["edges"].append(create_edge(pid, node_id, relation_type, args.note))

    set_updated_at(graph)
    write_json(data_path, graph)
    print(f"[OK] Added node: {node_id}")


def cmd_add_edge(args: argparse.Namespace) -> None:
    data_path = Path(args.data_path)
    graph = require_graph(data_path)

    if not node_exists(graph, args.from_id):
        raise ValueError(f"from_id does not exist: {args.from_id}")
    if not node_exists(graph, args.to_id):
        raise ValueError(f"to_id does not exist: {args.to_id}")
    if args.relation not in RELATIONS:
        raise ValueError(f"Invalid relation: {args.relation}")

    graph["edges"].append(create_edge(args.from_id, args.to_id, args.relation, args.note))
    set_updated_at(graph)
    write_json(data_path, graph)
    print(f"[OK] Added edge: {args.from_id} -> {args.to_id} ({args.relation})")


def cmd_import_timeline(args: argparse.Namespace) -> None:
    data_path = Path(args.data_path)
    timeline_input = Path(args.timeline_input)

    if not timeline_input.exists():
        raise FileNotFoundError(f"Timeline markdown not found: {timeline_input}")
    if data_path.exists() and not args.force:
        raise FileExistsError(f"Data file already exists: {data_path}\nUse --force to overwrite.")

    parsed_nodes = parse_timeline_markdown(timeline_input)
    if not parsed_nodes:
        raise ValueError("No nodes parsed from timeline markdown.")

    graph = empty_graph(project=args.project)

    branch_ids = sorted({n["branch_id"] for n in parsed_nodes if n["branch_id"] and n["branch_id"] != "main"})
    for branch_id in branch_ids:
        graph["branches"].append(
            {
                "id": branch_id,
                "name": branch_id.replace("-", " ").title(),
                "parent_branch_id": "main",
                "status": "active",
                "description": "Imported from timeline markdown.",
                "created_at": now_iso(),
            }
        )

    node_ids = set()
    for node in parsed_nodes:
        if node["id"] in node_ids:
            raise ValueError(f"Duplicate node id parsed from timeline: {node['id']}")
        node_ids.add(node["id"])
        graph["nodes"].append(node)

    edge_keys = set()
    ordered = sorted(graph["nodes"], key=lambda n: node_sort_key(n["id"]))
    prev_node_id = ""
    for node in ordered:
        relation = node.get("relation_type", "next")
        relation = relation if relation in RELATIONS else "next"
        parents = list(node.get("parent_node_ids", []))

        if not parents and prev_node_id:
            parents = [prev_node_id]
            relation = "next"
            node["parent_node_ids"] = parents
            node["relation_type"] = relation

        for pid in parents:
            if pid not in node_ids:
                continue
            key = (pid, node["id"], relation)
            if key in edge_keys:
                continue
            edge_keys.add(key)
            graph["edges"].append(create_edge(pid, node["id"], relation, "imported"))

        prev_node_id = node["id"]

    set_updated_at(graph)
    write_json(data_path, graph)
    print(f"[OK] Imported timeline into data: {data_path}")

    if args.render_after_import:
        timeline_path = Path(args.timeline_md)
        graph_path = Path(args.graph_md)
        ensure_parent(timeline_path)
        ensure_parent(graph_path)
        timeline_path.write_text(render_timeline_md(graph), encoding="utf-8", newline="\n")
        graph_path.write_text(render_graph_md(graph), encoding="utf-8", newline="\n")
        print(f"[OK] Timeline markdown: {timeline_path}")
        print(f"[OK] Graph markdown: {graph_path}")


def render_timeline_md(graph: Dict[str, Any]) -> str:
    lines: List[str] = []
    lines.append("# Research Thinking Timeline (Branch-Aware)")
    lines.append("")
    lines.append(f"- project: `{graph['meta']['project']}`")
    lines.append(f"- updated_at: `{graph['meta']['updated_at']}`")
    lines.append("")

    nodes = sorted(graph["nodes"], key=lambda n: node_sort_key(n["id"]))
    for n in nodes:
        lines.append(f"## {n['id']} | {n['title']}")
        lines.append(f"- branch_id: `{n['branch_id']}`")
        lines.append(f"- parent_node_ids: `{', '.join(n.get('parent_node_ids', []))}`")
        lines.append(f"- relation_type: `{n.get('relation_type', 'next')}`")
        lines.append(f"- created_at: `{n['created_at']}`")
        lines.append(f"- evidence_level: `{n['evidence_level']}`")
        if n["tags"]:
            lines.append(f"- tags: `{', '.join(n['tags'])}`")
        lines.append(f"- question: {n['question']}")
        lines.append(f"- trigger: {n['trigger']}")
        lines.append(f"- hypothesis: {n['hypothesis']}")
        lines.append(f"- actions: {n['actions']}")
        lines.append(f"- observations: {n['observations']}")
        lines.append(f"- conclusion: {n['conclusion']}")
        lines.append(f"- next_question: {n['next_question']}")
        lines.append(f"- next_plan: {n['next_plan']}")
        lines.append("")
    return "\n".join(lines) + "\n"


def render_graph_md(graph: Dict[str, Any]) -> str:
    lines: List[str] = []
    lines.append("# Research Thinking Graph (Mermaid)")
    lines.append("")
    lines.append("```mermaid")
    lines.append("%%{init: {'flowchart': {'curve': 'linear', 'nodeSpacing': 48, 'rankSpacing': 62}}}%%")
    lines.append("flowchart TD")

    nodes = sorted(graph["nodes"], key=lambda n: node_sort_key(n["id"]))
    for n in nodes:
        short_title = compact_node_title(n["title"])
        label = sanitize_mermaid(f"{n['id']}\\n{n['branch_id']}\\n{short_title}")
        lines.append(f'  {n["id"]}["{label}"]')

    palette = [
        ("#E8F1FF", "#2D6CDF"),
        ("#EAFBF1", "#2E8B57"),
        ("#FFF5E6", "#C27A00"),
        ("#FCEFF5", "#B12A6B"),
        ("#F0F4F8", "#4B5563"),
    ]
    branch_ids = sorted({n["branch_id"] for n in nodes})
    branch_class: Dict[str, str] = {}
    for idx, branch_id in enumerate(branch_ids):
        class_name = "branch_" + re.sub(r"[^A-Za-z0-9_]", "_", branch_id)
        fill, stroke = palette[idx % len(palette)]
        branch_class[branch_id] = class_name
        lines.append(
            f"  classDef {class_name} fill:{fill},stroke:{stroke},stroke-width:1px,color:#111111;"
        )
    for branch_id in branch_ids:
        member_ids = [n["id"] for n in nodes if n["branch_id"] == branch_id]
        if member_ids:
            lines.append(f"  class {','.join(member_ids)} {branch_class[branch_id]};")

    for e in graph["edges"]:
        rel = relation_label(e["relation"])
        lines.append(f'  {e["from"]} -- "{rel}" --> {e["to"]}')

    lines.append("```")
    lines.append("")
    lines.append("## Branches")
    for b in graph["branches"]:
        parent = b["parent_branch_id"] if b["parent_branch_id"] else "None"
        lines.append(f"- `{b['id']}`: {b['name']} (parent={parent}, status={b['status']})")
    lines.append("")
    return "\n".join(lines) + "\n"


def cmd_render(args: argparse.Namespace) -> None:
    data_path = Path(args.data_path)
    graph = require_graph(data_path)

    timeline_path = Path(args.timeline_md)
    graph_path = Path(args.graph_md)

    ensure_parent(timeline_path)
    ensure_parent(graph_path)

    timeline_path.write_text(render_timeline_md(graph), encoding="utf-8", newline="\n")
    graph_path.write_text(render_graph_md(graph), encoding="utf-8", newline="\n")

    print(f"[OK] Timeline markdown: {timeline_path}")
    print(f"[OK] Graph markdown: {graph_path}")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Local JSON/Markdown thinking branch tracker")
    p.add_argument("--data-path", default=str(DEFAULT_DATA_PATH), help="Path to thinking graph JSON file")
    sub = p.add_subparsers(dest="command", required=True)

    sp_init = sub.add_parser("init", help="Initialize data file")
    sp_init.add_argument("--project", default="research-project", help="Project name")
    sp_init.add_argument("--force", action="store_true", help="Overwrite existing file")
    sp_init.set_defaults(func=cmd_init)

    sp_branch = sub.add_parser("add-branch", help="Add a branch")
    sp_branch.add_argument("--branch-id", required=True, help="Branch id, e.g. mlp-line")
    sp_branch.add_argument("--name", required=True, help="Branch display name")
    sp_branch.add_argument("--parent-branch-id", default="", help="Optional parent branch id")
    sp_branch.add_argument("--description", default="", help="Branch description")
    sp_branch.set_defaults(func=cmd_add_branch)

    sp_node = sub.add_parser("add-node", help="Add a thinking node")
    sp_node.add_argument("--node-id", default="", help="Optional node id, e.g. N010")
    sp_node.add_argument("--branch-id", default="main", help="Branch id")
    sp_node.add_argument("--title", required=True, help="Node title")
    sp_node.add_argument("--question", default="", help="Question at that time")
    sp_node.add_argument("--trigger", default="", help="Trigger reason")
    sp_node.add_argument("--hypothesis", default="", help="Hypothesis")
    sp_node.add_argument("--actions", default="", help="Actions taken")
    sp_node.add_argument("--observations", default="", help="Observed facts")
    sp_node.add_argument("--conclusion", default="", help="Conclusion")
    sp_node.add_argument(
        "--evidence-level",
        default="observation",
        choices=sorted(EVIDENCE_LEVELS),
        help="Evidence level",
    )
    sp_node.add_argument("--next-question", default="", help="Next question")
    sp_node.add_argument("--next-plan", default="", help="Next plan")
    sp_node.add_argument("--tags", default="", help="Comma-separated tags")
    sp_node.add_argument("--parent-node", default="", help="Optional parent node id")
    sp_node.add_argument("--parent-node-ids", default="", help="Optional parent node ids, e.g. N001,N005")
    sp_node.add_argument(
        "--relation-type",
        default="next",
        choices=sorted(RELATIONS),
        help="Relation from parent nodes to this node",
    )
    sp_node.add_argument(
        "--relation",
        default="next",
        choices=sorted(RELATIONS),
        help="Backward-compatible alias; prefer --relation-type",
    )
    sp_node.add_argument("--note", default="", help="Edge note when parent-node is provided")
    sp_node.set_defaults(func=cmd_add_node)

    sp_edge = sub.add_parser("add-edge", help="Add edge between two existing nodes")
    sp_edge.add_argument("--from-id", required=True, help="Source node id")
    sp_edge.add_argument("--to-id", required=True, help="Target node id")
    sp_edge.add_argument("--relation", required=True, choices=sorted(RELATIONS), help="Edge relation")
    sp_edge.add_argument("--note", default="", help="Optional note")
    sp_edge.set_defaults(func=cmd_add_edge)

    sp_render = sub.add_parser("render", help="Render markdown outputs")
    sp_render.add_argument("--timeline-md", default=str(DEFAULT_TIMELINE_MD), help="Timeline markdown output path")
    sp_render.add_argument("--graph-md", default=str(DEFAULT_GRAPH_MD), help="Mermaid graph markdown output path")
    sp_render.set_defaults(func=cmd_render)

    sp_import = sub.add_parser("import-timeline", help="Import timeline markdown into branch JSON graph")
    sp_import.add_argument("--timeline-input", default=str(DEFAULT_IMPORT_TIMELINE), help="Source timeline markdown path")
    sp_import.add_argument("--project", default="research-project", help="Project name for imported graph")
    sp_import.add_argument("--force", action="store_true", help="Overwrite existing data-path")
    sp_import.add_argument("--render-after-import", action="store_true", help="Render timeline/graph markdown after import")
    sp_import.add_argument("--timeline-md", default=str(DEFAULT_TIMELINE_MD), help="Timeline markdown output path")
    sp_import.add_argument("--graph-md", default=str(DEFAULT_GRAPH_MD), help="Mermaid graph markdown output path")
    sp_import.set_defaults(func=cmd_import_timeline)

    return p


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if hasattr(args, "parent_branch_id") and args.parent_branch_id == "":
        args.parent_branch_id = None
    if hasattr(args, "node_id") and args.node_id == "":
        args.node_id = None
    if hasattr(args, "parent_node") and args.parent_node == "":
        args.parent_node = None
    if hasattr(args, "relation_type") and args.relation_type == "":
        args.relation_type = "next"
    if hasattr(args, "relation") and args.relation and hasattr(args, "relation_type"):
        if args.relation_type == "next":
            args.relation_type = args.relation
    args.func(args)


if __name__ == "__main__":
    main()
