# -*- coding: utf-8 -*-
"""FDNA 正演：受限平均最弱链原则（论文式 (1)(2)，含修正项 delta）。"""
import numpy as np


def forward(x, alpha, beta, delta=0.0):
    """x: (n, k) 供给节点效能；alpha/beta: (k,)；返回 (n,) 接收节点效能。"""
    x = np.asarray(x, dtype=float)
    if x.ndim == 1:
        x = x.reshape(1, -1)
    a = np.asarray(alpha, dtype=float).reshape(-1)
    b = np.asarray(beta, dtype=float).reshape(-1)
    sod_ij = a * x + 100.0 * (1.0 - a)      # 正向供给
    sod = sod_ij.mean(axis=1)               # 均值聚合
    cod = (x + b).min(axis=1)               # 反向限制（最弱链）
    return np.minimum(sod, cod) + delta


def forward_chain(se_df, params, delta=None):
    """按拓扑序逐级传播：params[(feeder, receiver)] = (alpha, beta)。"""
    import net
    se = se_df.copy()
    delta = delta or {}
    for node in net.TOPO_ORDER:
        fs = net.feeders_of(node)
        if not fs:
            continue
        a = [params[(f, node)][0] for f in fs]
        b = [params[(f, node)][1] for f in fs]
        se[node] = np.clip(
            forward(se[fs].to_numpy(), a, b, delta.get(node, 0.0)), 0.0, 100.0)
    return se
