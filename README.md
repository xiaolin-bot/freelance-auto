# freelance-auto

接单自动化流水线：**订单雷达 → AI 筛选 → 提案生成 → 交付流水线 → CRM/通知**。

一个面向自由职业者（尤其全栈/外包接单）的命令行自动化系统：自动抓取平台上的外包需求，用 LLM 筛选出值得投的订单，生成投标提案（需要你人工批准后手动去平台回复），接单后由交付流水线拆解任务、生成交付物（同样需要人工质检），并有 CRM 提醒跟进与收款。

> ⚠️ **本系统是"助手"，不是"代接单机器人"。** 所有对外动作（发提案、交付、收款）都设计为**人工关卡**，请务必在提交给客户之前亲自 review（详见下文「人工质检关卡」与「风险提示」）。

---

## 一、项目简介

| 环节 | 做什么 | 谁执行 |
| --- | --- | --- |
| radar（订单雷达） | 按限频礼貌抓取电鸭 / V2EX 外包板块，解析并去重入库 | 定时/手动 |
| screen（AI 筛选） | LLM 按你的技术栈给订单打分，>= min_score 进候选池 | 定时/手动 |
| proposal（提案生成） | 对候选订单生成投标书（报价/周期/正文），状态 draft | 定时/手动 |
| delivery（交付流水线） | 接单后拆任务、LLM 生成交付物、人工质检、导出 | 手动 |
| crm（客户提醒） | 跟进未回应、临近交付、待收款提醒，可推送通知 | 定时/手动 |
| scheduler（调度器） | 按 config.yaml 的间隔把 radar/screen/proposal/crm 串起来 | 常驻 |
| cli（命令行） | 人工入口：跑流程、批准提案、质检任务、导出交付物、查状态 | 手动 |

技术栈：Python 3.11+、SQLite（零配置本地库）、Pydantic v2、APScheduler、OpenAI 兼容 LLM 客户端（默认 DeepSeek，可换 OpenAI / 通义 / Kimi）。

---

## 二、架构图

```
                        ┌──────────────────────────────┐
                        │         config.yaml          │
                        │   radar/screener/proposal/   │
                        │   delivery/crm/notify/sched  │
                        └──────────────┬───────────────┘
                                       │  load_config()
        ┌──────────────────────────────┼───────────────────────────────┐
        │                              ▼                               │
        │                    ┌──────────────────┐                      │
        │  常驻(可选)         │    scheduler.py   │ ← APScheduler 按分钟间隔 │
        │                    │ radar/screen/     │                      │
        │                    │ proposal/crm      │                      │
        │                    └────────┬─────────┘                      │
        │                             │ 调用                           │
┌───────┼─────────┐   ┌───────────────┼───────────────┐   ┌───────────┴──────┐
│       ▼         │   │               ▼               │   │       ▼          │
│ ┌────────────┐  │   │ ┌──────────────────────────┐ │   │ ┌──────────────┐ │
│ │ radar/run  │  │   │ │    ai/screener.py        │ │   │ │  crm/crm.py  │ │
│ │ 抓取+入库   │──┼──▶│ │ LLM 打分 → 候选池         │ │──▶│ │ 跟进/交付/催款 │ │
│ └────────────┘  │   │ └──────────────┬───────────┘ │   │ └──────┬───────┘ │
└─────────────────┘   │                │              │          │          │
                      │ ┌──────────────▼───────────┐ │          │          │
                      │ │   ai/proposal.py         │ │  ┌───────▼────────┐ │
                      │ │  LLM 生成投标书 (draft)    │ │  │   notify/      │ │
                      │ └──────────────┬───────────┘ │  │   通知推送       │ │
                      │                │             │  └─────────────────┘ │
                      │    ┌───────────▼───────────┐ │                     │
                      │    │     delivery/         │ │  ┌────────────────┐ │
                      │    │  start → run → review │ │  │   cli.py       │ │
                      │    │  → export             │◀┼──│  approve/status │ │
                      │    └───────────────────────┘ │  └────────────────┘ │
                      └──────────────────────────────┘                     │
                                       │                                   │
                                       ▼                                   │
                        ┌──────────────────────────────┐                   │
                        │       data/freelance.db      │◀──────────────────┘
                        │  orders/proposals/projects/  │  所有模块共享一个 SQLite
                        │  tasks/customers/notifications│
                        └──────────────────────────────┘
```

