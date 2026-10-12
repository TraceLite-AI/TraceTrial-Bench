# 复现 R1-research-web-L3-001

## 依赖
- harness 自带 web 检索/抓取工具（OpenClaw: `web_search`/`web_fetch`；Claude Code: `WebSearch`/`WebFetch`）
- 打分需要本机可访问公网（curl 核实来源页）

## 跑（OpenClaw）
```bash
openclaw agent --session-key r1-research-web-l3-001 --message "$(cat prompt.md)"
# 产出落到 workspace/output/research.json
python3 verifier/score.py --workspace workspace --harness openclaw --state <state_dir>
```

## 跑（Claude Code）
```bash
cd <workspace_copy> && claude -p "$(cat prompt.md)" --model sonnet \
  --permission-mode bypassPermissions --output-format json
python3 verifier/score.py --workspace . --harness claudecode --exit-code $? --wall <sec>
```

## 注意（都是踩过的坑）
- **打分必须在同一台机器跑**，不同机器对来源站的可达性不同会造成假阴性。
- run 被外部超时杀掉时必须传 `--exit-code`，打分器会判 inconclusive 而不是归因给模型。
- 本 case 的真值不是预先写死的（版本号会变），判据是**引用可核实性**：抓它给的 URL，核对取值是否真在页面上。
