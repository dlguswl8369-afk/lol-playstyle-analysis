"""Spark 없는 LoL 플레이 스타일 추론 및 안전한 LLM 프롬프트 생성기.

학습(04 노트북)에서 건 변환을 추론에서도 똑같이 걸어야 한다.
    09-b  스타일 축 잔차화 — playstyle_explain_v2.StyleExplainMixin.style_vector
    09-c  성과 축 잔차화   — PlayStyleAnalyzer._perf_residual_delta (이 파일)
둘 다 style_model.json 의 회귀 계수를 그대로 쓴다. 블록이 없으면 변환하지 않는다.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np

TIER_BUCKET_MAP = {
    "IRON": "IRON_BRONZE",
    "BRONZE": "IRON_BRONZE",
    "SILVER": "SILVER",
    "GOLD": "GOLD",
    "PLATINUM": "PLATINUM",
    "EMERALD": "EMERALD",
    "DIAMOND": "DIAMOND_PLUS",
    "MASTER": "DIAMOND_PLUS",
    "GRANDMASTER": "DIAMOND_PLUS",
    "CHALLENGER": "DIAMOND_PLUS",
}


COACHING_TONE = {
    "core_weakness": {
        "priority": 1,
        "label": "핵심 개선점",
        "template": "{metric}은(는) {style}의 핵심인데 부족합니다. 가장 먼저 개선할 부분입니다.",
        "prompt_hint": "이 스타일의 핵심 지표가 부족하다. 최우선 과제로 명확하게 설명한다.",
    },
    "below_style": {
        "priority": 2,
        "label": "스타일 내 하위",
        "template": "{metric}은(는) 같은 스타일 유저들과 비교해도 낮습니다.",
        "prompt_hint": "반드시 같은 스타일 유저와의 비교임을 밝히고 전체 평균처럼 말하지 않는다.",
    },
    "core_strength": {
        "priority": 1,
        "label": "핵심 강점",
        "template": "{metric}은(는) {style}의 핵심 강점이며 이 부분을 잘 수행하고 있습니다.",
        "prompt_hint": "강점으로만 설명하고 개선 요구를 붙이지 않는다.",
    },
    "structural": {
        "priority": 99,
        "label": "기록 전용",
        "template": "",
        "prompt_hint": "사용자 프롬프트에 포함하지 않는다.",
    },
    "neutral": {
        "priority": 3,
        "label": "일반",
        "template": "{metric} 지표가 비교 기준과 차이를 보입니다.",
        "prompt_hint": "스타일과 무관한 일반 지표이므로 사실만 설명한다.",
    },
    "relative": {
        "priority": 99,
        "label": "상대적 하위",
        "template": "{metric}이(가) 다른 지표에 비해 상대적으로 낮은 편입니다.",
        "prompt_hint": (
            "임계값 미달 항목이다. 약점으로 단정하지 말고 '굳이 꼽자면' 정도로만 말한다."
        ),
    },
}


# ----------------------------------------------------------------------
# 신뢰도 하한
# ----------------------------------------------------------------------
# reliability(n) = n*ICC / (1 + (n-1)*ICC) 는 "n경기 중앙값을 얼마나 믿을 수 있는가" 다.
# n 은 config["analysis_games"] (학습 노트북의 ANALYSIS_GAMES).
# 0.45 면 그 지표의 변동 중 절반 이상이 경기 간 잡음이라는 뜻이라, 수축(median × rel)을
# 거쳐도 임계값을 넘으면 그대로 강점/개선점 목록에 올라가 확신에 찬 문장이 되어 나간다.
# 그래서 수축과 별개로 "목록에 올릴 자격" 을 따로 건다.
#
# 이 값을 코드 기본값으로 0.5 가 아니라 0.0 으로 둔 이유:
#   config 에 키가 없는 구버전 style_model.json 을 그대로 쓰는 배포가 있을 수 있다.
#   거기에 0.5 를 적용하면 아티팩트는 그대로인데 코드만 바꿔도 결과가 조용히 달라진다.
#   실제 운영값 0.5 는 04 노트북 Cell 00 의 MIN_RELIABILITY 에서 config 로 들어온다.
DEFAULT_MIN_RELIABILITY = 0.0

# "exclude": 후보에서 제외 (기본)
# "flag"   : 목록에는 남기되 low_reliability 표시와 완곡 힌트를 붙인다.
#            문턱을 넘는 지표가 2개 이하로 줄어 프로필이 비는 경우의 대안이다.
LOW_RELIABILITY_MODES = ("exclude", "flag")

# {games} 에 config["analysis_games"] 가 들어간다. 분석 창을 바꾸면 문장도 따라 바뀐다.
# (이 문자열은 LLM 프롬프트로 그대로 나가므로 숫자를 하드코딩하면 안 된다)
LOW_RELIABILITY_HINT_TEMPLATE = (
    "경기 간 편차가 커서 {games}경기로는 확정하기 이른 지표다. "
    "'~한 편' 정도로만 말하고 단정하거나 최우선 과제로 삼지 마라."
)


def load_artifact(path: str | Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as fp:
        return json.load(fp)


def tier_bucket(tier: Any) -> str:
    if tier is None:
        return "ALL"
    value = str(tier).strip().upper()
    if value in set(TIER_BUCKET_MAP.values()):
        return value
    return TIER_BUCKET_MAP.get(value, "ALL")


def _truthy_win(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "1.0", "win", "w"}


def _coaching_line(item: dict[str, Any], style_name: str = "") -> str:
    tag = item.get("finding_tag", "neutral")
    tone = COACHING_TONE.get(tag, COACHING_TONE["neutral"])
    if item.get("is_relative"):
        tone = COACHING_TONE["relative"]
    if not tone["template"]:
        return ""
    return tone["template"].format(
        metric=item.get("metric", item.get("key", "지표")),
        style=style_name or "이 플레이 스타일",
    )


class PlayStyleAnalyzer:
    """Databricks가 내보낸 style_model.json만으로 동작하는 NumPy 추론기."""

    def __init__(self, artifact: dict[str, Any]):
        self.artifact = artifact
        self.config = artifact["config"]
        self.style_features = self.config["style_features"]
        self.perf_features = self.config["perf_features"]
        self.centroids = {
            position: np.asarray(values, dtype=float)
            for position, values in artifact["centroids"].items()
        }

    def _stats(self, position: str, bucket: str) -> dict[str, Any] | None:
        exact = self.artifact["stats"].get(f"{position}|{bucket}")
        if exact:
            return exact
        all_bucket = self.artifact["stats"].get(f"{position}|ALL")
        if all_bucket:
            return all_bucket
        return self.artifact["fallback_stats"].get(position)

    def _z(self, game: dict[str, Any]) -> dict[str, float]:
        position = game.get("team_position")
        bucket = game.get("tier_bucket") or tier_bucket(game.get("tier"))
        stats = self._stats(position, bucket)
        if not stats:
            return {}

        clip = float(self.config["z_clip"])
        output: dict[str, float] = {}
        for feature, stat in stats.items():
            value = game.get(feature)
            if value is None:
                continue
            sigma = max(float(stat["sigma"]), 1e-6)
            z_value = (float(value) - float(stat["center"])) / sigma
            output[feature] = float(np.clip(z_value, -clip, clip))
        return output

    def _perf_residual_delta(self, game: dict[str, Any]) -> dict[str, float]:
        """성과 축 z 에서 빼야 할 승패 성분.

        학습 04 노트북 09-c 와 같은 계산이다.
            design = [1, win]        (절편, 승리 여부)
            beta   = (2, 축 수)      축 순서는 features 리스트 순서
            delta  = design @ beta

        오브젝트 피해처럼 "이겨야 올라가는" 결과 지표는 z 자체가 승패 정보를 들고 있다.
        빼지 않으면 '이겨서 높다' 가 '잘해서 높다' 로 포장되어 강점으로 나간다.

        perf_residual 블록이 없는 구버전 아티팩트에서는 빈 dict 를 돌려 아무것도 바꾸지 않는다.
        """
        block = self.artifact.get("perf_residual") or {}
        features = block.get("features") or []
        entry = (block.get("positions") or {}).get(game.get("team_position"))
        if not features or not entry:
            return {}
        beta = np.asarray(entry["beta"], dtype=float)
        design = np.array([1.0, 1.0 if _truthy_win(game.get("win")) else 0.0], dtype=float)
        delta = design @ beta
        return {feature: float(delta[index]) for index, feature in enumerate(features)}

    def style_vector(
        self,
        z_scores: dict[str, float],
        game: dict[str, Any] | None = None,
        position: str | None = None,
    ) -> np.ndarray:
        """학습과 동일한 스타일 벡터: 프로파일 센터링(09) + 승패·실력 잔차화(09-b).

        잔차화를 학습에서만 하고 추론에서 빼먹으면 중심점과 다른 좌표계에서 거리를 재게
        되어, 경계 경기의 군집이 조용히 뒤바뀐다. 그러면 스타일 기준선도 틀린 것이 붙어
        finding_tag 까지 어긋난다. 그래서 여기서 반드시 같은 변환을 건다.

        성과 축 잔차화(09-c)는 스타일 축 계산에 들어가면 안 되므로, 학습에서 통제 변수로
        쓴 값과 같은 원본(z_raw)을 입력으로 쓴다.
        """
        base = (game or {}).get("z_raw") or z_scores
        vector = np.asarray([base.get(f, 0.0) for f in self.style_features], dtype=float)
        vector = vector - vector.mean()

        residual = self.artifact.get("residual")
        positions = (residual or {}).get("positions") or {}
        if position is None and game is not None:
            position = game.get("team_position")
        if not residual or game is None or position not in positions:
            return vector

        beta = np.asarray(positions[position]["beta"], dtype=float)
        perf_features = residual.get("perf_features") or self.perf_features
        values = [base[f] for f in perf_features if f in base]
        design = np.array(
            [
                1.0,
                1.0 if _truthy_win(game.get("win")) else 0.0,
                float(np.mean(values)) if values else 0.0,
            ],
            dtype=float,
        )
        vector = vector - design @ beta
        return vector - vector.mean()  # 합이 0 인 제약 복원 (학습과 동일)

    def _assign_style(
        self,
        position: str,
        z_scores: dict[str, float],
        game: dict[str, Any] | None = None,
    ):
        if position not in self.centroids or not z_scores:
            return None, None
        vector = self.style_vector(z_scores, game, position)
        distances = np.linalg.norm(self.centroids[position] - vector, axis=1)
        return int(distances.argmin()), {int(i): float(v) for i, v in enumerate(distances)}

    def analyze_games(self, games: list[dict[str, Any]]) -> list[dict[str, Any]]:
        rows = []
        for game in games:
            normalized = dict(game)
            normalized["tier_bucket"] = game.get("tier_bucket") or tier_bucket(game.get("tier"))
            # z_raw : 클리핑까지만 끝난 원본. 스타일 축은 학습과 같이 이 값에서 출발한다.
            # z     : 성과 축에서 승패 성분을 뺀 값. 강점/개선점·추세 계산은 이 값을 쓴다.
            z_raw = self._z(normalized)
            z_scores = dict(z_raw)
            for feature, delta in self._perf_residual_delta(normalized).items():
                if feature in z_scores:
                    z_scores[feature] = z_scores[feature] - delta
            row = {**normalized, "z": z_scores, "z_raw": z_raw}
            cluster, distances = self._assign_style(normalized.get("team_position"), z_raw, row)
            row.update(cluster=cluster, dist=distances)
            rows.append(row)
        return rows

    def dominant_style(self, rows: list[dict[str, Any]], main_position: str) -> dict[str, Any]:
        selected = [
            row
            for row in rows
            if row.get("team_position") == main_position and row.get("cluster") is not None
        ]
        if len(selected) < self.config["min_position_games"]:
            return {
                "status": "insufficient",
                "games": len(selected),
                "styles": [],
                "consistency": None,
                "distribution": [],
                "message": (
                    f"{main_position} 경기가 {len(selected)}판이라 "
                    "대표 스타일을 판정할 수 없습니다."
                ),
            }

        counts = Counter(row["cluster"] for row in selected)
        total = sum(counts.values())

        def tie_break(cluster: int):
            distances = [
                row["dist"][cluster]
                for row in selected
                if row.get("dist") and cluster in row["dist"]
            ]
            return -counts[cluster], float(np.mean(distances)) if distances else 0.0

        ranked = sorted(counts, key=tie_break)
        top_share = counts[ranked[0]] / total
        second_share = counts[ranked[1]] / total if len(ranked) > 1 else 0.0
        top_two_share = sum(counts[c] for c in ranked[:2]) / total

        def label(cluster: int) -> dict[str, Any]:
            return self.artifact["labels"][main_position][str(cluster)]

        if top_share >= self.config["dominant_share"]:
            status, picked = "single", ranked[:1]
        elif (
            second_share >= self.config["mixed_share"]
            and top_two_share >= self.config["mixed_top2"]
        ):
            status, picked = "mixed", ranked[:2]
        else:
            status, picked = "unstable", ranked[:2]

        return {
            "status": status,
            "games": len(selected),
            "consistency": round(top_share, 2),
            "styles": [
                {"cluster": c, "ratio": round(100 * counts[c] / total, 1), **label(c)}
                for c in picked
            ],
            "distribution": [
                {
                    "style": label(c)["name"],
                    "count": counts[c],
                    "ratio": round(100 * counts[c] / total, 1),
                }
                for c in ranked
            ],
        }

    def build_profile(
        self,
        rows: list[dict[str, Any]],
        main_position: str,
        main_cluster: int | None,
    ) -> dict[str, Any]:
        reliability = self.artifact["reliability"]
        baseline_cut = float(self.config["style_baseline_cut"])
        z_clip = float(self.config["z_clip"])
        min_reliability = float(self.config.get("min_reliability", DEFAULT_MIN_RELIABILITY))
        low_mode = str(self.config.get("low_reliability_mode", "exclude")).lower()
        low_hint = LOW_RELIABILITY_HINT_TEMPLATE.format(
            games=int(self.config.get("analysis_games", 10))
        )
        if low_mode not in LOW_RELIABILITY_MODES:
            low_mode = "exclude"
        baseline = None
        if main_cluster is not None:
            baseline = self.artifact["style_baseline"].get(main_position, {}).get(str(main_cluster))

        scores: dict[str, float] = {}
        detail: dict[str, dict[str, Any]] = {}
        for feature in self.perf_features:
            values = [row["z"][feature] for row in rows if feature in row["z"]]
            if len(values) < self.config["min_games"]:
                continue

            raw_median = float(np.median(values))
            median, corrected, tag = raw_median, False, "neutral"
            style_median = baseline["median"].get(feature, 0.0) if baseline else 0.0

            if style_median < -baseline_cut:
                sigma = max(baseline["sigma"].get(feature, 1.0), 1e-6)
                median = float(np.clip((raw_median - style_median) / sigma, -z_clip, z_clip))
                corrected = True
                tag = "below_style" if median <= -0.5 else "structural"
            elif style_median > baseline_cut:
                tag = "core_weakness" if raw_median < 0 else "core_strength"

            feature_reliability = float(reliability.get(feature, 1.0))
            adjusted = median * feature_reliability
            scores[feature] = adjusted
            detail[feature] = {
                "median_z": round(median, 3),
                "raw_median_z": round(raw_median, 3),
                "reliability": round(feature_reliability, 3),
                "adjusted": round(adjusted, 3),
                "style_corrected": corrected,
                "finding_tag": tag,
                # 분석 창 경기 수로 판단하기엔 편차가 큰 지표. all_scores 에는 남는다.
                "low_reliability": feature_reliability < min_reliability,
            }

        # 신뢰도 미달 지표는 계산은 그대로 두고 '후보 자격' 만 뺀다.
        # exclude 모드에서만 실제로 빠지고, flag 모드에서는 표시만 붙여 목록에 남긴다.
        low_reliability_keys = [feature for feature in scores if detail[feature]["low_reliability"]]
        eligible = {
            feature
            for feature in scores
            if low_mode == "flag" or not detail[feature]["low_reliability"]
        }

        # 계열 대표는 후보 자격이 있는 것들 중에서만 고른다.
        # 미달 지표가 계열 대표를 차지한 뒤 빠지면 그 계열이 통째로 사라지기 때문이다.
        keep: set[str] = set()
        for members in self.config["perf_families"].values():
            available = [feature for feature in members if feature in eligible]
            if available:
                keep.add(max(available, key=lambda feature: abs(scores[feature])))

        ranked = sorted(
            ((feature, score) for feature, score in scores.items() if feature in keep),
            key=lambda item: item[1],
            reverse=True,
        )
        labels = self.artifact["feature_labels"]

        def item(feature: str, score: float) -> dict[str, Any]:
            return {
                "metric": labels.get(feature, feature),
                "key": feature,
                "score": round(score, 2),
                **detail[feature],
            }

        strengths = [
            item(feature, score)
            for feature, score in ranked
            if score >= self.config["strength_threshold"]
        ][:3]
        improvements = [
            item(feature, score)
            for feature, score in reversed(ranked)
            if score <= -self.config["improvement_threshold"]
            and detail[feature]["finding_tag"] != "structural"
        ][:3]

        if not strengths and self.config.get("always_show_strength"):
            for feature, score in ranked:
                if detail[feature]["finding_tag"] != "structural" and score > 0:
                    strengths.append({**item(feature, score), "is_relative": True})
                    break

        if not improvements and self.config.get("always_show_improvement"):
            for feature, score in reversed(ranked):
                if detail[feature]["finding_tag"] in {"structural", "core_strength"}:
                    continue
                if score < 0:
                    improvements.append({**item(feature, score), "is_relative": True})
                break

        style_name = ""
        if main_cluster is not None:
            style_name = self.artifact["labels"][main_position][str(main_cluster)]["name"]

        for collection in (strengths, improvements):
            for finding in collection:
                finding.setdefault("is_relative", False)
                finding.setdefault("low_reliability", False)
                tag = "relative" if finding["is_relative"] else finding["finding_tag"]
                tone = COACHING_TONE.get(tag, COACHING_TONE["neutral"])
                finding["tone_label"] = tone["label"]
                finding["priority"] = tone["priority"]
                finding["prompt_hint"] = tone["prompt_hint"]
                finding["coaching_line"] = _coaching_line(finding, style_name)
                # flag 모드에서만 도달한다. 단정 표현을 막고 우선순위를 뒤로 민다.
                if finding["low_reliability"]:
                    finding["tone_label"] = f"{tone['label']} · 판단 유보"
                    finding["prompt_hint"] = low_hint
                    finding["priority"] = max(finding["priority"], 3)
            collection.sort(key=lambda finding: (finding["priority"], -abs(finding["score"])))

        real_improvements = [
            finding
            for finding in improvements
            if not finding["is_relative"] and not finding["low_reliability"]
        ]
        primary_pool = real_improvements or improvements
        # 신뢰할 수 있는 항목이 하나라도 있으면 그쪽을 최우선 목표로 삼는다.
        confident = [finding for finding in primary_pool if not finding["low_reliability"]]
        primary_pool = confident or primary_pool
        core = [finding for finding in primary_pool if finding["finding_tag"] == "core_weakness"]
        primary_goal = (core or primary_pool or [None])[0]
        if primary_goal and primary_goal.get("is_relative"):
            primary_goal = {
                **primary_goal,
                "finding_tag_raw": primary_goal["finding_tag"],
                "finding_tag": "relative",
                "prompt_hint": COACHING_TONE["relative"]["prompt_hint"],
            }

        excluded = [
            labels.get(feature, feature)
            for feature, score in ranked
            if score <= -self.config["improvement_threshold"]
            and detail[feature]["finding_tag"] == "structural"
        ]

        # 어떤 지표가 왜 빠졌는지 기록에 남긴다. 값이 없어서가 아니라 못 믿어서 빠진 것이다.
        low_reliability_excluded = [
            {
                "metric": labels.get(feature, feature),
                "key": feature,
                "reliability": detail[feature]["reliability"],
                "median_z": detail[feature]["median_z"],
                "score": round(scores[feature], 2),
                "dropped": low_mode == "exclude",
            }
            for feature in low_reliability_keys
        ]

        return {
            "strengths": strengths,
            "improvements": improvements,
            "has_strengths": any(
                not finding["is_relative"] and not finding["low_reliability"]
                for finding in strengths
            ),
            "has_improvements": bool(real_improvements),
            "primary_goal": primary_goal,
            "excluded_structural": excluded,
            "low_reliability_excluded": low_reliability_excluded,
            "min_reliability": min_reliability,
            "low_reliability_mode": low_mode,
            "all_scores": detail,
        }

    def recent_trend(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        ordered = sorted(rows, key=lambda row: str(row.get("game_start_datetime", "")))
        minimum = int(self.config["trend_min_games"])
        if len(ordered) < minimum:
            return {
                "status": "insufficient",
                "changes": [],
                "window": None,
                "note": (
                    f"추세 비교에는 주 포지션 {minimum}경기가 필요합니다 (현재 {len(ordered)}경기)."
                ),
            }

        half = len(ordered) // 2
        previous, recent = ordered[:half], ordered[half:]
        threshold = float(self.config["trend_threshold"])
        changes = []
        for feature in self.perf_features:
            before = [row["z"][feature] for row in previous if feature in row["z"]]
            after = [row["z"][feature] for row in recent if feature in row["z"]]
            if not before or not after:
                continue
            delta = (float(np.median(after)) - float(np.median(before))) * self.artifact[
                "reliability"
            ].get(feature, 1.0)
            if abs(delta) >= threshold:
                changes.append(
                    {
                        "metric": self.artifact["feature_labels"].get(feature, feature),
                        "key": feature,
                        "delta": round(delta, 2),
                        "direction": "up" if delta > 0 else "down",
                        "significant": True,
                    }
                )

        return {
            "status": "changed" if changes else "stable",
            "window": {"previous": len(previous), "recent": len(recent)},
            "changes": sorted(changes, key=lambda change: abs(change["delta"]), reverse=True)[:3],
            "note": None if changes else "최근 구간에서 임계값을 넘는 변화가 없습니다.",
        }

    def analyze(self, games: list[dict[str, Any]]) -> dict[str, Any]:
        if len(games) < self.config["min_games"]:
            return {
                "status": "insufficient_games",
                "games": len(games),
                "message": f"분석에는 최소 {self.config['min_games']}경기가 필요합니다.",
            }

        rows = self.analyze_games(games)
        valid = [row for row in rows if row["z"]]
        if not valid:
            return {
                "status": "unsupported_position",
                "games": len(games),
                "message": "분석 기준이 없는 포지션 또는 티어입니다.",
            }

        position_counts = Counter(row["team_position"] for row in valid)
        main_position, main_games = position_counts.most_common(1)[0]
        style = self.dominant_style(rows, main_position)

        # unstable이면 특정 스타일 기준선으로 강약점을 보정하지 않는다.
        main_cluster = None
        if style["status"] in {"single", "mixed"} and style.get("styles"):
            main_cluster = style["styles"][0]["cluster"]

        main_rows = [row for row in valid if row["team_position"] == main_position]
        profile = self.build_profile(main_rows, main_position, main_cluster)

        return {
            "status": "ok",
            "games_analyzed": len(valid),
            "main_position": main_position,
            "main_position_games": main_games,
            "position_mix": dict(position_counts),
            "position_focus_ratio": round(main_games / len(valid), 2),
            "tier_buckets": dict(Counter(row["tier_bucket"] for row in valid)),
            "win_rate": round(
                100 * sum(_truthy_win(row.get("win")) for row in valid) / len(valid), 1
            ),
            "coaching_mode": "consistency" if style["status"] == "unstable" else "style",
            "play_style": style,
            "strengths": profile["strengths"],
            "improvements": profile["improvements"],
            "primary_goal": profile["primary_goal"],
            "has_strengths": profile["has_strengths"],
            "has_improvements": profile["has_improvements"],
            # 아래는 기록/디버깅 전용. build_llm_payload에서 제거한다.
            "excluded_structural": profile["excluded_structural"],
            "low_reliability_excluded": profile["low_reliability_excluded"],
            "reliability_filter": {
                "min_reliability": profile["min_reliability"],
                "mode": profile["low_reliability_mode"],
            },
            "_debug_scores": profile["all_scores"],
            "recent_trend": self.recent_trend(main_rows),
        }


def build_llm_payload(result: dict[str, Any]) -> dict[str, Any]:
    """LLM에 전달해도 되는 필드만 허용 목록으로 복사한다."""
    allowed = {
        "status",
        "games_analyzed",
        "main_position",
        "main_position_games",
        "position_mix",
        "position_focus_ratio",
        "tier_buckets",
        "win_rate",
        "coaching_mode",
        "play_style",
        "strengths",
        "improvements",
        "primary_goal",
        "has_strengths",
        "has_improvements",
        "recent_trend",
    }
    return {key: value for key, value in result.items() if key in allowed}


def build_coaching_prompt(result: dict[str, Any], player_name: str = "플레이어") -> str:
    payload = build_llm_payload(result)
    if payload.get("status") != "ok":
        return f"분석 불가: {payload.get('status')}"

    rules = [
        "입력 JSON에 없는 사실이나 원인을 추측하지 마라.",
        "finding_tag와 prompt_hint를 그대로 지켜 코칭 강도를 정하라.",
        "core_strength는 강점으로만 언급하고 개선 요구를 붙이지 마라.",
        "숫자는 JSON에 있는 값만 사용하라.",
    ]

    if not payload.get("has_improvements"):
        rules.append(
            "뚜렷한 개선점이 없으므로 개선점 3개를 만들지 마라. "
            "있으면 상대적 제안 1개만 완곡하게 말하라."
        )
    else:
        rules.append("개선점은 improvements에 있는 실제 항목만 사용하고 최대 3개로 제한하라.")

    if payload.get("play_style", {}).get("status") == "unstable":
        rules.append(
            "대표 스타일을 단정하지 마라. "
            "경기별 스타일 변동과 플레이 일관성을 핵심 주제로 코칭하라."
        )
    else:
        rules.append("play_style에 있는 대표 스타일을 강점과 개선점의 맥락으로 활용하라.")

    return (
        f"{player_name}님의 최근 LoL 플레이를 한국어로 코칭해라.\n"
        "구성: 플레이 스타일 요약 → 근거 있는 강점 → 패배/변동 패턴 → "
        "가장 중요한 개선 포인트 → 한 줄 요약.\n"
        "규칙:\n- " + "\n- ".join(rules) + "\n\n"
        "분석 JSON:\n" + json.dumps(payload, ensure_ascii=False, indent=2)
    )


__all__ = [
    "PlayStyleAnalyzer",
    "build_coaching_prompt",
    "build_llm_payload",
    "load_artifact",
    "tier_bucket",
]