数据流：`radar 入库 → screen 标记候选 → proposal 生成提案 → approve 后手动发客户 → 客户接受 → delivery 启动项目 → LLM 生成交付物 → 人工质检 → export 交付 → crm 跟进收款`。

---

## 三、目录结构

```
C:\freelance-auto\
├── run.ps1                # Windows PowerShell 一键启动（装依赖 + 跑 once）
├── run.bat                # Windows cmd 一键启动（同功能）
├── config.yaml            # 非敏感配置（间隔、阈值、渠道开关）
├── .env                   # 敏感配置（LLM_API_KEY 等，从 .env.example 复制）
├── .env.example           # 环境变量模板
├── pyproject.toml         # 项目定义与依赖
├── data\                  # 运行时数据（自动创建）
│   ├── freelance.db       # SQLite 数据库（订单/提案/项目/任务/通知）
│   ├── app.log            # 日志文件
│   └── workspaces\        # 交付流水线的工作目录（接单项目文件）
├── src\freelance_auto\
│   ├── config.py          # 配置模型 + 加载（AppConfig / LLMSettings）
│   ├── db.py              # SQLite 存储层（CRUD）
│   ├── models.py          # Pydantic 数据模型与状态枚举
│   ├── llm.py             # LLM 客户端（OpenAI 兼容接口）
│   ├── utils.py           # 日志、限频、时间等公共工具
│   ├── scheduler.py       # ★ APScheduler 编排（常驻 / --once）
│   ├── cli.py             # ★ 命令行工具（人工入口）
│   ├── radar\             # 订单雷达（eleduck / v2ex 抓取源）
│   ├── ai\                # AI 筛选与提案生成
│   ├── delivery\          # 交付流水线（任务拆解/生成/质检/导出）
│   ├── crm\               # 客户提醒检查
│   └── notify\            # 通知推送（serverchan/dingtalk/email）
└── tests\                 # 测试
```

（★ = 本次调度与入口模块）

---

## 四、安装步骤

要求：**Windows 10/11**，**Python 3.11+**（本项目在 3.12 上开发）。

```powershell
# 1. 进入项目目录
cd C:\freelance-auto

# 2.（推荐）创建虚拟环境
python -m venv .venv
.\.venv\Scripts\Activate.ps1        # PowerShell 激活

# 3. 安装依赖（可编辑安装，含 console script: freelance-auto）
pip install -e .

# 4. 配置 .env（复制模板并填入 API key）
copy .env.example .env
# 然后编辑 .env，填入 LLM_API_KEY（见下节）

# 5. 验证安装
python -c "import sys; sys.path.insert(0,'src'); from freelance_auto.cli import main; print('cli ok')"
```

> 也可以直接**双击 `run.ps1` 或 `run.bat`**：脚本会自动检查 Python、`pip install -e .`、然后跑一遍 `once`，省去手动步骤（首次会自动创建 `data\` 目录与数据库）。

> ⚠️ 如果直接双击运行，请确保 Python 安装时勾选了 **"Add Python to PATH"**，否则脚本找不到 `python` 命令。

---

## 五、.env 配置说明

`.env` 放**敏感配置**（绝不提交到 git）。从 `.env.example` 复制后编辑：

```ini
# LLM 配置（OpenAI 兼容接口，支持 DeepSeek / OpenAI / 通义 / Kimi 等）
# DeepSeek 默认（便宜、国内可用）
LLM_API_KEY=sk-你的密钥            # ← 必填，否则 screen/proposal/delivery run 无法工作
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_MODEL=deepseek-chat

