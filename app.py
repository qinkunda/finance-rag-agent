import streamlit as st
import pandas as pd
import glob
import json
import os
import time

st.set_page_config(page_title="RAG 实时查询监控", layout="wide")
st.title("📊 RAG 知识库实时窗口查询看板")

# 👇 基于脚本所在目录定位输出文件，避免工作目录不一致导致读不到数据
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_PATTERN = os.path.join(BASE_DIR, "output", "rag_window_stats", "*.json")

# 自动刷新机制 (每5秒刷新一次)
if st.button("🔄 手动刷新"):
    st.rerun()

data = []
files = glob.glob(OUTPUT_PATTERN)

if files:
    for file in files:
        try:
            with open(file, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line:
                        data.append(json.loads(line))
        except Exception as e:
            st.warning(f"读取文件失败: {os.path.basename(file)} - {e}")

    if data:
        df = pd.DataFrame(data)
        df['window_start'] = pd.to_datetime(df['window_start'])
        df['window_end'] = pd.to_datetime(df['window_end'])

        # 窗口化数据展示
        st.subheader("📋 最新窗口聚合结果")
        st.dataframe(
            df.sort_values('window_start', ascending=False),
            use_container_width=True,
            hide_index=True
        )

        # 趋势图表
        st.subheader("📈 各文档查询量统计")
        chart_data = df.groupby('doc_id')['query_count'].sum().reset_index()
        st.bar_chart(chart_data.set_index('doc_id'))
    else:
        st.info("⏳ JSON 文件存在但暂无有效数据，等待 Flink 窗口触发...")
else:
    st.info(f"⏳ 等待 Flink 写入数据...\n\n监控路径: `{OUTPUT_PATTERN}`")

# 👇 每 5 秒自动刷新页面
time.sleep(5)
st.rerun()