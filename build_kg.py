"""
build_kg.py — 读取 triples.json，构建知识图谱并生成交互式可视化
运行: python build_kg.py
输出: kg.html（双击浏览器打开，可拖拽/缩放的网状图）
"""
import json
import networkx as nx
from pyvis.network import Network


def main():
    triples = json.load(open("triples.json", encoding="utf-8"))

    G = nx.DiGraph()
    for t in triples:
        G.add_edge(t["head"], t["tail"], label=t["relation"])

    net = Network(height="750px", width="100%", directed=True,
                  bgcolor="#ffffff", font_color="#333")
    net.from_nx(G)
    try:
        net.show("kg.html", notebook=False)
    except TypeError:  # 兼容旧版 pyvis
        net.save_graph("kg.html")

    print(f"图谱构建完成: {G.number_of_nodes()} 个实体 / {G.number_of_edges()} 条关系")
    print("可视化: 双击打开 kg.html（可拖拽、缩放，点节点看关系）")


if __name__ == "__main__":
    main()