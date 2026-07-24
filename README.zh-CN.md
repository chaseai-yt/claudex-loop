# grill-me-codex

[English](README.md) | [简体中文](README.zh-CN.md) | [日本語](README.ja.md)

**让两个 AI 模型共同打磨你的计划——然后互换角色来构建它。** 这是一组 Claude Code 技能，旨在弥合 AI 辅助编码中的两道鸿沟：*你与 Claude* 之间的鸿沟（我们是否对要构建的内容达成了一致？），以及 *Claude 与其产出质量* 之间的鸿沟（计划是否真的正确——你又该如何判断？）。

第一幕会向**你**追问，以锁定计划。第二幕将计划交给 **OpenAI Codex**——来自另一提供商的竞争模型——由它展开多轮对抗式审查，直至两个模型都批准该计划。第三幕（可选）会互换角色：**Codex 根据冻结的计划编写代码**，而 **Claude 像审查贡献者 PR 一样审查差异**。双向进行跨模型检查——没有任何模型为自己的工作打分。

> 为什么要使用第二个模型？因为不能指望规划构建并执行构建的同一个模型去*评判自己的工作*——这会形成回声室。来自不同提供商的模型能够发现 Claude 因自身结构限制而无法察觉的问题。

本项目基于 [Matt Pocock 的 `grill-me` / `grill-with-docs`](https://github.com/mattpocock/skills) 技能（MIT）构建——第一幕出自他的工作；迭代式跨模型 Codex 审查（第二幕）以及角色互换后的构建（第三幕）是本项目新增的内容。第三幕的委派模式改编自 [Peter Steinberger 的 `codex-first`](https://github.com/steipete/agent-scripts)。

## 技能

| 技能 | 第一幕 | 第二幕 | 第三幕 | 适用场景 |
|-------|-------|-------|-------|----------|
| **`grill-me-codex`** | Claude 每次向你提出一个问题，直到整个决策树得到解决 | Codex 对抗式审查循环 | 可选 → `codex-build` | 从零开始规划，并希望同时获得共识与第二个模型的检查 |
| **`grill-with-docs-codex`** | 同上，但会根据项目的 `CONTEXT.md` 术语表质疑你的计划，并以内联方式编写 ADR | Codex 审查循环 | 可选 → `codex-build` | 同上，但项目已有既定术语或架构决策 |
| **`codex-review`** | —（你已经有计划） | Codex 审查循环 | 可选 → `codex-build` | 你已有计划，只想通过跨模型审查进行压力测试 |
| **`codex-build`** | — | — | Codex 实现冻结的计划；Claude 负责验证 | 你已有经过审查的规范，并希望由第二个模型编写代码 |

## 第二幕的工作方式（审查）

1. Claude 将锁定后的计划写入 `PLAN.md`，并在 `PLAN-REVIEW-LOG.md` 中开始记录日志。
2. **第 1 轮：** Codex 在**只读沙箱**中审查计划，并返回 `VERDICT: APPROVED` 或 `VERDICT: REVISE`。
3. **第 2..N 轮：** Claude 修改计划；随后恢复*同一个* Codex 会话，使其记得此前的批评，并仅检查这些问题是否已经得到解决。
4. 轮数受 `MAX_ROUNDS` 限制（默认为 5）。流程会在收到 `APPROVED` 或达到上限时终止（明确标记的僵局胜过虚假的“批准”）。
5. **你只需在两个环节把关：**启动时，以及编写任何代码之前的最终确认。Codex 在每一轮中都保持只读，绝不会写入文件。

流程会生成两个产物：`PLAN.md`（简洁的最终计划——说明*要做什么*）和 `PLAN-REVIEW-LOG.md`（完整的逐轮论证——说明*为什么*）。

## 第三幕的工作方式（构建——角色互换）

1. 你批准计划后，`codex-build` 会把 `PLAN.md` 作为冻结的规范交给 Codex——Codex 获得**完整写入权限**（`--yolo`），端到端实现计划，并在过程中运行测试。开始前要求 **git 工作树保持干净**，从而让差异可以隔离且能够回滚。
2. Claude 此时转为审查者，像审查贡献者 PR 一样读取**完整差异**，并亲自运行验证测试。Codex 的声明仅供参考；Claude 自己的运行结果才是证据。
3. 问题会作为精确的修复轮次发回*同一个* Codex 会话，轮数受 `MAX_FIX_ROUNDS` 限制（默认为 2）——超过上限后，Claude 会接手并手动完成，而不是让两个模型无休止地来回处理。
4. **你还需再把关一次：**确认差异。Claude 编写提交；Codex 从不提交。
5. 构建轮次会追加到同一个 `PLAN-REVIEW-LOG.md`，因此一个产物就能讲述完整过程：追问 → 审查 → 构建 → 验证。

额外能力：Codex 会话内置**原生图像生成工具**（由 ChatGPT 账户提供支持，无需 API 密钥）。规范可以包含“自行生成这些图像资源”的步骤，并给出精确的路径和尺寸，将精灵图、纹理、背景等纳入构建契约。

## 安装

将技能文件夹复制到你的 Claude Code 技能目录：

```bash
# macOS / Linux
cp -r skills/* ~/.claude/skills/

# Windows (PowerShell)
Copy-Item -Recurse skills\* $env:USERPROFILE\.claude\skills\
```

然后在 Claude Code 中调用：`/grill-me-codex`、`/grill-with-docs-codex`、`/codex-review` 或 `/codex-build`。

## 前置要求

- **Codex CLI ≥ 0.130**——运行 `npm install -g @openai/codex@latest`（旧版本会在默认的 `gpt-5.5` 模型上报错）。
- **已通过身份验证的 Codex**——运行一次 `codex login`（ChatGPT 账户即可；Free/Plus/Pro/Max 均可）。
- **不要固定模型**——ChatGPT 账户身份验证会拒绝 `gpt-5.x-codex` 模型变体；这些技能会使用你的配置默认值。

## 可调参数

| 技能 | 变量 | 默认值 | 含义 |
|-------|-----|---------|---------|
| 审查技能 | `MAX_ROUNDS` | `5` | 审查轮次的硬性上限 |
| 审查技能 | `PLAN_FILE` | `PLAN.md` | 计划的存放位置 |
| 全部 | `LOG_FILE` | `PLAN-REVIEW-LOG.md` | 论证记录 |
| `codex-build` | `SPEC_FILE` | `PLAN.md` | Codex 要实现的冻结规范 |
| `codex-build` | `MAX_FIX_ROUNDS` | `2` | Claude 接手前的修复轮数 |
| `codex-build` | `PROOF_CMD` | 来自规范 | 作为验证依据的确切测试命令 |

调用时可传入 `rounds=3` 等参数来覆盖默认值。

## 安全性

**审查技能（第一至第二幕）：** Codex 在**每一轮中都以只读方式运行**——首次调用使用 `-s read-only`，每次恢复都使用 `-c sandbox_mode="read-only"`（`resume` 子命令不接受 `-s`；如果不强制设为只读，它会继承 `config.toml` 中的沙箱默认值，该值可能是 `danger-full-access`）。这些技能会替你处理上述设置。在你批准最终计划之前，不会写入任何代码。

**`codex-build`（第三幕）**有意反转这一机制：Codex 获得完整写入权限——正因如此，该技能设置了严格的把关环节。启动前要求工作树保持干净（便于隔离差异和干净回滚），Claude 会逐行读取差异并亲自运行验证，修复轮次有明确上限，提交必须由人类把关且由 Claude 编写。恢复调用需要使用长选项 `--dangerously-bypass-approvals-and-sandbox`（resume 没有 `--yolo`）——并且始终通过明确的 `thread_id` 恢复，绝不能使用 `--last`：错误或缺失的 ID 可能会在没有提示的情况下进入另一个会话。

## 致谢

- 第一幕（`grill-me`、`grill-with-docs`）© Matt Pocock——https://github.com/mattpocock/skills（MIT）。请参阅各技能的 `THIRD-PARTY-NOTICES.md`。
- 第三幕的 Codex 构建者模式改编自 Peter Steinberger 的 [`codex-first`](https://github.com/steipete/agent-scripts)。
- 第二幕（迭代式 Codex 审查）、第三幕（codex-build）和打包由 [Chase AI](https://youtube.com/@chaseai) 完成。
- 想要进一步深入？**Claude Code Masterclass** 以及一个使用智能体 AI 交付产品的开发者社区位于 [Chase AI+](https://www.skool.com/chase-ai/about)。

## 许可证

MIT——请参阅 [LICENSE](./LICENSE)。
