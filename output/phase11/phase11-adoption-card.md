# Phase 11 Adoption Card

状态：`PENDING SECOND INDEPENDENT REVIEW`；业务采用结论受真实图片 provider 和受保护 Harness 同步门禁约束。

实现代码基线：`9cf8e9e862419023b1355ee8156dc7efa50e2d7e`。

主要证据：

- synthetic 五方法报告：[phase11-benchmark-synthetic.json](phase11-benchmark-synthetic.json)，SHA-256 `c26d75c33396ef3dd8e5c43de7f3de210f3b9bef40fa240c183dc30c54b41fdd`。
- 真实 ERP API/Web 报告：[phase11-benchmark-erp-readonly.json](phase11-benchmark-erp-readonly.json)，SHA-256 `bb517ff2f6a9fccc29db25af245aacf3f8005a8bd5faa4aa10053cad2a1a6a9e`。
- 真实 ERP 脱敏 GUI 边界：[phase11-erp-visual-boundary.json](phase11-erp-visual-boundary.json)，SHA-256 `50a32c4454ee65b83fc286c5ed9c382fc83a25925fd5114bf3cc97763a23ab37`。

## 方法决策

| 方法 | 决策 | 适用条件 | 证据与限制 |
| --- | --- | --- | --- |
| typed API | `KEEP BUSINESS DEFAULT` | 采购事实有稳定、受治理的 typed Gateway | 真实 ERP API/Web 三次均 `MATCHED`；继续经过现有 Run、capability 和权限边界，不新增写入权限 |
| DOM | `LAB_ONLY CANDIDATE` | 页面结构已知、selector/引用可观察且唯一 | synthetic 9/9 正确；v2 属性变化先失败后修复；尚未授权接入业务 Runtime |
| ARIA | `LAB_ONLY CANDIDATE` | 控件有稳定 role、accessible name、焦点顺序 | synthetic 9/9 正确；只证明本实验页面的可访问性路径，不是完整 ERP 无障碍审计 |
| screenshot GUI | `BLOCKED / EXPERIMENT ONLY` | 仅在截图可靠脱敏且真实图片模型通过内容验证 | 脱敏 GUI test double 在显式 trusted API mapping 下安全返回四字段；四个已配置角色均 `VISION_PROVIDER_UNAVAILABLE`，因此没有真实 GUI 准确率或 ERP 三方成功 |
| hybrid | `LAB_ONLY CANDIDATE` | DOM/ARIA 与同页面版本截图同步且冲突可停止 | synthetic test double 9/9 正确，目标不存在返回 `NOT_FOUND`，冲突会返回 `OBSERVATION_CONFLICT`；不能在视觉失败后静默降级为成功 |

## 统一业务任务

找到指定采购单，读取单号、供应商、业务状态和币种，并说明观察是否完整。目标不存在、无权限、加载未完成、证据冲突和登录失效都返回明确终态；数量明细只用于滚动/定位实验，不跨单位相加。

## 采用边界

- 所有新增执行器仍在 `LAB_ONLY`，不注册进业务 Runtime，不修改 `ProviderMessage.content`，不调用 ERP 写工具。
- 业务主线继续使用 typed API；DOM/ARIA 和混合只作为可复跑实验候选，视觉保持阻塞直到 provider 读出合成图片中未在提示词透露的内容。
- synthetic 视觉/混合成绩是 `scripted-fixture-replay` test double 的执行循环证据，不是模型质量、生产延迟或成本承诺。
- usage 未返回时记录 `null`，没有可核验价格就不换算成本；三次重复只报告中位数和范围。

## 重新评估触发器

1. `probe-vision` 对两张不同合成截图返回严格结构化、可核验的四字段观察，并保存角色/模型/usage 摘要。
2. 重新在同一真实 ERP 单据、同一脱敏映射和同一页面版本下完成 API/Web/GUI 三方只读对照。
3. 新页面版本或 provider 变化必须保留旧失败证据，先补回归测试和独立审查，再更新本卡。
