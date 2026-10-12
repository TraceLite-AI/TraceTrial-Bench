# services/ 说明

每个模块结构一致：顶部 import 协议层依赖，`handle()` 是入口，`health()` 供探活。
整改时保持 `handle()` / `health()` 的签名不变。
