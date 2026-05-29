# Thinking Branch Tracker（中文说明）

这是一个最小可用原型，用来记录“非线性研究思路”：

1. 用 `JSON` 存储思路节点与关系边。  
2. 用 `Markdown` 输出时间线。  
3. 自动生成 `Mermaid` 思路分叉图。  

## 文件说明

- `scripts/branch_tracker.py`：命令行工具
- `data/thinking_graph.json`：思路图数据源（`init` 或 `import-timeline` 创建）
- `thinking_timeline.md`：渲染后的时间线
- `thinking_graph.md`：渲染后的 Mermaid 图

## 快速开始

在仓库根目录运行：

```bash
python plugins/thinking-branch-tracker/scripts/branch_tracker.py init --project rl_do
```

如果你已经有 `research_thinking_timeline.md`，可直接导入并建图：

```bash
python plugins/thinking-branch-tracker/scripts/branch_tracker.py import-timeline --timeline-input research_thinking_timeline.md --force --render-after-import
```

## 常用命令

新增分支：

```bash
python plugins/thinking-branch-tracker/scripts/branch_tracker.py add-branch --branch-id mlp-line --name "MLP路线"
python plugins/thinking-branch-tracker/scripts/branch_tracker.py add-branch --branch-id siren-line --name "SIREN路线" --parent-branch-id mlp-line
```

新增节点（带分叉关系）：

```bash
python plugins/thinking-branch-tracker/scripts/branch_tracker.py add-node --branch-id siren-line --title "切换到SIREN" --parent-node-ids N004 --relation-type split_from --question "SIREN是否更容易拟合DP值函数？" --actions "扫描w0/lr" --observations "调参后可达标" --conclusion "结构更匹配，但对w0敏感"
```

手动加边（可选）：

```bash
python plugins/thinking-branch-tracker/scripts/branch_tracker.py add-edge --from-id N004 --to-id N005 --relation split_from
```

渲染输出：

```bash
python plugins/thinking-branch-tracker/scripts/branch_tracker.py render
```

## 三字段（必须）

每个节点建议都补齐以下字段，才能清晰展示分叉/合流路径：

- `branch_id`
- `parent_node_ids`（如 `N001,N005`）
- `relation_type`（`split_from` / `merge_into` / `parallel_to` / `next` / `depends_on`）

## 建议工作流

1. 每次出现“问题变化/假设变化/结论变化/路线变化”就加一个节点。  
2. 开新路线时用 `split_from`。  
3. 两条路线回连时用 `merge_into`。  
4. 每次会话结束后执行 `render`，刷新时间线和思路图。  
