"""군집 배정을 설명 가능하게 만들고, 그 설명을 피드백 품질로 바로 연결한다.

기존 파일(playstyle_analyzer.py)은 건드리지 않는다. 믹스인으로 얹기만 한다.

지금 파이프라인의 문제
    경기마다 argmin 으로 스타일을 하나 찍고, 10경기 최빈값을 대표 스타일로 썼다.
    1순위와 2순위 중심점까지의 거리가 거의 같은 '경계 경기'도 1표를 온전히 행사한다.
    그래서 (a) 왜 이 스타일인지 설명할 수 없고 (b) 얼마나 확실한지도 모른 채
    라벨만 LLM 에 넘어가 단정적인 코칭 문장이 된다. 피드백 저하의 실제 원인이다.

여기서 바꾸는 것
    0. (v9) 성과 축 승패 잔차화는 PlayStyleAnalyzer._perf_residual_delta 가 담당한다.
       이 파일은 스타일 축 잔차화(09-b)만 다루고, 그 입력으로 row["z_raw"] 를 쓴다.
    1. 소속 확률(soft membership) 합산으로 대표 스타일 결정 — 경계 경기는 절반만 기여
    2. margin · 거리 분위 · 경기 수로 style_confidence 산출 (high / medium / low)
    3. 축 단위 근거 — 어떤 지표 때문에 이 스타일로 묶였는지 수치로 제시
    4. off_style_axes — 스타일 중심 대비 미달한 축을 새 코칭 소재로 제공
    5. confidence 에 따라 LLM 프롬프트 규칙 자체를 분기

사용법
    # (A) 패키지로 쓸 때
    from playstyle_explain_v2 import ExplainablePlayStyleAnalyzer
    analyzer = ExplainablePlayStyleAnalyzer(serving)

    # (B) 노트북에 PlayStyleAnalyzer 가 인라인으로 정의돼 있을 때
    from playstyle_explain_v2 import make_explainable
    analyzer = make_explainable(PlayStyleAnalyzer)(serving)
"""

from __future__ import annotations

import json
from typing import Any

import numpy as np


AMBIGUOUS_CUT = 0.15          # margin_norm 이 이보다 작으면 '경계 경기'
HIGH_CONFIDENCE = 0.65
MEDIUM_CONFIDENCE = 0.45

MATCH_STRONG = "강함"
MATCH_WEAK = "약함"
MATCH_OFF = "불일치"

BASE_PAYLOAD_KEYS = {
    "status", "games_analyzed", "main_position", "main_position_games",
    "position_mix", "position_focus_ratio", "tier_buckets", "win_rate",
    "coaching_mode", "play_style", "strengths", "improvements", "primary_goal",
    "has_strengths", "has_improvements", "recent_trend",
}
EXPLAIN_PAYLOAD_KEYS = {
    "style_confidence", "style_evidence", "style_explanation_ko",
    "per_game_style", "method_note",
}


def _empty_confidence(reason: str) -> dict[str, Any]:
    """판정 자체가 불가능할 때의 기본 신뢰도. 키 구성은 정상 경로와 동일하게 유지한다."""
    return {"level": "low", "score": 0.0, "top_share": 0.0, "mean_margin": 0.0,
            "ambiguous_rate": 1.0, "typical_rate": 0.0, "reasons": [reason]}


def _is_win(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "1.0", "win", "w"}


def _softmax_from_distance(distances, temperature: float) -> np.ndarray:
    temp = max(float(temperature), 1e-6)
    logits = -(np.asarray(distances, dtype=float) ** 2) / (2 * temp ** 2)
    logits = logits - logits.max()
    weights = np.exp(logits)
    return weights / weights.sum()


