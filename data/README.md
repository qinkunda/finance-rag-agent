# 合成财务数据说明

本目录只包含统一平台演示和自动化测试使用的**合成数据**，不对应真实企业、人员、账户或交易。不得把真实财务文件、个人明细或密钥提交到本仓库。

## 1. 当前规模

| 数据 | 数量或内容 |
| --- | --- |
| 组织 | 5 个部门、9 类角色、15 名用户；其中苏离默认 `terminated` |
| 初始统一事项 | 22 条：18 条报销 + 4 条合同付款流程 |
| 费用报销 | 18 条种子，覆盖关闭、已支付、待支付、部门审批、财务审核、财务负责人复核、预算审批和退补 |
| 知识文档 | 14 条目录：10 份 PDF + 4 份 TXT |
| 知识 Chunk | 101 条；含 2 份 `archived` 旧版和 1 份 `pending` 差旅 V3 |
| 供应商 | 12 家，含启停／冻结状态、风险等级和账户指纹 |
| 合同 | 12 份合同、36 个里程碑，含草稿、失效和未审批状态 |
| 付款 | 24 笔申请、20 张发票、8 条验收记录 |
| 文件证据 | 10 条附件登记：合成发票、验收单、相对路径和 SHA-256 |
| 预算 | 8 个项目、8 笔申请，含冻结金额和超可用预算场景 |
| 全量对账 | 120 条银行流水、120 条总账记录，113 笔匹配、7 项差异 |
| `CNY-OPER-002` | 59 条银行、59 条总账，56 笔匹配、2 项差异 |
| 运行时写入 | 初始 `tickets=0`；运行时支付、凭证、对账批次、动作请求和变更记录均为 0 |

4 条初始合同付款流程为 PR-001、PR-002、PR-003、PR-007。其余付款申请保留为业务预检或待提交数据，例如周宁可从工作台提交 PR-019。对账差异只有运行批次后才生成统一事项，因此不计入初始 22 条。

## 2. 原始数据目录

| 路径 | 内容与作用 |
| --- | --- |
| `raw/organization/` | `departments.json`、`roles.json`、`users.json`；定义部门、成本中心、岗位、职级、汇报关系、项目归属、角色和启停状态 |
| `raw/expenses/` | `expense_reports.json`；18 条多状态报销种子，包含费用明细、超标金额、申请人、部门、项目、流程状态和异常 |
| `raw/policies/` | 10 份可提取文本的中文 PDF，覆盖付款、合同、预算、对账、发票、供应商、共享工单和异常流程；含 2 份归档版本 |
| `raw/vendors/` | 合成供应商主数据、账户指纹、状态和风险等级 |
| `raw/contracts/` | 合成合同及预付款、到货款、质保金里程碑 |
| `raw/transactions/` | `payment_requests.json`、`invoices.json`、`acceptances.json` 和 `payment_workflows.json`；前者为业务事实，后者定义 4 条初始统一流程 |
| `raw/attachments/` | 合成发票／验收 PDF 及 `attachments.json` 登记；记录关联单据、附件类型、路径和 SHA-256 |
| `raw/budget/` | 预算台账和申请；可用预算口径为 `年度预算 - 已承诺 - 已发生 - 冻结金额` |
| `raw/reconciliation/` | 银行流水和总账，内置金额差异、总账缺失、银行未达、重复银行流水、重复总账、币种差异和日期差异 |
| `raw/approval_matrix.csv` | 合成审批链规则 |
| `labels/` | 10 条制度检索金标准，含权限拒答、版本和复杂规则问题 |
| `catalog.json` | 唯一文档入口；管线只处理显式登记的文件 |
| `curated/` | 管线生成的 `parsed.jsonl`、`cleaned.jsonl`、`chunks.jsonl` 和 `manifest.json` |
| `finance_demo.db` | 从原始 JSON／CSV 构建的本地 SQLite 统一业务库，已被 `.gitignore` 排除 |

项目根目录 `knowledge/` 还包含 4 份 TXT：现行差旅 V2.1、报销单据表、月度成本执行表和待发布差旅 V3。它们与 PDF 一起进入同一知识管线。

## 3. 数据到底存在哪里

| 类型 | 存储位置 | 用途 |
| --- | --- | --- |
| 原始制度与文件证据 | PDF／TXT | 制度来源、合成发票／验收附件和哈希校验 |
| 结构化建库来源 | JSON／CSV | 可复现生成组织、合同、付款、预算、报销、流水和总账；不是运行时最终真源 |
| 中间知识产物 | JSONL／Manifest | 解析、清洗、切分、标签、血缘和检索输入 |
| 运行时业务事实 | SQLite `finance_demo.db` | 统一事项、流程、权限、预算、支付、凭证、对账、异常和审计查询／写入 |
| 语义索引 | 项目根目录 `chroma_db/unified/` | 统一 Chunk 的向量、Metadata、权限和版本过滤 |
| 关系图谱 | 查询时由 SQLite 生成 | 解释供应商、合同、发票、验收、支付和凭证关系，不另存业务真源 |

