# Thinking Branch Tracker (MVP)

Minimal local plugin prototype for non-linear research thinking.

Chinese documentation: `README.zh-CN.md`

It stores thinking nodes/edges in JSON and renders:
- timeline markdown (narrative replay)
- mermaid graph markdown (branch/merge visualization)
- can import existing `research_thinking_timeline.md` to bootstrap graph data

## Files

- `scripts/branch_tracker.py`: CLI tool
- `data/thinking_graph.json`: local source of truth (created by `init`)
- `thinking_timeline.md`: rendered timeline (created by `render`)
- `thinking_graph.md`: rendered mermaid graph (created by `render`)

## Quick Start

Run from repo root:

```bash
python plugins/thinking-branch-tracker/scripts/branch_tracker.py init --project rl_do
```

Or bootstrap from an existing timeline markdown:

```bash
python plugins/thinking-branch-tracker/scripts/branch_tracker.py import-timeline --timeline-input research_thinking_timeline.md --force --render-after-import
```

Add branches:

```bash
python plugins/thinking-branch-tracker/scripts/branch_tracker.py add-branch --branch-id mlp-line --name "MLP line"
python plugins/thinking-branch-tracker/scripts/branch_tracker.py add-branch --branch-id siren-line --name "SIREN line" --parent-branch-id mlp-line
```

Add nodes:

```bash
python plugins/thinking-branch-tracker/scripts/branch_tracker.py add-node --branch-id mlp-line --title "MLP fitting difficulty noticed" --question "Why does MLP miss max_abs target?" --actions "Ran width/layer/lr sweeps" --observations "Many configs failed strict tol" --conclusion "Capacity + optimization mismatch" --next-question "Try alternative basis networks?"
python plugins/thinking-branch-tracker/scripts/branch_tracker.py add-node --branch-id siren-line --title "Switch to SIREN" --parent-node-ids N001 --relation-type split_from --question "Can sinusoidal bias fit better?" --actions "Scanned w0/lr" --observations "Solved after tuning" --conclusion "Structure helps but sensitive to w0"
```

Add manual edges (optional):

```bash
python plugins/thinking-branch-tracker/scripts/branch_tracker.py add-edge --from-id N001 --to-id N002 --relation split_from
```

Render markdown outputs:

```bash
python plugins/thinking-branch-tracker/scripts/branch_tracker.py render
```

## Suggested Workflow

1. `add-node` whenever question/hypothesis/conclusion/route changes.
2. Use `split_from` when a new branch starts.
3. Use `merge_into` when two lines reconnect.
4. Run `render` after each session to refresh timeline + graph.

## Required Node Fields for Branch-Aware Tracking

Each node should carry:

- `branch_id`
- `parent_node_ids` (comma-separated `Nxxx` list)
- `relation_type` (`split_from` / `merge_into` / `parallel_to` / `next` / `depends_on`)

These fields are used to build explicit branch/merge paths in Mermaid.

## Notes

- Default data path:
  `plugins/thinking-branch-tracker/data/thinking_graph.json`
- You can override all output paths via CLI flags.
