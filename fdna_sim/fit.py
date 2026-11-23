# -*- coding: utf-8 -*-
"""依赖关系参数求解（论文 5.3 节）：L-BFGS-B + 差分进化全局起点。

损失函数取 RMSE（论文步骤 2）。由于 CoD 的 min() 使目标函数分片光滑、
且不同实验点"绑定边"会切换，单起点梯度法容易落到局部极小，
因此先用差分进化（DE）给出全局初值，再用 L-BFGS-B 精修。
"""
import numpy as np
from scipy.optimize import differential_evolution, minimize

import fdna

D_BOUND = 10.0


def _unpack(p, k):
    return p[:k], p[k:2 * k], p[2 * k]


def _loss(p, x, y, k):
    a, b, d = _unpack(p, k)
    if d < -D_BOUND or d > D_BOUND:
        return 1e6
    return float(np.sqrt(np.mean((fdna.forward(x, a, b, d) - y) ** 2)))


def fit_receiver(x, y, n_starts=8, seed=0, use_de=True, de_maxiter=180):
    """x: (n, k) 供给节点 SE；y: (n,) 接收节点 SE。"""
    x = np.asarray(x, float)
    y = np.asarray(y, float)
    k = x.shape[1]
    bounds = [(0.0, 1.0)] * k + [(0.0, 100.0)] * k + [(-D_BOUND, D_BOUND)]
    rng = np.random.default_rng(seed)

    starts = []
    if use_de:
        de = differential_evolution(
            lambda p: _loss(p, x, y, k), bounds, seed=seed, maxiter=de_maxiter,
            popsize=12, tol=1e-8, polish=False, init="sobol")
        starts.append(de.x)
    for _ in range(n_starts):
        p = np.concatenate([rng.uniform(0.0, 0.9, k),
                            rng.uniform(10.0, 90.0, k),
                            [rng.uniform(-3.0, 3.0)]])
        starts.append(p)

    best = None
    for p0 in starts:
        r = minimize(_loss, p0, args=(x, y, k), method="L-BFGS-B", bounds=bounds,
                     options=dict(maxiter=3000, ftol=1e-12, gtol=1e-9))
        if best is None or r.fun < best.fun:
            best = r
    a, b, d = _unpack(best.x, k)
    pred = fdna.forward(x, a, b, d)
    ss_res = float(np.sum((y - pred) ** 2))
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    return dict(alpha=a, beta=b, delta=d,
                rmse=float(np.sqrt(ss_res / len(y))),
                r2=float(1.0 - ss_res / ss_tot) if ss_tot > 0 else np.nan,
                pred=pred)
