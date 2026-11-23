# -*- coding: utf-8 -*-
""""施法推演平台"的代理模型（data generator / simulation reference）。

分三段，便于"变量驱动"与"节点效能调节"两类实验共用同一套物理：
  1) gen_source_se   : 实验变量 -> 叶子/补充基础节点的指标 -> 归一化 SE
  2) propagate       : 真值 FDNA 逐级聚合（含 FDNA 未刻画的直接效应与场域随机扰动）
  3) generate        : 1+2 的便捷封装

为什么生成端不是"用 FDNA 生成再拿 FDNA 拟合"（那样是循环论证）：
  (a) 指标层由物理化的变量响应函数产生，含测量噪声；
  (b) 聚合层额外注入 FDNA 结构上没有入边的直接效应（方舟防阵/离场对 F7、
      浮游灵体探知对 S5/S6、感知魔像探知对 F9/F11）与跨节点共享的随机扰动。
  这两项正是论文 5.4.1 自陈"指标不精细、不确定性无法穷尽"的来源，
  因此拟合残差是有物理含义的，而不是纯噪声。
clean 模式（gamma=0, sigma_run=0）可验证链路本身无偏。
"""
import numpy as np
import pandas as pd

from net import NODES, feeders_of, truth_params
import fdna

# 表 5 的效能指标类型与归一化区间
INDICATOR = {
    "S1":  dict(name="发现目标时间",           kind="cost",    lo=2.0,  hi=30.0),
    "D2":  dict(name="感知信息融合完成时间",        kind="cost",    lo=3.0,  hi=25.0),
    "S3":  dict(name="发现目标次数",            kind="benefit", lo=0.0,  hi=20.0),
    "D4":  dict(name="感知信息融合完成时间",        kind="cost",    lo=3.0,  hi=27.0),
    "S8":  dict(name="有效发现并灵性锁定目标次数",   kind="benefit", lo=0.0,  hi=13.0),
    "S10": dict(name="有效发现并灵性锁定目标次数",   kind="benefit", lo=0.0,  hi=11.0),
}
SOURCE_NODES = list(INDICATOR.keys())


def _norm(x, meta):
    if meta["kind"] == "cost":
        return np.clip(100.0 * (meta["hi"] - x) / (meta["hi"] - meta["lo"]), 0.0, 100.0)
    return np.clip(100.0 * (x - meta["lo"]) / (meta["hi"] - meta["lo"]), 0.0, 100.0)


def gen_source_se(u, rng, sigma_ind=0.06):
    """变量 -> 指标 -> 源节点 SE。返回 (ind_df, se_df[仅源节点])。"""
    n = len(u["u1"])
    ind = {}
    ind["S1"] = 2 + 28 * (1 - (0.60 * u["u1"] + 0.40 * u["u3"])) ** 1.2
    ind["D2"] = 3 + 22 * (1 - (0.75 * u["u5"] + 0.25 * u["u3"])) ** 1.1
    ind["S3"] = 2 + 18 * (0.60 * u["u3"] + 0.25 * u["u1"] + 0.15 * u["u2"]) ** 1.2
    ind["D4"] = 3 + 24 * (1 - (0.60 * u["u5"] + 0.40 * u["u4"])) ** 1.1
    ind["S8"] = 1 + 12 * (0.55 * u["u3"] + 0.45 * u["u2"]) ** 1.1
    ind["S10"] = 1 + 10 * (0.45 * u["u3"] + 0.35 * u["u2"] + 0.20 * u["u1"]) ** 1.1

    for k in ind:
        ind[k] = np.clip(ind[k] * (1.0 + sigma_ind * rng.standard_normal(n)),
                         0.0, INDICATOR[k]["hi"] * 1.2)
    se = pd.DataFrame({k: _norm(v, INDICATOR[k]) for k, v in ind.items()})
    return pd.DataFrame(ind), se


def propagate(se_src, u, rng, gamma=1.0, sigma_run=2.5, sigma_node=1.5,
              delta_true=None, attunements=None):
    """在源节点 SE 已知的前提下，按真值 FDNA 聚合出 5 个内生节点。

    attunements: {node: array}，对指定节点做"直接调节"（节点效能调节），
            其上游不受影响，下游按注入后的值继续传播。物理含义是
            "该能力被单独干扰（传讯中断、效能受调节）"，与只改平台性能参数不同。
    """
    se = se_src.copy()
    n = len(se)
    z_run = sigma_run * rng.standard_normal(n)      # 场域随机扰动（跨节点共享）
    delta_true = delta_true or {}
    attunements = attunements or {}

    # FDNA 结构之外、预设的直接效应（clean 模式下置零）
    direct = {
        "S5":  gamma * (10.0 * (u["u4"] - 0.5) - 4.0 * (u["u6"] - 0.5)),
        "S6":  gamma * (8.0 * (u["u4"] - 0.5) - 4.0 * (u["u7"] - 0.5)),
        "F7":  gamma * (-14.0 * (u["u6"] - 0.5) - 10.0 * (u["u7"] - 0.5)),
        "F9":  gamma * (6.0 * (u["u3"] - 0.5)),
        "F11": gamma * (4.0 * (u["u3"] - 0.5)),
    }

    for node in ["S5", "S6", "F7", "F9", "F11"]:
        fs, a, b = truth_params(node)
        x = se[fs].to_numpy()
        y = fdna.forward(x, np.array(a), np.array(b), delta_true.get(node, 0.0))
        y = y + direct[node] + z_run / 2.0 + sigma_node * rng.standard_normal(n)
        if node in attunements:
            arr = np.asarray(attunements[node], dtype=float)
            y = np.where(np.isnan(arr), y, arr)
        se[node] = np.clip(y, 0.0, 100.0)
    return se[NODES], pd.DataFrame(direct)


def generate(u, rng, **kw):
    ind, se_src = gen_source_se(u, rng, sigma_ind=kw.pop("sigma_ind", 0.06))
    se, _ = propagate(se_src, u, rng, **kw)
    return ind, se


def spell_chain_effect(se_df):
    """法术链闭合效能（L5 报告口径，不参与参数拟合）。

    组内并联取 max（同环节的备份力量），组间串联取 min（最弱环节决定闭合）。
    KC2 按论文表 4：((S8,S10), S3) + (F9,F11) —— S5、S6 属跨链供给，不计入本链环节。
    该口径可替换（如取加权平均、或改为"链上最小节点"），故单列而不并入 SE 表。
    """
    import net
    out = {}
    for kc, cfg in net.SPELL_CHAINS.items():
        vals = np.full(len(se_df), 100.0)
        for group in cfg["nodes"]:
            vals = np.minimum(vals, se_df[group].max(axis=1).to_numpy())
        out[kc] = vals
    return pd.DataFrame(out)
