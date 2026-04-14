# 第27课示例代码：Human as 甲方——人工介入的三个工程节点

本课在第26课四步任务链基础上增加**两个人工确认节点**，并新增**多轮需求澄清**机制。
Manager 和 PM 之间的协作机制不变，区别在于：编排器（`m4l27_run.py`）在关键决策点主动暂停，等待人类确认后再继续。

---

## 核心教学点

| 概念 | 说明 |
|------|------|
| **单一接口原则** | `human.json` 只由 `run.py`（以 manager 身份）写入，LLM Agent 不直接接触人类 |
| **编排器控制时机** | 何时打扰人由 `run.py` 决定，不由 LLM 自行判断 |
| **wait_for_human()** | FileLock 读 `human.json`，命令行 `input()` 等待用户确认，y/n 控制流程走向 |
| **HumanDecision** | `wait_for_human()` 的返回值，封装 `confirmed` + `feedback`，支持 `if decision:` 简洁写法 |
| **多轮需求澄清** | 编排器控制循环（最多 `MAX_CLARIFICATION_ROUNDS` 轮，默认3轮），LLM 每轮无状态；用户拒绝时可输入反馈触发下一轮修订 |
| **步骤1新增** | `RequirementsDiscoveryCrew`：Manager 先做需求澄清，再拆解任务（相比第26课多了这一步） |

---

## 目录结构

```
m4l27/
├── m4l27_run.py              # 编排器（主入口，4步 + 2个确认节点 + 多轮澄清）
├── m4l27_manager.py          # Manager 三个 Crew（需求澄清 / 任务分配 / 验收）
├── m4l27_pm.py               # PM Crew（读邮件 → 写产品文档 → 通知）
├── test_m4l27.py             # 单元测试（9个）+ 集成测试（4个，需 LLM）
├── conftest.py               # pytest fixtures（clean_crewai_hooks 等）
├── tools/
│   ├── __init__.py
│   └── mailbox_ops.py        # send_mail / read_inbox（含单一接口约束校验）
├── sandbox-docker-compose.yaml
└── workspace/
    ├── manager/              # Manager 个人区（sessions/、review_result.md）
    ├── pm/                   # PM 个人区（sessions/）
    └── shared/               # 共享工作区
        ├── mailboxes/        # manager.json / pm.json / human.json
        ├── needs/            # requirements.md（需求澄清后写入）
        ├── design/           # product_spec.md（PM输出）
        └── sop/              # product_design_sop.md（Manager 读取后按步骤分配任务）
```

---

## 关键类与函数

### `HumanDecision`（m4l27_run.py）

```python
@dataclass
class HumanDecision:
    confirmed: bool
    feedback: Optional[str] = None   # 拒绝时收集，allow_feedback=True 才有值

    def __bool__(self) -> bool:
        return self.confirmed         # 支持 `if decision:` 简洁写法
```

### `wait_for_human()`（m4l27_run.py）

```
参数：
  human_inbox    human.json 路径
  expected_type  期望消息类型（"needs_confirm" | "checkpoint_request"）
  step_label     打印标签
  allow_feedback 是否在拒绝时收集补充意见（步骤1多轮澄清传 True）

返回：HumanDecision
```

### Manager 三个 Crew（m4l27_manager.py）

| Crew | 说明 |
|------|------|
| `RequirementsDiscoveryCrew` | 需求澄清，用 requirements-discovery skill 发问，写 `requirements.md` |
| `ManagerAssignCrew` | 读 SOP + 需求文档，向 PM 发 `task_assign` |
| `ManagerReviewCrew` | 读 PM 回邮，验收产品文档，写 `review_result.md` |

### `_SessionMixin`（m4l27_manager.py）

所有 Crew 的公共基类，提供 session 保存/恢复逻辑（Manager 和 PM 共用）。

---

## 运行步骤

### 第一步：启动沙盒

第27课的两个沙盒**同时启动**（`docker-compose.yaml` 无 profile 分组）：

```bash
cd /path/to/crewai_mas_demo/m4l27
docker compose -f sandbox-docker-compose.yaml up -d
```

| 角色 | 沙盒端口 | 个人区挂载 | 共享区挂载 |
|------|---------|-----------|-----------|
| Manager | 8027 | `workspace/manager` | `workspace/shared` |
| PM | 8028 | `workspace/pm` | `workspace/shared` |

### 第二步：运行演示

```bash
cd /path/to/crewai_mas_demo
python m4l27/m4l27_run.py
```

启动后会先提示输入需求：
```
请告诉 Manager 你要做什么：
```
输入后流程自动推进，**遇到确认节点时暂停等待 y/n 输入**。

### 可选：调整多轮澄清轮次上限

```bash
MAX_CLARIFICATION_ROUNDS=5 python m4l27/m4l27_run.py
```

---

## 完整流程说明

```
步骤1  Manager   需求澄清第1轮（RequirementsDiscoveryCrew）→ 写 requirements.md
  ↓
⏸️ 确认节点1  run.py 写 human.json(needs_confirm)
              → 终端提示用户打开 needs/requirements.md 确认
              → 输入 y 继续 / n 拒绝（可输入反馈意见）
              → n：收集反馈 → 进入第2轮需求澄清（最多 MAX_CLARIFICATION_ROUNDS 轮）
              → y：确认，流程继续
  ↓
步骤2  Manager   读SOP → 向PM发 task_assign（ManagerAssignCrew）
  ↓
步骤3  PM        读邮件 → 写 product_spec.md → 发 task_done（PMExecuteCrew）
  ↓
⏸️ 确认节点2  run.py 写 human.json(checkpoint_request)
              → 终端提示用户打开 design/product_spec.md 确认
              → 输入 y 继续 / n 终止
  ↓
步骤4  Manager   读邮件 → 验收文档 → 写 review_result.md（ManagerReviewCrew）
```

