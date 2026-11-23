# -*- coding: utf-8 -*-
"""实验变量定义与实验设计（DOE）。

对应论文表 6 的 7 个实验变量，分三类：施法方式 / 法器性能 / 交互环境。
设计要点：
  1) 空间填充（LHS / Sobol）保证 7 维变量空间被均匀覆盖；
  2) 单因素极端点（OAT corners）：强制把每个叶子节点调至低效能端，
     否则 CoD 的 min() 会在高值处把所有样本"顶"到 100，参数不可辨识；
  3) 重复实验（replicates）：用于估计仿真噪声、给拟合一个噪声底线。
"""
import numpy as np
import pandas as pd
from scipy.stats import qmc

# name, 中文名, 类型, 低, 高, 单位/取值, 类别
VARIABLES = [
    ("mirror_start",    "星界探知镜启动时间",   "num",  0.0, 30.0, "min",  "施法方式"),
    ("golem_path", "感知魔像移动路径",   "cat",  0.0,  2.0, "-",    "施法方式"),
    ("golem_range", "感知魔像探知范围", "num", 200.0, 500.0, "km", "法器性能"),
    ("spirit_range", "浮游灵体探知范围", "num", 60.0, 120.0, "km", "法器性能"),
    ("golem_processing", "感知魔像感知信息处理速度", "num", 10.0, 60.0, "批/min", "法器性能"),
    ("ark_ward",  "浮空方舟防阵强度",   "num",  1.0, 10.0, "级",   "交互环境"),
    ("ark_speed", "浮空方舟离场速度",  "num", 10.0, 35.0, "kn",   "交互环境"),
]
VAR_NAMES = [v[0] for v in VARIABLES]
ROUTE_LABELS = ["前缘共鸣位", "侧缘共鸣位", "后缘共鸣位"]


def _to_frame(u):
    """把 [0,1]^7 的单位超立方采样还原为物理量。"""
    df = pd.DataFrame(columns=VAR_NAMES, index=range(len(u)))
    for k, (name, _, kind, lo, hi, _, _) in enumerate(VARIABLES):
        if kind == "cat":
            idx = np.clip((u[:, k] * 3).astype(int), 0, 2)
            df[name] = idx
        else:
            df[name] = lo + u[:, k] * (hi - lo)
    return df


def sample(n_runs=6000, seed=42, frac_lhs=0.60, frac_stress=0.20):
    """混合实验设计：LHS + 均匀随机 + 单因素极端点。"""
    rng = np.random.default_rng(seed)
    d = len(VARIABLES)
    n_lhs = int(n_runs * frac_lhs)
    n_stress = int(n_runs * frac_stress)
    n_rand = n_runs - n_lhs - n_stress

    parts = []
    # (1) 拉丁超立方：空间填充，保证主效应与低阶交互可估
    parts.append(qmc.LatinHypercube(d=d, seed=seed).random(n_lhs))
    # (2) 均匀随机：补充非规则区域
    parts.append(rng.random((n_rand, d)))
    # (3) 单因素极端点：每次把一个变量推到 0 或 1，其余随机 —— 专门为打破 min() 的绑定
    s = rng.random((n_stress, d))
    for i in range(n_stress):
        k = i % d
        s[i, k] = 0.0 if (i // d) % 2 == 0 else 1.0
    parts.append(s)

    u = np.vstack(parts)
    df = _to_frame(u)
    df.insert(0, "run_id", np.arange(len(df)))
    df["design"] = (["lhs"] * n_lhs) + (["rand"] * n_rand) + (["stress"] * n_stress)
    return df


def normalize_vars(df):
    """物理量 -> [0,1] 能力化变量 u1..u7（值越大越有利于圣光同盟）。"""
    u = {}
    u["u1"] = 1.0 - df["mirror_start"].to_numpy() / 30.0            # 星界探知镜越早开机越好
    u["u2"] = np.array([1.0, 0.65, 0.35])[df["golem_path"].to_numpy(dtype=int)]
    u["u3"] = (df["golem_range"].to_numpy() - 200.0) / 300.0
    u["u4"] = (df["spirit_range"].to_numpy() - 60.0) / 60.0
    u["u5"] = (df["golem_processing"].to_numpy() - 10.0) / 50.0
    u["u6"] = (df["ark_ward"].to_numpy() - 1.0) / 9.0          # 防阵强度（对圣光同盟不利）
    u["u7"] = (df["ark_speed"].to_numpy() - 10.0) / 25.0      # 离场速度（对圣光同盟不利）
    return {k: np.clip(v, 0.0, 1.0) for k, v in u.items()}


# ---------------------------------------------------------------------------
# 第二类实验：定向节点效能调节（对应论文图 8 的单节点 0→100 扫参，但改为"每条边都扫"）
# 目的：CoD 的 min() 只暴露 argmin 那条边，其余 beta 在自然实验里永不被绑定，
#       因此不可辨识。必须在"基准态势"下把每个供给节点逐个调至低端，
#       才能让每条边至少当一次瓶颈，beta 才可估。
# ---------------------------------------------------------------------------
def attunement_design(levels=(0.0, 15.0, 35.0, 60.0, 85.0, 100.0), n_rep=10,
                 sources=None, attunable_nodes=None, seed=7):
    """返回 (vars_df, se_src_df, attunement_dict)。

    vars_df 用变量中位值填充（此时生成端的"未建模直接效应"恰好为 0，
    使注入实验只检验依赖边本身，不掺入混杂）。
    attunable_nodes 中的内生节点（S5、S6）走"直接调节"通道：上游不变、自身效能降低，
    这样才能造出 S5 高 / S6 低这类组合，否则二者上游重合、参数无法分离。
    """
    rng = np.random.default_rng(seed)
    sources = sources or ["S1", "D2", "S3", "D4", "S8", "S10"]
    attunable_nodes = attunable_nodes or ["S5", "S6"]

    mid = {}
    for name, _, kind, lo, hi, _, _ in VARIABLES:
        mid[name] = 1 if kind == "cat" else (lo + hi) / 2.0

    rows_v, rows_s, rows_i = [], [], []
    for t in sources + attunable_nodes:
        for lv in levels:
            for _ in range(n_rep):
                rows_v.append(dict(mid, design="attunement", attuned_node=t, attuned_level=lv))
                se = {k: rng.uniform(82.0, 98.0) for k in sources}
                lvv = float(np.clip(lv + rng.normal(0, 1.0), 0.0, 100.0))
                inj = {k: np.nan for k in attunable_nodes}
                if t in sources:
                    se[t] = lvv
                else:
                    inj[t] = lvv
                rows_s.append(se)
                rows_i.append(inj)
    # 全高 / 全低 两个基准点
    for lv in (95.0, 5.0):
        for _ in range(n_rep * 2):
            rows_v.append(dict(mid, design="attunement", attuned_node="ALL", attuned_level=lv))
            rows_s.append({k: float(np.clip(lv + rng.normal(0, 1.0), 0.0, 100.0))
                           for k in sources})
            rows_i.append({k: np.nan for k in attunable_nodes})

    dv = pd.DataFrame(rows_v)
    dv.insert(0, "run_id", -1)
    ds = pd.DataFrame(rows_s)
    di = {k: np.array([r[k] for r in rows_i], dtype=float) for k in attunable_nodes}
    return dv, ds, di
