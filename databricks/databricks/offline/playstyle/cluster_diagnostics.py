"""군집이 '어떻게' 나뉘었는지 수치로 설명하고, 그 설명을 서빙 아티팩트로 내보낸다.

04 노트북(학습)에서 Cell 13(모델 학습) ~ Cell 18(서빙 JSON) 사이에 끼워 넣는다.
입력은 이미 만들어져 있는 객체 그대로:
    pdf            : z_*, s_*, team_position, cluster, win 이 들어 있는 pandas DataFrame
    style_models   : {position: sklearn KMeans}
    STYLE_FEATURES / PERF_FEATURES / S_COLS / STYLE_LABELS

출력은 두 가지.
    1) 사람이 읽는 진단 테이블 (발표 자료/검증용)
    2) style_model.json 에 넣을 explain 블록 (추론 시 설명·신뢰도 계산에 사용)
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


# ----------------------------------------------------------------------
# 기본 거리/소속 계산
# ----------------------------------------------------------------------
def pairwise_distance(X: np.ndarray, centers: np.ndarray) -> np.ndarray:
    """(n, k) 거리 행렬. X 는 이미 프로파일 센터링된 스타일 벡터."""
    return np.linalg.norm(X[:, None, :] - centers[None, :, :], axis=2)


def centroid_separation(centers: np.ndarray) -> float:
    """중심점들이 서로 얼마나 떨어져 있는지 (평균 쌍거리).

    margin 을 이 값으로 나눠 포지션 간 비교가 가능한 스케일로 만든다.
    """
    k = len(centers)
    if k < 2:
        return 1.0
    dist = pairwise_distance(centers, centers)
    return float(dist[np.triu_indices(k, 1)].mean())


def soft_membership(distances: np.ndarray, temperature: float) -> np.ndarray:
    """거리 → 소속 확률. 가우시안 커널 softmax.

    하드 배정(argmin)은 10경기에서 한두 판만 흔들려도 대표 스타일이 바뀐다.
    소속 확률로 합산하면 '경계에 걸친 경기'가 절반씩 기여하므로 훨씬 안정적이다.
    """
    temp = max(float(temperature), 1e-6)
    logits = -(distances ** 2) / (2 * temp ** 2)
    logits = logits - logits.max(axis=-1, keepdims=True)
    weights = np.exp(logits)
    return weights / weights.sum(axis=-1, keepdims=True)


def assignment_quality(X: np.ndarray, centers: np.ndarray) -> pd.DataFrame:
    """경기 단위 배정 품질: 1순위/2순위 거리, margin, 애매도."""
    dist = pairwise_distance(X, centers)
    order = np.argsort(dist, axis=1)
    first = order[:, 0]
    second = order[:, 1] if dist.shape[1] > 1 else order[:, 0]
    d1 = dist[np.arange(len(X)), first]
    d2 = dist[np.arange(len(X)), second]
    sep = centroid_separation(centers)
    return pd.DataFrame({
        "cluster": first,
        "runner_up": second,
        "d1": d1,
        "d2": d2,
        "margin": d2 - d1,
        "margin_norm": (d2 - d1) / sep,
    })


# ----------------------------------------------------------------------
# 진단 1 — 군집별 축 해석
# ----------------------------------------------------------------------
def cluster_axis_table(
    centers: np.ndarray,
    style_features: list[str],
    top_n: int = 3,
) -> pd.DataFrame:
    """각 군집 중심이 '어느 축으로 얼마나' 튀어 있는지.

    센터링 때문에 6개 값의 합이 0 이므로, 절댓값 상위 축만 보면
    그 군집의 성격이 그대로 드러난다.
    """
    rows = []
    for cluster, center in enumerate(centers):
        order = np.argsort(-np.abs(center))[:top_n]
        rows.append({
            "cluster": cluster,
            "축_요약": " / ".join(
                f"{style_features[i]} {center[i]:+.2f}" for i in order
            ),
            "지배축": style_features[int(order[0])],
            "지배축_값": round(float(center[order[0]]), 3),
            "중심_노름": round(float(np.linalg.norm(center)), 3),
        })
    return pd.DataFrame(rows)


# ----------------------------------------------------------------------
# 진단 2 — 실력 등급 누수 점검
# ----------------------------------------------------------------------
# 스타일 축으로 만들어진 성과 지표. 이걸 누수 판정에 넣으면 순환 논리가 된다.
#   resource_efficiency = damage_share / gold_share  (둘 다 스타일 축)
#   damage_per_min      ~ damage_share 와 구조적으로 붙어 있음
DERIVED_PERF = {"resource_efficiency", "damage_per_min"}


def skill_leak_check(
    sub: pd.DataFrame,
    perf_features: list[str],
    exclude_derived: bool = True,
) -> dict[str, Any]:
    """군집이 '스타일'이 아니라 '잘함/못함'으로 갈렸는지 확인한다.

    전반적 성과 수준 = 성과 축 z 의 행 평균.
    군집 간 이 값의 분산 비율(eta^2)이 크면 센터링이 덜 된 것이다.
      eta^2 < 0.05  : 스타일 축이 성과와 분리됨 (정상)
      0.05 ~ 0.15   : 약한 누수
      > 0.15        : 군집이 사실상 실력 등급 (STYLE_FEATURES 재검토 필요)
    """
    def eta(cols):
        if not cols:
            return None
        level = sub[cols].mean(axis=1)
        grand = level.mean()
        between = sum(
            len(g) * (level[g.index].mean() - grand) ** 2
            for _, g in sub.groupby("cluster")
        )
        total = float(((level - grand) ** 2).sum())
        return float(between / total) if total > 0 else 0.0

    all_cols = [f"z_{f}" for f in perf_features if f"z_{f}" in sub.columns]
    if not all_cols or sub["cluster"].nunique() < 2:
        return {"eta_squared": None, "eta_squared_all": None,
                "verdict": "판정 불가", "by_cluster": {}}

    clean_cols = [c for c in all_cols if c[2:] not in DERIVED_PERF]
    eta2_all = eta(all_cols)
    eta2 = eta(clean_cols) if exclude_derived and clean_cols else eta2_all

    z_cols = clean_cols or all_cols
    level = sub[z_cols].mean(axis=1)

    if eta2 < 0.05:
        verdict = "정상 — 스타일과 실력이 분리됨"
    elif eta2 < 0.15:
        verdict = "주의 — 약한 실력 누수"
    else:
        verdict = "위험 — 군집이 실력 등급에 가까움"

    return {
        "eta_squared": round(eta2, 4),
        "eta_squared_all": round(eta2_all, 4) if eta2_all is not None else None,
        "verdict": verdict,
        "by_cluster": {
            int(c): round(float(level[g.index].mean()), 3)
            for c, g in sub.groupby("cluster")
        },
    }


# ----------------------------------------------------------------------
# 진단 3 — 포지션 단위 종합 리포트
# ----------------------------------------------------------------------
def build_position_report(
    pdf: pd.DataFrame,
    position: str,
    centers: np.ndarray,
    style_features: list[str],
    perf_features: list[str],
    s_cols: list[str],
    labels: dict[int, dict[str, str]] | None = None,
    ambiguous_cut: float = 0.15,
) -> dict[str, Any]:
    sub = pdf[pdf["team_position"] == position].copy()
    X = sub[s_cols].to_numpy(dtype=float)
    quality = assignment_quality(X, centers)
    quality.index = sub.index

    # 학습 시 저장한 cluster 와 재계산 결과가 어긋나면 매핑이 깨진 것이다.
    mismatch = int((quality["cluster"].to_numpy() != sub["cluster"].to_numpy()).sum())

    temp = float(np.median(quality["d1"]))
    membership = soft_membership(pairwise_distance(X, centers), temp)

    axis = cluster_axis_table(centers, style_features)
    rows = []
    for cluster in range(len(centers)):
        mask = sub["cluster"] == cluster
        q = quality[mask.to_numpy()]
        share = float(mask.mean())
        win = sub.loc[mask, "win"]
        win_rate = (
            float(pd.Series(win).astype(str).str.lower().isin(["true", "1", "1.0"]).mean())
            if len(win) else float("nan")
        )
        rows.append({
            "cluster": cluster,
            "스타일": labels.get(cluster, {}).get("name", "-") if labels else "-",
            "지배축": axis.loc[cluster, "지배축"],
            "축_요약": axis.loc[cluster, "축_요약"],
            "비중": round(share, 3),
            "n": int(mask.sum()),
            "승률": round(win_rate, 3),
            "평균거리": round(float(q["d1"].mean()), 3) if len(q) else None,
            "거리_p90": round(float(q["d1"].quantile(0.90)), 3) if len(q) else None,
            "평균margin": round(float(q["margin_norm"].mean()), 3) if len(q) else None,
            "애매경기비율": round(float((q["margin_norm"] < ambiguous_cut).mean()), 3) if len(q) else None,
            "평균소속확률": round(float(membership[mask.to_numpy(), cluster].mean()), 3) if mask.any() else None,
        })

    profile = pd.DataFrame(rows)

    perf_median = (
        sub.groupby("cluster")[[f"z_{f}" for f in perf_features if f"z_{f}" in sub.columns]]
        .median()
        .round(3)
    )

    return {
        "position": position,
        "n_rows": len(sub),
        "k": int(len(centers)),
        "temperature": round(temp, 4),
        "separation": round(centroid_separation(centers), 4),
        "label_mismatch_rows": mismatch,
        "ambiguous_rate": round(float((quality["margin_norm"] < ambiguous_cut).mean()), 4),
        "margin_p25": round(float(quality["margin_norm"].quantile(0.25)), 4),
        "margin_p50": round(float(quality["margin_norm"].quantile(0.50)), 4),
        "dist_p50": round(float(quality["d1"].quantile(0.50)), 4),
        "dist_p90": round(float(quality["d1"].quantile(0.90)), 4),
        "cluster_profile": profile,
        "perf_median": perf_median,
        "skill_leak": skill_leak_check(sub, perf_features),
        "confusion": _confusion(quality, len(centers)),
    }


def _confusion(quality: pd.DataFrame, k: int) -> pd.DataFrame:
    """어떤 군집이 어떤 군집과 자주 헷갈리는가 (1순위 × 2순위)."""
    table = pd.crosstab(quality["cluster"], quality["runner_up"], normalize="index")
    return table.reindex(index=range(k), columns=range(k), fill_value=0.0).round(3)


def print_report(report: dict[str, Any]) -> None:
    print(f"\n===== {report['position']} (n={report['n_rows']}, k={report['k']}) =====")
    print(f"중심점 평균 간격 {report['separation']} / 소속 온도 {report['temperature']}")
    print(f"애매 경기 비율 {report['ambiguous_rate']:.1%} (margin_norm < 0.15)")
    print(f"margin 분위 p25={report['margin_p25']} p50={report['margin_p50']}")
    if report["label_mismatch_rows"]:
        print(f"[경고] 저장된 cluster 와 재계산 결과가 {report['label_mismatch_rows']}행 불일치")
    leak = report["skill_leak"]
    print(f"실력 누수 eta^2={leak['eta_squared']} → {leak['verdict']}"
          f"  (파생 지표 포함 시 {leak['eta_squared_all']})")
    print(report["cluster_profile"].to_string(index=False))
    print("\n[군집별 성과 축 중앙값]")
    print(report["perf_median"].to_string())
    print("\n[혼동 행렬: 행=배정 군집, 열=2순위 군집]")
    print(report["confusion"].to_string())


def build_cluster_report(
    pdf: pd.DataFrame,
    style_models: dict[str, Any],
    style_features: list[str],
    perf_features: list[str],
    s_cols: list[str],
    style_labels: dict[str, dict[int, dict[str, str]]] | None = None,
    ambiguous_cut: float = 0.15,
    verbose: bool = True,
) -> dict[str, dict[str, Any]]:
    reports = {}
    for position, model in style_models.items():
        report = build_position_report(
            pdf, position, model.cluster_centers_,
            style_features, perf_features, s_cols,
            (style_labels or {}).get(position), ambiguous_cut,
        )
        reports[position] = report
        if verbose:
            print_report(report)
    return reports


# ----------------------------------------------------------------------
# 서빙 아티팩트용 explain 블록
# ----------------------------------------------------------------------
def build_explain_artifact(
    reports: dict[str, dict[str, Any]],
    style_features: list[str],
    ambiguous_cut: float = 0.15,
) -> dict[str, Any]:
    """추론 쪽에서 신뢰도와 근거 문장을 만들 때 필요한 값만 추린다."""
    positions = {}
    for position, report in reports.items():
        profile = report["cluster_profile"]
        clusters = {}
        for _, row in profile.iterrows():
            clusters[str(int(row["cluster"]))] = {
                "share": float(row["비중"]),
                "win_rate": None if pd.isna(row["승률"]) else float(row["승률"]),
                "mean_dist": float(row["평균거리"]) if row["평균거리"] is not None else None,
                "dist_p90": float(row["거리_p90"]) if row["거리_p90"] is not None else None,
                "mean_margin": float(row["평균margin"]) if row["평균margin"] is not None else None,
                "ambiguous_rate": float(row["애매경기비율"]) if row["애매경기비율"] is not None else None,
                "dominant_axis": row["지배축"],
                "axis_summary": row["축_요약"],
            }
        positions[position] = {
            "k": report["k"],
            "temperature": report["temperature"],
            "separation": report["separation"],
            "dist_p50": report["dist_p50"],
            "dist_p90": report["dist_p90"],
            "margin_p25": report["margin_p25"],
            "margin_p50": report["margin_p50"],
            "train_ambiguous_rate": report["ambiguous_rate"],
            "skill_leak_eta2": report["skill_leak"]["eta_squared"],
            "clusters": clusters,
        }

    return {
        "ambiguous_cut": ambiguous_cut,
        "style_features": style_features,
        "positions": positions,
        "method_note": (
            "포지션·티어별 중앙값과 IQR로 각 지표를 로버스트 Z로 바꾸고, "
            "경기마다 6개 스타일 축의 평균을 빼(프로파일 센터링) 전반적 실력 수준을 제거한 뒤, "
            "포지션별 K-Means 중심점 중 가장 가까운 것을 그 경기의 스타일로 본다. "
            "대표 스타일은 경기별 소속 확률을 합산해 정한다."
        ),
    }


__all__ = [
    "assignment_quality",
    "build_cluster_report",
    "build_explain_artifact",
    "build_position_report",
    "centroid_separation",
    "cluster_axis_table",
    "pairwise_distance",
    "print_report",
    "skill_leak_check",
    "soft_membership",
]
