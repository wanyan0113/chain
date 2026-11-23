# -*- coding: utf-8 -*-
"""实验数据模拟器主流程：实验设计 -> 生成数据 -> 求解 SoD/CoD 参数 -> 报告。

两类实验叠加：
  A. 变量驱动（LHS+随机+单因素极端，6000 量级）——对应论文"探索性仿真实验"；
  B. 定向节点效能调节（每个源节点 6 档扫参）——补足 beta 的可辨识性。

用法：
    python run_demo.py --mode realistic --runs 6000
    python run_demo.py --mode realistic --runs 6000 --no_attunement   # 消融：去掉注入实验
    python run_demo.py --mode clean --pure --runs 600            # 无偏自检
"""
import argparse
import json
import os

import numpy as np
import pandas as pd

import fit
import leaf_model
import net
import variables

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")
os.makedirs(OUT, exist_ok=True)


def build_dataset(args, rng, gamma, sigma_run, sigma_ind, sigma_node):
    # ---------- A. 变量驱动实验 ----------
    doe = variables.sample(n_runs=args.runs, seed=args.seed)
    u = variables.normalize_vars(doe)
    ind_a, se_src_a = leaf_model.gen_source_se(u, rng, sigma_ind=sigma_ind)
    se_a, _ = leaf_model.propagate(se_src_a, u, rng, gamma=gamma,
                                  sigma_run=sigma_run, sigma_node=sigma_node)
    a = pd.concat([doe, ind_a.add_prefix("ind_"), se_a], axis=1)
    a["attuned_node"] = None
    a["attuned_level"] = np.nan

    if not args.with_attunement:
        data = a
    else:
        # ---------- B. 定向节点效能调节 ----------
        dv, ds, di = variables.attunement_design(n_rep=args.attunement_repeats, seed=args.seed + 11)
        uv = variables.normalize_vars(dv)
        se_b, _ = leaf_model.propagate(ds, uv, rng, gamma=gamma,
                                       sigma_run=sigma_run, sigma_node=sigma_node,
                                       attunements=di)
        b = pd.concat([dv, pd.DataFrame(index=dv.index), se_b], axis=1)
        b = b.reindex(columns=list(a.columns))
        data = pd.concat([a, b], ignore_index=True)
        data["run_id"] = np.arange(len(data))

    # 法术链闭合效能（报告口径，见 leaf_model.spell_chain_effect）
    kc = leaf_model.spell_chain_effect(data)
    data["KC1"] = kc["KC1"].to_numpy()
    data["KC2"] = kc["KC2"].to_numpy()
    return data


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["clean", "realistic"], default="realistic")
    ap.add_argument("--runs", type=int, default=6000)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--n_starts", type=int, default=6)
    ap.add_argument("--attunement_repeats", type=int, default=10)
    ap.add_argument("--pure", action="store_true", help="完全无噪声，校验数学链路")
    ap.add_argument("--no_attunement", dest="with_attunement", action="store_false",
                    help="消融：不使用节点效能调节实验")
    ap.add_argument("--sigma_ind", type=float, default=0.06)
    ap.add_argument("--sigma_node", type=float, default=1.5)
    args = ap.parse_args()

    rng = np.random.default_rng(args.seed + 1)
    noisy = not args.pure
    gamma = 0.0 if (args.mode == "clean" or args.pure) else 1.0
    sigma_run = 2.5 if (noisy and args.mode != "clean") else 0.0
    sigma_ind = args.sigma_ind if noisy else 0.0
    sigma_node = args.sigma_node if noisy else 0.0

    data = build_dataset(args, rng, gamma, sigma_run, sigma_ind, sigma_node)
    tag = args.mode + ("" if args.with_attunement else "_noattunement")
    data.to_csv(os.path.join(OUT, f"data_{tag}.csv"), index=False, encoding="utf-8-sig")

    # 逐接收节点求解（损失对接收节点可分离；用"观测到的"供给节点 SE，避免误差级联）
    # 注意：对被注入的节点本身，其观测值被人工改写、不满足依赖模型，拟合时必须剔除，
    #       否则这些"不可能点"会把参数整体拉偏（实测造成 ~8 的系统偏差）。
    rows, preds = [], {}
    for node in net.ENDO:
        fs, a_true, b_true = net.truth_params(node)
        keep = data["attuned_node"].ne(node).to_numpy()
        res = fit.fit_receiver(data.loc[keep, fs].to_numpy(),
                               data.loc[keep, node].to_numpy(),
                               n_starts=args.n_starts, seed=args.seed)
        full = np.full(len(data), np.nan)
        full[keep] = res["pred"]
        preds[node] = full
        for i, f in enumerate(fs):
            rows.append(dict(receiver=node, feeder=f,
                             alpha_true=a_true[i], alpha_fit=res["alpha"][i],
                             beta_true=b_true[i], beta_fit=res["beta"][i],
                             rmse=res["rmse"], pseudo_r2=res["r2"]))
        rows.append(dict(receiver=node, feeder="<delta>", alpha_true=np.nan,
                         alpha_fit=np.nan, beta_true=np.nan, beta_fit=np.nan,
                         rmse=res["rmse"], pseudo_r2=res["r2"],
                         delta=res["delta"]))
    params = pd.DataFrame(rows)
    params.to_csv(os.path.join(OUT, f"params_{tag}.csv"), index=False,
                  encoding="utf-8-sig")

    e = params[params.feeder != "<delta>"]
    a_err = (e.alpha_fit - e.alpha_true).abs()
    b_err = (e.beta_fit - e.beta_true).abs()
    summary = dict(
        mode=args.mode, with_attunement=args.with_attunement, runs=int(len(data)),
        gamma=gamma, sigma_run=sigma_run,
        n_nodes=len(net.NODES), n_edges=net.edge_count(),
        alpha_mae=round(float(a_err.mean()), 5), alpha_max_err=round(float(a_err.max()), 5),
        beta_mae=round(float(b_err.mean()), 3), beta_max_err=round(float(b_err.max()), 3),
        fit={n: dict(rmse=round(float(params[params.receiver == n].rmse.iloc[0]), 3),
                     pseudo_r2=round(float(params[params.receiver == n].pseudo_r2.iloc[0]), 3),
                     delta=round(float(params[(params.receiver == n) &
                                              (params.feeder == "<delta>")].delta.iloc[0]), 3))
             for n in net.ENDO},
        se_range={n: [round(float(data[n].quantile(.05)), 1),
                      round(float(data[n].quantile(.95)), 1)] for n in net.NODES},
        spell_chain={k: dict(mean=round(float(data[k].mean()), 1),
                           p05=round(float(data[k].quantile(.05)), 1),
                           p95=round(float(data[k].quantile(.95)), 1))
                   for k in ("KC1", "KC2")},
    )
    with open(os.path.join(OUT, f"summary_{tag}.json"), "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2)

    try:
        make_fig(data, preds, e, tag)
    except Exception as exc:
        print("[warn] 绘图失败:", exc)

    print(json.dumps(summary, ensure_ascii=False, indent=2))


def make_fig(data, preds, edge_rows, tag):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False

    fig, ax = plt.subplots(1, 3, figsize=(15, 4.3))
    cols = {"S5": "#4C78A8", "S6": "#72B7B2", "F7": "#F58518", "F9": "#E45756",
            "F11": "#54A24B"}
    for n in net.ENDO:
        m = ~np.isnan(preds[n])
        ax[0].scatter(data[n].to_numpy()[m], preds[n][m], s=3, alpha=.22,
                      label=n, c=cols[n])
    ax[0].plot([0, 100], [0, 100], "k--", lw=1)
    ax[0].set_xlabel("仿真观测效能 SE")
    ax[0].set_ylabel("FDNA 拟合效能")
    ax[0].set_title("(a) 拟合效果")
    ax[0].legend(markerscale=3, fontsize=8, loc="upper left")

    for n in net.ENDO:
        m = ~np.isnan(preds[n])
        ax[1].hist(data[n].to_numpy()[m] - preds[n][m], bins=60, alpha=.45,
                   label=n, color=cols[n])
    ax[1].set_xlabel("残差（观测 − 拟合）")
    ax[1].set_title("(b) 残差分布")
    ax[1].legend(fontsize=8)

    ax[2].scatter(edge_rows.alpha_true, edge_rows.alpha_fit, s=50, c="#4C78A8",
                  label=r"$\alpha_{ij}$ (SoD)")
    ax[2].scatter(edge_rows.beta_true / 100, edge_rows.beta_fit / 100, s=50,
                  marker="^", c="#E45756", label=r"$\beta_{ij}/100$ (CoD)")
    ax[2].plot([0, 1], [0, 1], "k--", lw=1)
    ax[2].set_xlabel("模拟参照（表 7）")
    ax[2].set_ylabel("拟合值")
    ax[2].set_title("(c) 依赖参数恢复")
    ax[2].legend(fontsize=8)

    for a in ax:
        a.grid(alpha=.25)
    fig.suptitle(f"FDNA 数据模拟器自检  |  {tag}", y=1.02)
    fig.tight_layout()
    p = os.path.join(OUT, f"fig_fit_{tag}.png")
    fig.savefig(p, dpi=140, bbox_inches="tight")
    print("[ok] figure ->", p)


if __name__ == "__main__":
    main()