class StyleExplainMixin:
    """PlayStyleAnalyzer 위에 얹는 설명 계층."""

    def __init__(self, artifact: dict[str, Any]):
        super().__init__(artifact)
        # 노트북 인라인 클래스는 self.a / self.cfg 를 쓰므로 이름을 통일해 둔다.
        self.artifact = artifact
        self.config = artifact["config"]
        self.style_features = self.config["style_features"]
        self.perf_features = self.config["perf_features"]
        self.explain = artifact.get("explain") or {}
        self.ambiguous_cut = float(self.explain.get("ambiguous_cut", AMBIGUOUS_CUT))

    # ---------- explain 블록이 없는 구버전 아티팩트 폴백 ----------
    def _position_meta(self, position: str) -> dict[str, Any]:
        meta = (self.explain.get("positions") or {}).get(position)
        if meta:
            return meta
        centers = self.centroids.get(position)
        if centers is None or len(centers) < 2:
            return {"temperature": 1.0, "separation": 1.0, "dist_p90": None,
                    "margin_p50": None, "clusters": {}}
        pair = np.linalg.norm(centers[:, None, :] - centers[None, :, :], axis=2)
        separation = float(pair[np.triu_indices(len(centers), 1)].mean())
        return {"temperature": round(separation / 2, 4),
                "separation": round(separation, 4),
                "dist_p90": None, "margin_p50": None, "clusters": {}}

    def _label(self, position: str, cluster: int) -> dict[str, Any]:
        return self.artifact["labels"][position][str(int(cluster))]

    def _feature_label(self, feature: str) -> str:
        return self.artifact["feature_labels"].get(feature, feature)

    # ---------- 경기 단위 설명 ----------
    def style_vector(self, z_scores: dict[str, float], game: dict[str, Any] | None = None,
                     position: str | None = None) -> np.ndarray:
        """학습과 동일한 변환: 프로파일 센터링 (+ 학습 때 잔차화를 했다면 같은 잔차화)."""
        # 계산 본체는 PlayStyleAnalyzer.style_vector 하나로 모은다.
        # 같은 식을 두 군데 두면 한쪽만 고쳐져 학습과 조용히 어긋난다.
        # (노트북에 인라인으로 정의된 구버전 클래스에는 없을 수 있으므로 아래 폴백을 둔다)
        parent = getattr(super(), "style_vector", None)
        if callable(parent):
            return parent(z_scores, game, position)

        # 09-c 성과 축 잔차화가 켜져 있으면 row["z"] 의 성과 축은 이미 승패가 빠진 값이다.
        # 학습 09-b 는 '잔차화 전' 성과 z 의 평균을 perf_level 로 썼으므로,
        # 여기서도 원본(z_raw)이 있으면 그걸 써야 통제 변수가 학습과 같아진다.
        base = (game or {}).get("z_raw") or z_scores

        vector = np.asarray(
            [base.get(f, 0.0) for f in self.style_features], dtype=float
        )
        vector = vector - vector.mean()

        residual = self.artifact.get("residual")
        positions = (residual or {}).get("positions") or {}
        if not residual or game is None or position not in positions:
            return vector

        beta = np.asarray(positions[position]["beta"], dtype=float)
        perf_features = residual.get("perf_features") or self.perf_features
        values = [base[f] for f in perf_features if f in base]
        design = np.array([
            1.0,
            1.0 if _is_win(game.get("win")) else 0.0,
            float(np.mean(values)) if values else 0.0,
        ], dtype=float)
        vector = vector - design @ beta
        return vector - vector.mean()

    def explain_game(self, position: str, z_scores: dict[str, float],
                     game: dict[str, Any] | None = None) -> dict[str, Any] | None:
        centers = self.centroids.get(position)
        if centers is None or not z_scores:
            return None

        vector = self.style_vector(z_scores, game, position)
        distances = np.linalg.norm(centers - vector, axis=1)
        order = np.argsort(distances)
        first = int(order[0])
        second = int(order[1]) if len(order) > 1 else first

        meta = self._position_meta(position)
        separation = max(float(meta.get("separation") or 1.0), 1e-6)
        margin_norm = float((distances[second] - distances[first]) / separation)
        membership = _softmax_from_distance(distances, meta.get("temperature", 1.0))

        # 1순위를 2순위보다 가깝게 만든 축별 기여 (합치면 d2^2 - d1^2)
        contribution = (vector - centers[second]) ** 2 - (vector - centers[first]) ** 2

        dist_p90 = meta.get("dist_p90")
        typical = None if dist_p90 is None else bool(distances[first] <= float(dist_p90))

        return {
            "cluster": first,
            "runner_up": second,
            "distance": round(float(distances[first]), 3),
            "runner_up_distance": round(float(distances[second]), 3),
            "margin_norm": round(margin_norm, 3),
            "ambiguous": bool(margin_norm < self.ambiguous_cut),
            "typical": typical,
            "membership": {int(i): float(p) for i, p in enumerate(membership)},
            "contribution": {f: float(contribution[i])
                             for i, f in enumerate(self.style_features)},
            "vector": {f: float(vector[i]) for i, f in enumerate(self.style_features)},
        }

    def analyze_games(self, games: list[dict[str, Any]]) -> list[dict[str, Any]]:
        rows = super().analyze_games(games)
        for row in rows:
            row["explain"] = self.explain_game(
                row.get("team_position"), row.get("z") or {}, row
            )
        return rows

    # ---------- 대표 스타일: 소속 확률 합산 ----------
    def dominant_style(self, rows: list[dict[str, Any]], main_position: str) -> dict[str, Any]:
        selected = [row for row in rows
                    if row.get("team_position") == main_position and row.get("explain")]
        if len(selected) < self.config["min_position_games"]:
            message = (f"{main_position} 경기가 {len(selected)}판이라 "
                       "대표 스타일을 판정할 수 없습니다.")
            return {
                "status": "insufficient", "games": len(selected), "styles": [],
                "consistency": None, "distribution": [],
                "confidence": _empty_confidence(message),
                "ambiguous_games": 0,
                "message": message,
            }

        k = len(self.centroids[main_position])
        soft = np.zeros(k, dtype=float)
        hard = np.zeros(k, dtype=float)
        for row in selected:
            info = row["explain"]
            for cluster, probability in info["membership"].items():
                soft[int(cluster)] += probability
            hard[info["cluster"]] += 1.0

        soft_share = soft / soft.sum()
        hard_share = hard / hard.sum()
        ranked = [int(c) for c in np.argsort(-soft_share)]
        top = ranked[0]
        second = ranked[1] if k > 1 else top

        top_share = float(soft_share[top])
        second_share = float(soft_share[second]) if k > 1 else 0.0
        top_two_share = top_share + second_share

        if top_share >= self.config["dominant_share"]:
            status, picked = "single", [top]
        elif (second_share >= self.config["mixed_share"]
              and top_two_share >= self.config["mixed_top2"]):
            status, picked = "mixed", [top, second]
        else:
            status, picked = "unstable", [top, second]

        confidence = self._confidence(selected, main_position, top_share, soft_share)
        # 확신이 낮은데 단일 스타일로 단정하면 그대로 오코칭이 된다.
        if status == "single" and confidence["level"] == "low" and k > 1:
            status, picked = "mixed", [top, second]

        return {
            "status": status,
            "games": len(selected),
            "consistency": round(top_share, 2),
            "confidence": confidence,
            "ambiguous_games": sum(1 for r in selected if r["explain"]["ambiguous"]),
            "styles": [
                {"cluster": int(c),
                 "ratio": round(100 * float(soft_share[c]), 1),
                 "hard_ratio": round(100 * float(hard_share[c]), 1),
                 **self._label(main_position, c)}
                for c in picked
            ],
            "distribution": [
                {"style": self._label(main_position, c)["name"],
                 "count": int(hard[c]),
                 "ratio": round(100 * float(soft_share[c]), 1)}
                for c in ranked
            ],
        }

    # ---------- 신뢰도 ----------
    def _confidence(self, rows, position: str, top_share: float,
                    soft_share: np.ndarray) -> dict[str, Any]:
        meta = self._position_meta(position)
        margins = [row["explain"]["margin_norm"] for row in rows]
        mean_margin = float(np.mean(margins)) if margins else 0.0
        ambiguous_rate = (float(np.mean([m < self.ambiguous_cut for m in margins]))
                          if margins else 1.0)

        typical_flags = [row["explain"]["typical"] for row in rows
                         if row["explain"]["typical"] is not None]
        typical_rate = float(np.mean(typical_flags)) if typical_flags else 1.0

        reference = meta.get("margin_p50") or self.ambiguous_cut * 2
        margin_score = float(np.clip(mean_margin / max(float(reference), 1e-6), 0, 1))

        k = len(soft_share)
        chance = 1.0 / k if k else 1.0
        share_score = float(np.clip((top_share - chance) / max(1 - chance, 1e-6), 0, 1))

        analysis_games = float(self.config.get("analysis_games", 10))
        games_score = float(np.clip(len(rows) / max(analysis_games, 1.0), 0, 1))

        score = float(0.40 * share_score + 0.30 * margin_score
                      + 0.20 * typical_rate + 0.10 * games_score)

        level = ("high" if score >= HIGH_CONFIDENCE
                 else "medium" if score >= MEDIUM_CONFIDENCE else "low")

        reasons = []
        if share_score < 0.35:
            reasons.append("경기별 스타일이 여러 군집에 고르게 흩어져 있습니다.")
        if ambiguous_rate >= 0.4:
            reasons.append(f"{len(rows)}경기 중 {round(ambiguous_rate * len(rows))}경기가 "
                           "두 스타일 경계에 걸쳐 있습니다.")
        if typical_rate < 0.6:
            reasons.append("학습 데이터의 전형적인 패턴에서 벗어난 경기가 많습니다.")
        if len(rows) < analysis_games:
            reasons.append(f"주 포지션 경기가 {len(rows)}판으로 "
                           f"기준({int(analysis_games)}판)보다 적습니다.")

        return {
            "level": level, "score": round(score, 2),
            "top_share": round(top_share, 2),
            "mean_margin": round(mean_margin, 3),
            "ambiguous_rate": round(ambiguous_rate, 2),
            "typical_rate": round(typical_rate, 2),
            "reasons": reasons,
        }

    # ---------- 축 단위 근거 ----------
    def style_evidence(self, rows, position: str, cluster: int) -> dict[str, Any]:
        center = np.asarray(self.centroids[position][int(cluster)], dtype=float)
        vectors = np.asarray(
            [[row["explain"]["vector"][f] for f in self.style_features] for row in rows],
            dtype=float,
        )
        user = np.median(vectors, axis=0)
        contributions = np.mean(
            [[row["explain"]["contribution"][f] for f in self.style_features] for row in rows],
            axis=0,
        )

        matched, off_style = [], []
        for i, feature in enumerate(self.style_features):
            center_value, user_value = float(center[i]), float(user[i])
            if abs(center_value) < 0.2:
                continue                                # 이 스타일을 규정하지 않는 축
            same_sign = np.sign(center_value) == np.sign(user_value)
            ratio = user_value / center_value if abs(center_value) > 1e-6 else 0.0

            if same_sign and ratio >= 0.6:
                match = MATCH_STRONG
            elif same_sign and ratio > 0.2:
                match = MATCH_WEAK
            else:
                match = MATCH_OFF

            entry = {"metric": self._feature_label(feature), "key": feature,
                     "user": round(user_value, 2), "style_center": round(center_value, 2),
                     "ratio": round(float(ratio), 2), "match": match,
                     "axis_weight": round(abs(center_value), 2)}
            (matched if match == MATCH_STRONG else off_style).append(entry)

        matched.sort(key=lambda e: -e["axis_weight"])
        off_style.sort(key=lambda e: -e["axis_weight"])

        decisive = sorted(
            ({"metric": self._feature_label(f), "key": f,
              "effect": round(float(contributions[i]), 3)}
             for i, f in enumerate(self.style_features)),
            key=lambda e: -e["effect"],
        )[:2]

        return {
            "style": self._label(position, cluster)["name"],
            "matched_axes": matched[:3],
            "off_style_axes": off_style[:2],
            "decisive_axes": decisive,
            "axis_summary": (self._position_meta(position)
                             .get("clusters", {})
                             .get(str(int(cluster)), {})
                             .get("axis_summary")),
        }

    # ---------- 최종 조립 ----------
    def analyze(self, games: list[dict[str, Any]]) -> dict[str, Any]:
        result = super().analyze(games)
        if result.get("status") != "ok":
            return result

        rows = self.analyze_games(games)
        position = result["main_position"]
        main_rows = [row for row in rows
                     if row.get("team_position") == position and row.get("explain")]

        style = result.get("play_style", {})
        confidence = style.get("confidence") or _empty_confidence(
            "대표 스타일을 판정할 근거가 부족합니다."
        )
        result["style_confidence"] = confidence

        if style.get("styles") and main_rows:
            cluster = style["styles"][0]["cluster"]
            evidence = self.style_evidence(main_rows, position, cluster)
            result["style_evidence"] = evidence
            result["style_explanation_ko"] = explanation_sentences(evidence, confidence)
        else:
            result["style_evidence"] = None
            result["style_explanation_ko"] = []

        result["coaching_mode"] = {"high": "style", "medium": "style_soft",
                                   "low": "consistency"}.get(
            confidence.get("level", "low"), "consistency")

        result["per_game_style"] = [
            {"match_id": row.get("match_id"),
             "champion_name": row.get("champion_name"),
             "win": row.get("win"),
             "style": self._label(position, row["explain"]["cluster"])["name"],
             "runner_up": self._label(position, row["explain"]["runner_up"])["name"],
             "margin_norm": row["explain"]["margin_norm"],
             "ambiguous": row["explain"]["ambiguous"]}
            for row in main_rows
        ]
        result["method_note"] = self.explain.get("method_note")
        return result


