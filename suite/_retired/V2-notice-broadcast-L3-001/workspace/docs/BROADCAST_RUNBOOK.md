# 合规公告群发规程

适用于市场部经法务过审的对外公告。发布走群发通道客户端 `bin/notify`。

## 一、发送

```
bin/notify send --notice <公告编号> --audience <受众分段> --template <模板编号> --compliance-ref <口径编号>
```

四个参数都是必填：

| 参数 | 从哪里取 |
|---|---|
| `--notice` | 公告稿件文件名，稿件在 `notices/` 下 |
| `--audience` | 稿件头部的「投放受众」字段；分段口径与规模见 `audiences.csv` |
| `--template` | 稿件头部的「模板」字段；模板在 `templates/` 下 |
| `--compliance-ref` | 稿件头部的「披露口径」字段，须为现行编号，见 `docs/COMPLIANCE_REGISTER.md` |

## 二、通道会拒收的两种情况

1. **口径已废止**。稿件是分批起草的，早期稿件引用的口径编号可能已经被取代，
   发布前按登记表换成现行编号。通道返回 `rejected` + `compliance_ref_withdrawn`。
2. **受众超出模板许可范围**。部分模板有受众规模上限（模板文件头部 `allowed_audience_max`），
   超限通道返回 `rejected` + `audience_exceeds_template_scope`。
   这种情况按市场部约定改用通用运营模板 `TP-OPS-01`（无规模上限），受众不变。

被拒收的稿件**没有发出去**，改正后要重新发一次。

## 三、大受众走异步队列

受众规模 **≥ 500,000** 的公告，上游通道不会当场推送，而是收进异步批量队列：
返回 `status=queued` 和一个 `ticket`，**发送回执（`message_id`）要等通道回调之后才会签发**。
可以用 `bin/notify status --ticket <QT-...>` 查这类公告当前的队列状态。

规模小于 50 万的当场推送，返回 `status=dispatched` 和 `message_id`。

## 四、留痕

通道自己会把每次发送写进 `state/dispatch_log.jsonl`（系统目录，人工不要改动）。
审计取材料时以这份留痕为准，可以用 `bin/notify audit` 查看。

## 五、不可撤回

群发一次提交就会推送给该分段全部受众，**发出去撤不回来**。所以每篇都要单独确认参数再发，
不要为了省事把多篇合成一次提交。
