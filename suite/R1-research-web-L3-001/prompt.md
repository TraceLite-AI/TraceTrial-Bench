做一份开源 coding agent 的横向调研，供技术选型使用。

需要调研这 6 个项目：Cline、Continue、Aider、Goose (block/goose)、OpenHands、Roo Code。
每个项目查这 4 项：最新发布的版本号、开源许可证、是否支持 MCP（Model Context Protocol）、主要实现语言。

产出 `output/research.json`：

{
  "findings": [
    {
      "project": "Cline",
      "field": "latest_version",
      "value": "...",
      "source_url": "...",
      "quote": "..."
    }
  ]
}

字段说明：`field` 取 `latest_version` / `license` / `mcp_support` / `primary_language`；`mcp_support` 填 `yes` 或 `no`；`quote` 填支持该取值的片段。

写完文件即可，不需要额外解释。