# 备选：OpenAI
# LLM_BASE_URL=https://api.openai.com/v1
# LLM_MODEL=gpt-4o-mini
```

| 变量 | 必填 | 说明 |
| --- | --- | --- |
| `LLM_API_KEY` | ✅ | LLM 服务商密钥。DeepSeek 从 https://platform.deepseek.com 获取 |
| `LLM_BASE_URL` | 可选 | OpenAI 兼容接口地址，默认 DeepSeek |
| `LLM_MODEL` | 可选 | 模型名，默认 `deepseek-chat` |

未配置 `LLM_API_KEY` 时，`screen` / `proposal` / `delivery run` / `once` 会打印：

```
未配置 LLM_API_KEY，请在 .env 中配置
```

并退出（不影响 radar / crm / status / approve / delivery review / delivery export）。

---

## 六、config.yaml 各项说明

`config.yaml` 放**非敏感配置**。所有项都有默认值，可直接使用默认配置运行。

### radar（订单雷达）
| 键 | 默认 | 说明 |
| --- | --- | --- |
| `request_interval_sec` | `3.0` | 每抓取一页的延迟秒数。**别改太小**（见「平台抓取注意事项」） |
| `max_pages` | `3` | 每次抓取最大页数 |
| `order_ttl_days` | `7` | 订单保留窗口，超过 N 天不再进筛选 |
| `eleduck.enabled` | `true` | 电鸭社区开关（外包/需求版块） |
| `v2ex.enabled` | `true` | V2EX 外包板块开关 |

### screener（AI 筛选）
| 键 | 默认 | 说明 |
| --- | --- | --- |
| `daily_cap` | `20` | 每天最多筛选多少条新订单 |
| `min_score` | `60` | 进入候选池的最低得分（0-100） |
| `top_n` | `5` | 每次通知 Top N 候选 |
| `profile` | 内置文案 | **你的技术栈描述**，LLM 据此匹配订单。建议认真填写，直接影响筛选质量 |

### proposal（提案生成）
| 键 | 默认 | 说明 |
| --- | --- | --- |
| `default_price_min` / `default_price_max` | `500` / `5000` | 默认报价区间（元），LLM 会结合订单预算调整 |
| `default_days` | `5` | 交付周期预估（天） |
| `require_approval` | `true` | 提案生成后是否需要人工批准才可发送（强烈建议保持 `true`） |

### delivery（交付流水线）
| 键 | 默认 | 说明 |
| --- | --- | --- |
| `workdir` | `data/workspaces` | 项目工作目录 |
| `auto_test` | `true` | 自动运行测试（如订单含代码任务） |
| `require_review` | `true` | **人工质检关卡**（保持 `true`，见下） |

### crm（客户提醒）
| 键 | 默认 | 说明 |
| --- | --- | --- |
| `followup_days` | `2` | 提案发出后 N 天未回应则提醒跟进 |
| `deadline_alert_days` | `1` | 交付截止前 N 天提醒 |
| `payment_reminder_days` | `3` | 交付后 N 天未收款则提醒 |

### notify（通知）
| 键 | 默认 | 说明 |
| --- | --- | --- |
| `channel` | `none` | 通知渠道：`serverchan` / `dingtalk` / `email` / `none` |
| `serverchan.send_key` | 空 | ServerChan 的 SendKey（微信推送） |
| `dingtalk.webhook` | 空 | 钉钉机器人 webhook |
| `email.*` | 空 | SMTP 配置（smtp_host/port/user/password/from_addr/to_addrs） |
| `on_new_orders` / `on_shortlist` / `on_proposal_ready` / `on_delivery_review` | `true` | 各类事件是否推送 |

### scheduler（调度）
| 键 | 默认 | 说明 |
| --- | --- | --- |
| `radar_interval_min` | `60` | 雷达抓取间隔（分钟） |
| `screen_interval_min` | `60` | 筛选间隔（分钟） |
| `proposal_interval_min` | `120` | 提案生成间隔（分钟） |
| `crm_interval_min` | `180` | CRM 提醒检查间隔（分钟） |

---

## 七、使用方式

### 1. 手动跑一遍全流程（推荐日常用）

```powershell
python -m freelance_auto.cli once
# 等价于 scheduler 的 --once：radar → screen → proposal → crm 顺序执行后退出
```

或双击 `run.ps1` / `run.bat`（首次会安装依赖）。

### 2. 常驻调度（可选）

配置好 `config.yaml` 的 `scheduler` 间隔后：

```powershell
python -m freelance_auto.scheduler            # 常驻，按间隔自动跑
python -m freelance_auto.scheduler --once     # 立即跑一遍后退出（不进常驻）
```

启动时会打印每个任务的下次运行时间；`Ctrl+C` 停止。建议配合计划任务/进程守护，或干脆手动定期跑 `once`。

### 3. 各子命令

| 命令 | 作用 |
| --- | --- |
| `python -m freelance_auto.cli once` | 跑一遍全流程（radar→screen→proposal→crm） |
| `python -m freelance_auto.cli radar` | 只抓订单 |
| `python -m freelance_auto.cli screen` | 只 AI 筛选 |
| `python -m freelance_auto.cli proposal` | 只生成提案 |
| `python -m freelance_auto.cli crm` | 只跑 CRM 提醒检查 |
| `python -m freelance_auto.cli status` | 打印统计：各状态订单数、提案数、项目数、最近通知 |
| `python -m freelance_auto.cli approve 12` | 批准提案 #12（标记 approved，提示你手动去平台回复客户） |
| `python -m freelance_auto.cli delivery start 5` | 为订单 #5 启动交付项目（拆解任务） |
| `python -m freelance_auto.cli delivery start 5 --customer 张三` | 同上，附带客户名称 |
| `python -m freelance_auto.cli delivery run 3` | LLM 为项目 #3 生成各任务交付物 |
| `python -m freelance_auto.cli delivery review 7 --approve` | 人工质检：通过任务 #7 |
| `python -m freelance_auto.cli delivery review 7 --reject` | 人工质检：打回任务 #7 |
| `python -m freelance_auto.cli delivery export 3` | 导出项目 #3 的交付物（到工作目录） |
| `python -m freelance_auto.cli delivery export 3 --dest D:\out` | 导出到指定目录 |

安装后也可以用 console script（等价）：`freelance-auto once`、`freelance-auto status` 等。

### 4. 典型工作流（人工参与）

```
① 抓+筛+提案     →  python -m freelance_auto.cli once
② 查看候选/提案  →  python -m freelance_auto.cli status
③ 人工检查提案    →  python -m freelance_auto.cli approve 12
④ 手动去平台回复客户（系统不会替你发！）
⑤ 客户接受后     →  python -m freelance_auto.cli delivery start 5
⑥ 生成交付物     →  python -m freelance_auto.cli delivery run 3
⑦ 人工质检       →  python -m freelance_auto.cli delivery review 7 --approve
⑧ 交付给客户     →  python -m freelance_auto.cli delivery export 3
⑨ 定期催款提醒   →  python -m freelance_auto.cli crm
```

---

## 八、人工质检关卡（为什么必须有）

交付流水线在**任务生成 → 任务交付**之间强制设置了一道人关卡：

```
task todo ──▶ LLM 生成交付物（status: review）──▶ 人工质检 ──▶ done/打回
```

- 没有 `--approve`，任务不会进入 `done`，交付物不会对外导出；
- 只有人工 `delivery review <task_id> --approve` 才会放行；
- `--reject` 会把任务打回，重新生成。

**为什么必须有：**
1. **LLM 会自信地胡说。** 生成的代码/文档可能能用，也可能有隐蔽 bug、跑不通、甚至方向完全错了。你——而不是模型——对交付结果负全责。
2. **交付物代表你的信誉。** 一次烂交付比丢一单的代价大得多。系统帮你省掉"从零写"的时间，但"审一遍"的时间不能省。
3. **平台规则红线。** 很多平台明令禁止纯机器生成交付，被识别后轻则警告、重则封号（见「风险提示」）。

`config.yaml` 中 `delivery.require_review: true` 即此关卡，**请不要关掉**。

---

## 九、平台抓取注意事项

本系统抓取的是**公开网页**（电鸭社区、V2EX 外包板块），遵守以下约定：

- **限频**：默认每页 3 秒，见 `radar.request_interval_sec`。请勿把间隔调得太小，否则大概率触发平台风控。
- **礼貌爬虫**：带正常浏览器 UA 头、只抓公开列表页、不做并发轰炸。本系统已经内置。
- **被封风险**：任何抓取都有被限制访问（IP 封禁/验证码/封号）的风险。被抓后请：
  - 立即调大 `request_interval_sec`（如 5-10 秒）；
  - 降低 `max_pages`；
  - 用固定 IP / 住宅代理更稳妥；必要时关闭对应平台源（`enabled: false`）。
- **尊重平台条款**：请阅读目标平台的 robots 与用户协议。本工具仅用于个人接单的信息收集，请勿用于批量倒卖数据。

---

## 十、风险提示

1. **AI 交付必须人工 review。** 任何由 LLM 生成的任务交付物，在发给客户前必须经过你本人检查（系统已用质检关卡强制，别绕过它）。
2. **平台规则风险。** 自动抓取、自动发帖、机器生成交付物都可能违反平台条款，可能面临警告、封号、冻结账户。请遵守各平台规则，并对自己的行为负责。
3. **不保证收入。** 本系统只做信息收集与辅助生成，**不保证能接到单**，更不保证收入。中标取决于你的报价、能力、沟通和运气。
4. **提案需人工确认。** 提案默认需要 `approve` 后才算可发送，且需要你**手动到平台回复客户**——系统不会自动发消息。
5. **隐私与安全。** `.env` 含你的 API key，请勿提交到 git / 分享给他人。数据库含客户信息，请妥善保管。
6. **LLM 费用。** 筛选与提案生成会消耗 API 额度（DeepSeek 很便宜，但高频常驻仍会产生费用），请在 `config.yaml` 控制频率。
7. **数据非实时。** 抓取有延迟与去重，订单可能错过或重复，请以平台为准。

---

## 附录：常见问题

**Q: 双击 run.ps1 报"禁止运行脚本"？**
A: PowerShell 默认禁止脚本。可右键 `run.ps1` → 使用 PowerShell 运行；或临时放开：`Set-ExecutionPolicy -Scope Process Bypass`。也可以直接双击 `run.bat`。

**Q: `once` 报"未配置 LLM_API_KEY"？**
A: 把 `.env.example` 复制为 `.env`，填入 `LLM_API_KEY`（DeepSeek 在 https://platform.deepseek.com 申请）。

**Q: 数据存在哪？**
A: 全部在 `data\`：`freelance.db`（SQLite）、`app.log`（日志）、`workspaces\`（交付项目文件）。删掉 `data\` 等于清空重来。

**Q: 如何查看历史日志？**
A: `data\app.log`，或运行命令时终端会同步输出日志。

---

## 十一、全自动模式与自动发送提案

### 1. 全自动日常任务（Windows 计划任务）

每小时自动执行：抓单 → AI 筛选 → 生成提案 → 增量导出到桌面。

``powershell
# 注册计划任务（每小时）
C:\freelance-auto\setup_task.bat
# 或手动运行一次
powershell -ExecutionPolicy Bypass -File C:\freelance-auto\run_daily.ps1
``

运行日志在 `data\daily.log`，新提案自动导出到 `桌面\提案检阅` 文件夹（增量，只加新文件）。

### 2. 自动发送提案到平台（可选，有封号风险）

系统可以自动打开订单页面、粘贴投标书并提交回复。**使用前请评估平台风控风险**，建议新账号先在少量帖子上试跑。

``powershell
# 第一步：登录（弹出浏览器窗口，手动登录电鸭 + V2EX，登录态自动保存）
python C:\freelance-auto\login.py

# 第二步：批量发送已批准的提案（间隔 45-120 秒防风控）
python C:\freelance-auto\sender.py
``

发送成功的提案状态变为 `sent`，未找到回复框的会打印订单链接提示手动处理。

> ⚠️ 自动回复可能违反平台条款（V2EX/电鸭禁止机器人批量操作），存在封号风险，请谨慎使用并自行承担后果。建议保持人工审核提案内容后再发送。
