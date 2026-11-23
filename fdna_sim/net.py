# -*- coding: utf-8 -*-
"""给定协同阵界的能力依赖关系网络定义（王玉帅 2025, 图6 / 表7）。

11 个能力节点、18 条依赖边。边参数 (alpha, beta) 采用论文表 7 的拟合结果，
在本模拟器中作为"真值"(simulation reference)，用于检验参数恢复能力。
"""

NODES = ["S1", "D2", "S3", "D4", "S5", "S6", "F7", "S8", "F9", "S10", "F11"]

# 结构性源节点：无入边
#   LEAF  : 论文表 6 明确由实验变量驱动的叶子节点
#   QUASI : 结构上同样是源节点，但论文未把它们列入实验变量（本文按"补充基础"处理）
LEAF = ["S1", "D2", "S3", "D4"]
QUASI = ["S8", "S10"]
ENDO = ["S5", "S6", "F7", "F9", "F11"]          # 由 FDNA 聚合得到
SOURCES = LEAF + QUASI

# (feeder, receiver, alpha_ij, beta_ij)  —— 表 7
EDGES = [
    ("S1",  "S5", 0.21, 34), ("D2", "S5", 0.18, 56), ("D4", "S5", 0.43, 62),
    ("S1",  "S6", 0.18, 48), ("D2", "S6", 0.23, 59), ("D4", "S6", 0.51, 57),
    ("S5",  "F7", 0.33, 38), ("S6", "F7", 0.37, 42),
    ("S3",  "F9", 0.12, 43), ("S5", "F9", 0.03, 68), ("S6", "F9", 0.06, 69),
    ("S8",  "F9", 0.43, 26), ("S10", "F9", 0.17, 75),
    ("S3", "F11", 0.11, 49), ("S5", "F11", 0.04, 64), ("S6", "F11", 0.08, 65),
    ("S10", "F11", 0.15, 81), ("S8", "F11", 0.39, 33),
]

# 论文 4.2 节强调的拓扑序：保证拟合/传播时供给节点总在接收节点之前
TOPO_ORDER = ["S1", "D2", "S3", "D4", "S8", "S10", "S5", "S6", "F7", "F9", "F11"]

SPELL_CHAINS = {
    "KC1": {  # 暮影学会目标：暮影浮空方舟
        "target": "暮影浮空方舟",
        "nodes": [["S1", "D2", "D4"], ["S5", "S6"], ["F7"]],
        "terminal": "F7",
    },
    "KC2": {  # 暮影学会目标：暮影感知魔像
        "target": "暮影感知魔像",
        "nodes": [["S8", "S10"], ["S3"], ["F9", "F11"]],
        "terminal": "F9",
    },
}


def feeders_of(receiver):
    """返回某接收节点的供给节点列表（按 EDGES 顺序）。"""
    return [e[0] for e in EDGES if e[1] == receiver]


def truth_params(receiver):
    """返回接收节点的真值 (alpha, beta) 数组。"""
    fs = feeders_of(receiver)
    d = {(e[0], e[1]): (e[2], e[3]) for e in EDGES}
    a = [d[(f, receiver)][0] for f in fs]
    b = [d[(f, receiver)][1] for f in fs]
    return fs, a, b


def edge_count():
    return len(EDGES)