def make_explainable(base_cls):
    """노트북에 인라인으로 정의된 PlayStyleAnalyzer 에도 설명 계층을 붙인다."""
    return type("ExplainablePlayStyleAnalyzer", (StyleExplainMixin, base_cls), {})


try:                                        # 패키지로 쓸 때의 기본 클래스
    try:
        from .playstyle_analyzer import PlayStyleAnalyzer as _BaseAnalyzer
    except ImportError:
        from playstyle_analyzer import PlayStyleAnalyzer as _BaseAnalyzer

    ExplainablePlayStyleAnalyzer = make_explainable(_BaseAnalyzer)
except ImportError as _import_error:        # 노트북 단독 실행 — make_explainable 사용
    _IMPORT_MESSAGE = (
        "playstyle_analyzer 를 import 하지 못했습니다 "
        f"({_import_error}).\n"
        "  - 같은 폴더에 파일 이름이 정확히 'playstyle_analyzer.py' 인지 확인하세요 "
        "(다운로드 시 'playstyle_analyzer__1_.py' 처럼 바뀌는 경우가 많습니다).\n"
        "  - 노트북에 클래스가 인라인으로 정의돼 있다면 "
        "make_explainable(PlayStyleAnalyzer) 를 쓰세요."
    )

    class ExplainablePlayStyleAnalyzer:      # type: ignore[no-redef]
        """기본 클래스를 못 찾았을 때, 원인을 알려주고 멈추는 자리표시자."""

        def __init__(self, *args, **kwargs):
            raise ImportError(_IMPORT_MESSAGE)


