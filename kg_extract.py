"""
kg_extract.py — 用千问从制度文本抽取知识图谱三元组
运行: python kg_extract.py
输入: knowledge/ 下的制度 txt（取第一个）
输出: triples.json（JSON 数组：[{"head","relation","tail"}]）
"""
import json
import os
import re
import glob

from langchain_openai import ChatOpenAI
from config import DASHSCOPE_API_KEY, DASHSCOPE_BASE_URL

PROMPT_TEMPLATE = """你是企业知识图谱抽取器。从下面的企业制度文本中抽取三元组（实体-关系-实体）。

【schema 规则】
1. 实体关注：职级（普通员工/经理级/总监级）、城市（深圳/北京/成都等）、城市分级（一类/新一线/二类/三类）、费用类型（住宿/伙食补助/市内交通）、员工姓名、单据号
2. 关系用简洁词：住宿标准、伙食标准、属于、职级为、申请、目的地为、审批链包含
3. 标准类三元组格式示例：
   {{"head": "普通员工", "relation": "住宿标准", "tail": "一类城市500元/晚"}}
   {{"head": "深圳", "relation": "属于", "tail": "一类城市"}}
4. 只输出 JSON 数组，不要任何其他文字，不要 markdown 代码块。

【制度文本】
{doc}
"""


def find_doc():
    files = sorted(glob.glob(os.path.join("knowledge", "*.txt")))
    if not files:
        raise FileNotFoundError("knowledge/ 下没有找到制度 txt")
    return files[0]


def read_doc(path):
    for enc in ("utf-8", "gbk", "gb18030"):
        try:
            return open(path, encoding=enc).read()
        except UnicodeDecodeError:
            continue
    return open(path, encoding="utf-8", errors="ignore").read()


def parse_json_array(text):
    """容错解析：从模型输出中提取 JSON 数组"""
    text = text.strip()
    text = re.sub(r"^```(json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\[.*\]", text, re.DOTALL)
        if m:
            return json.loads(m.group(0))
        raise


def main():
    doc_path = find_doc()
    doc = read_doc(doc_path)
    print(f"读取制度文档: {os.path.basename(doc_path)} ({len(doc)} 字)")

    llm = ChatOpenAI(
        model="qwen-turbo",   # 如额度不足改为 "qwen3.6-flash" 或 "qwen-plus"
        base_url=DASHSCOPE_BASE_URL,
        api_key=DASHSCOPE_API_KEY,
        temperature=0,
    )
    resp = llm.invoke(PROMPT_TEMPLATE.format(doc=doc[:4000]))
    triples = parse_json_array(resp.content)
    triples = [t for t in triples if "head" in t and "relation" in t and "tail" in t]

    with open("triples.json", "w", encoding="utf-8") as f:
        json.dump(triples, f, ensure_ascii=False, indent=2)
    print(f"抽取完成: {len(triples)} 条三元组 -> triples.json")
    for t in triples[:5]:
        print(f"  {t['head']} --[{t['relation']}]--> {t['tail']}")


if __name__ == "__main__":
    main()