离线关键词模式不需要 Chroma，但仍使用统一 Chunk，因为切分同时服务于离线检索、在线向量检索、权限过滤、版本控制和引用血缘。不能据此声称离线模式已经完成语义向量检索。

## 4. SQLite 中的主要数据分组

| 分组 | 主要表 |
| --- | --- |
| 原业务事实 | `vendors`、`contracts`、`contract_milestones`、`payment_requests`、`invoices`、`acceptances`、`budgets`、`budget_requests`、`bank_transactions`、`ledger_entries` |
| 组织与身份 | `departments`、`users`、`roles`、`user_roles`、`sessions` |
| 统一事项与流程 | `work_items`、`tasks`、`workflow_events`、`exceptions`、`supplements` |
| 预算与财务结果 | `budget_reservations`、`payment_records`、`accounting_vouchers`、`reconciliation_runs` |
| 知识与证据状态 | `document_states`、`attachments` |
| 工程与审计 | `action_requests`、`change_events`、`audit_events` |
| 旧兼容写入 | `tickets`、旧差旅实验使用的 `expense_reports` 字段；新报销同样进入统一 `work_items` |

`work_items` 保存当前状态、当前处理人、当前角色、申请人、部门、项目、金额、制度版本标识和乐观版本。`workflow_events` 不覆盖历史；`tasks` 保留已完成任务；`change_events` 保存主数据变更前后快照。

## 5. 数据变化如何生效

- **离职与停用**：用户状态改为 `terminated`，原会话失效；未关闭待办转交给具备相同角色的有效接管人，历史审批保留。
- **调岗与角色变化**：当前角色通过 `user_roles.is_current` 生效，旧角色保留有效期；后续数据范围和动作权限使用新身份。
- **供应商变化**：状态、风险和账户指纹更新写入当前主数据，同时保留变更快照；后续预检重新读取数据库。
- **预算变化**：预算调整校验已承诺、已发生和已冻结之和；流程预留、释放和核销在同一 SQLite 事务中更新。
- **制度变化**：`document_states` 管理 `active`、`pending`、`superseded`、`archived` 和生效日期。检索每次读取状态；未来版本不会污染当前回答，历史日期可通过 `as_of` 查询。事项保留提交时的制度版本标识。
- **银行与总账**：原始 CSV 用于可复现建库；对账按运行批次保存统计与差异事项，不用新结果覆盖旧批次。
- **向量索引**：新增或更换知识文件后，先登记目录、执行知识管线，再重建 Chroma。文档发布只改变版本状态，不替代文件登记和索引。
- **关系图谱**：每次根据当前 SQLite 明确关系生成，业务数据变化后无需维护另一套手工三元组真源。

## 6. 重建与清理

### 只从当前提交的原始来源重建 SQLite

```bash
python scripts/build_finance_db.py
```

这是最常用的演示重置命令。它会删除并重建 `data/finance_demo.db`，清除本地会话、流程动作、运行时支付与凭证、对账批次、旧兼容工单和变更记录，恢复：

- 22 个初始统一事项；
- 18 条报销种子和 4 条付款流程种子；
- 120 条银行流水与 120 条总账；
- 孙岚、陈宇、V-009、FIN-2026 和制度状态的初始值；
- `tickets=0`，运行时动作、支付、凭证、对账批次和变更记录为 0。

### 只重建知识产物

```bash
python -m finance_platform.ingestion
```

### 从生成器完整重建全部合成来源

```bash
# 仅重新生成 PDF 时需要
pip install -r requirements-dev.txt

python scripts/generate_demo_corpus.py
python scripts/generate_enterprise_data.py
python scripts/build_finance_db.py
```

第一条生成基础 PDF／JSON／CSV、文档目录和知识产物；第二条扩充组织、报销、合同付款、附件与对账；第三条用扩充后的来源完成最终业务库重建。生成器只写入项目内合成文件，不读取本机真实业务文件或密钥。

### 可选真实向量索引

```bash
# 配置本地 .env 中的 DashScope Key 后
python scripts/build_vector_store.py
```

SQLite、Chroma、本地 `.env` 和模型目录均不提交 Git。

## 7. 血缘与验收口径

每条 Chunk 带有 `document_id`、`scenario`、`version`、`effective_date`、`status`、`access_level`、`labels`、`source_path` 和 SHA-256。`manifest.json` 记录文件哈希、解析器、清洗器、切分器和 Chunk 数，支持审计追溯。附件登记也保存文件 SHA-256。

自动化测试中的 Chroma 使用 `FakeEmbeddings`，只验证索引、读取、权限和版本过滤契约，不证明真实语义检索质量。真实 DashScope Embedding、在线 Chroma、BGE 精排和千问生成需要配置真实 Key 后单独验收。

真实企业文档接入前，应另行完成授权、分类分级、脱敏和来源登记。