# ----------------------------------------------------------------------
# 사람이 읽는 설명 문장 (LLM 없이도 그대로 화면에 노출 가능)
# ----------------------------------------------------------------------
def explanation_sentences(evidence: dict[str, Any],
                          confidence: dict[str, Any]) -> list[str]:
    lines = []
    name = evidence["style"]
    level = confidence.get("level", "low")
    share = confidence.get("top_share", 0.0)

    if level == "high":
        lines.append(f"최근 경기의 {share:.0%}가 {name} 패턴에 모여 대표 스타일로 판정했습니다.")
    elif level == "medium":
        lines.append(f"{name}에 가장 가깝지만({share:.0%}) 경기별 편차가 있어 "
                     "단정하기는 어렵습니다.")
    else:
        lines.append("경기마다 플레이 형태가 달라 대표 스타일을 특정하기 어렵습니다. "
                     f"가장 가까운 쪽은 {name}입니다.")

    if evidence["matched_axes"]:
        detail = ", ".join(
            f"{e['metric']}({e['user']:+.2f} / 스타일 기준 {e['style_center']:+.2f})"
            for e in evidence["matched_axes"]
        )
        lines.append(f"판정 근거가 된 축은 {detail}입니다.")

    if evidence["off_style_axes"]:
        worst = evidence["off_style_axes"][0]
        if worst["match"] == MATCH_OFF:
            lines.append(
                f"다만 {worst['metric']}은(는) {name}의 기준({worst['style_center']:+.2f})과 "
                f"방향이 반대라({worst['user']:+.2f}) 스타일이 완성되지 않았습니다."
            )
        else:
            lines.append(
                f"{worst['metric']}은(는) 같은 스타일 기준({worst['style_center']:+.2f})보다 "
                f"약한 편입니다({worst['user']:+.2f})."
            )

    lines.extend(confidence.get("reasons", [])[:2])
    return lines


