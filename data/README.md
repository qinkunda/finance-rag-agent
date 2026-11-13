# 合成财务数据说明

本目录只包含用于演示和自动化测试的**合成数据**，不对应真实企业、人员、账户或交易。

## 数据分层

- `raw/policies/`：6 份可提取文本的中文 PDF，覆盖付款、合同、预算、对账、发票和共享工单。
- `raw/contracts/`：合成合同及付款里程碑。
- `raw/transactions/`：正常、缺验收单、金额不一致三类付款申请。
- `raw/budget/`：预算台账和正常／超预算申请。
- `raw/reconciliation/`：银行流水和总账，内置金额差异、缺失、未达及重复记录。
- `labels/`：制度检索金标准，含权限拒答用例。
- `curated/`：由管线生成的解析文本、清洗文本、Chunk 和血缘清单。
- `catalog.json`：唯一文档入口；管线不会扫描未登记目录。

## 重建

```bash
# 仅需要重新生成 PDF 时安装开发依赖
pip install -r requirements-dev.txt
python scripts/generate_demo_corpus.py
python -m finance_platform.ingestion
```

每条 Chunk 带有 `document_id`、`scenario`、`version`、`effective_date`、`access_level`、`labels`、`source_path` 和 SHA-256。`manifest.json` 记录文件哈希、解析器、清洗器、切分器和 Chunk 数，支持审计追溯。

真实企业文档接入前，应另行完成授权、分类分级、脱敏和来源登记；不要直接把真实财务文件提交到本仓库。