### 多轮需求澄清示意

```
第1轮：初次梳理 → requirements.md v1
  → 用户输入 n + 反馈"希望增加安全要求"
第2轮：基于反馈修订 → requirements.md v2（覆盖写入）
  → 用户输入 y → 确认，继续步骤2
```

---

## 与第26课的对比

| 项目 | 第26课 | 第27课 |
|------|--------|--------|
| 步骤数 | 4步（步骤0-3） | 4步（步骤1-4） |
| 人工节点 | 无 | 2个（确认需求 + 确认设计） |
| 多轮澄清 | 无 | 有（需求阶段最多N轮，可配置） |
| Manager Crew 数量 | 2（分配+验收） | 3（澄清+分配+验收） |
| human.json | 无 | 有（单一接口，只由 run.py 写） |
| PM → human 路径 | 无 | 禁止（`send_mail` 校验，非 manager 写 human 抛 ValueError） |
| 沙盒启动 | 按 profile 分开 | `up -d` 同时启动 |
| 共享区多出内容 | — | `sop/`（产品设计SOP） |

---

## 运行测试（不需要沙盒）

```bash
cd /path/to/crewai_mas_demo
python -m pytest m4l27/test_m4l27.py -v
```

### 测试用例一览

| ID | 类名 | 说明 | 需要LLM |
|----|------|------|---------|
| T_unit_1 | `TestHumanInboxEmpty` | human.json 为空/类型不匹配时 check 返回空 | ✗ |
| T_unit_2 | `TestSinglePointOfContact` | PM/未知角色写 human.json → raise ValueError | ✗ |
| T_unit_3 | `TestSinglePointOfContact` | Manager 写 human.json 成功 | ✗ |
| T_unit_4 | `TestWaitForHuman` | 用户 y → 消息标记 read=True，returned confirmed=True | ✗ |
| T_unit_5 | `TestWaitForHuman` | 用户 n → 消息也标记 read=True + rejected=True | ✗ |
| T_unit_6 | `TestWaitForHuman` | allow_feedback=True 时拒绝并输入反馈，写入 human_feedback | ✗ |
| T_unit_7 | `TestBuildClarificationInputs` | 首轮 revision_context 为空字符串 | ✗ |
| T_unit_8 | `TestBuildClarificationInputs` | 后续轮 revision_context 含历史反馈 | ✗ |
| T_unit_9 | `TestBuildClarificationInputs` | 反馈中含 `{}` 时自动转义 | ✗ |
| T_int_1 | `TestIntegrationRequirements` | RequirementsDiscoveryCrew → requirements.md 存在 | ✅ |
| T_int_2 | `TestIntegrationTaskAssign` | ManagerAssignCrew → pm.json 有 task_assign | ✅ |
| T_int_3 | `TestIntegrationProductSpec` | PMExecuteCrew → product_spec.md 存在 | ✅ |
| T_int_4 | `TestIntegrationReviewResult` | ManagerReviewCrew → review_result.md 存在 | ✅ |

仅跑单元测试（无需 LLM）：
```bash
python -m pytest m4l27/test_m4l27.py -v -k "unit"
```

---

## 常见问题

**Q：运行到确认节点卡住不动？**
这是正常行为——程序在等 `input()`。终端会显示：
```
⏸️  [人工确认节点] 需求文档确认（第1轮）
  你的决定 (y/n)：
```
输入 `y` 回车继续，输入 `n` 回车进入反馈收集（步骤1）或终止（步骤3）。

**Q：`n` 拒绝后如何触发下一轮修订？**
步骤1的确认节点会提示输入补充意见：
```
  请输入你的补充意见（直接回车跳过）：
  补充意见：希望增加安全要求
```
反馈会传入下一轮 `RequirementsDiscoveryCrew`，引导 LLM 针对性修订文档。

**Q：最多可以修订几轮？**
默认最多 `MAX_CLARIFICATION_ROUNDS=3` 轮。超出后自动终止，可通过环境变量调大：
```bash
MAX_CLARIFICATION_ROUNDS=5 python m4l27/m4l27_run.py
```

**Q：想清除状态重新跑？**
```bash
echo "[]" > workspace/shared/mailboxes/manager.json
echo "[]" > workspace/shared/mailboxes/pm.json
echo "[]" > workspace/shared/mailboxes/human.json
rm -f workspace/shared/needs/requirements.md
rm -f workspace/shared/design/product_spec.md
rm -f workspace/manager/review_result.md
rm -f workspace/manager/sessions/*.json workspace/manager/sessions/*.jsonl
rm -f workspace/pm/sessions/*.json workspace/pm/sessions/*.jsonl
```

**Q：报 `ModuleNotFoundError`？**
确认从 `crewai_mas_demo/` 目录运行，不要在 `m4l27/` 内直接运行。

**Q：`n` 拒绝步骤3的设计文档确认后想重新触发？**
本 demo 直接退出。在真实系统中，拒绝会重新触发对应阶段（由编排器决定逻辑）。