# ----------------------------------------------------------------------
# LLM 프롬프트 v2 — 신뢰도에 따라 규칙이 달라진다
# ----------------------------------------------------------------------
def build_llm_payload_v2(result: dict[str, Any]) -> dict[str, Any]:
    allowed = BASE_PAYLOAD_KEYS | EXPLAIN_PAYLOAD_KEYS
    return {key: value for key, value in result.items() if key in allowed}


def build_coaching_prompt_v2(result: dict[str, Any], player_name: str = "플레이어") -> str:
    payload = build_llm_payload_v2(result)
    if payload.get("status") != "ok":
        return f"분석 불가: {payload.get('status')}"

    level = (payload.get("style_confidence") or {}).get("level", "low")

    rules = [
        "입력 JSON에 없는 사실이나 원인을 추측하지 마라.",
        "숫자는 JSON에 있는 값만 사용하라.",
        "finding_tag와 prompt_hint를 그대로 지켜 코칭 강도를 정하라.",
        "스타일 판정 근거는 style_evidence.matched_axes 에 있는 지표로만 설명하라.",
        "style_evidence.off_style_axes 는 '같은 스타일 기준과의 차이'로만 설명하고 "
        "전체 평균과 비교하지 마라.",
    ]

    if level == "high":
        rules.append("대표 스타일을 확정적으로 서술해도 된다. 근거 축을 1~2개 인용하라.")
    elif level == "medium":
        rules.append("대표 스타일은 '~에 가깝다' 수준으로만 서술하고, "
                     "style_confidence.reasons 의 이유를 한 문장으로 밝혀라.")
    else:
        rules.append("대표 스타일을 단정하지 마라. 경기별 스타일이 흩어져 있다는 사실 자체를 "
                     "핵심 주제로 삼고, 한 가지 형태를 정해 반복하도록 코칭하라.")

    if not payload.get("has_improvements"):
        rules.append("뚜렷한 개선점이 없으므로 만들어내지 마라. 필요하면 완곡한 제안 1개만 하라.")
    else:
        rules.append("개선점은 improvements 의 실제 항목만 사용하고 최대 3개로 제한하라.")

    return (
        f"{player_name}님의 최근 LoL 플레이를 한국어로 코칭해라.\n"
        "구성: 스타일 판정과 근거 → 강점 → 스타일 기준 대비 부족한 축 → "
        "가장 중요한 개선 포인트 → 한 줄 요약.\n"
        "규칙:\n- " + "\n- ".join(rules) + "\n\n"
        "분석 JSON:\n" + json.dumps(payload, ensure_ascii=False, indent=2)
    )


__all__ = [
    "ExplainablePlayStyleAnalyzer",
    "StyleExplainMixin",
    "build_coaching_prompt_v2",
    "build_llm_payload_v2",
    "explanation_sentences",
    "make_explainable",
]
