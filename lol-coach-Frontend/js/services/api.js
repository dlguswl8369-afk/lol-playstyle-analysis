async function getPlayerAnalysis(gameName, tagLine, matchCount = 20) {
  const response = await fetch("/api/player-analysis", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      riot_id: gameName,
      tag_line: tagLine,
      match_count: matchCount,
    }),
  });

  if (!response.ok) {
    let errorCode = `HTTP_${response.status}`;
    try {
      const body = await response.json();
      errorCode = body?.detail?.error_code ?? errorCode;
    } catch (_) {
      // Keep the safe HTTP fallback when the server did not return JSON.
    }
    const messages = {
      RIOT_ACCOUNT_NOT_FOUND: "플레이어를 찾을 수 없습니다.",
      RIOT_API_KEY_EXPIRED_OR_FORBIDDEN: "Riot API 키가 만료되었거나 권한이 없습니다.",
      RIOT_RATE_LIMITED: "Riot API 요청 한도를 초과했습니다. 잠시 후 다시 시도해 주세요.",
    };
    throw new Error(messages[errorCode] ?? `플레이어 조회에 실패했습니다 (${errorCode}).`);
  }

  return response.json();
}

async function getCoachingReport(gameName, tagLine, matchCount = 10) {
  const response = await fetch("/api/coaching-report", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      riot_id: gameName,
      tag_line: tagLine,
      match_count: matchCount,
    }),
  });

  if (!response.ok) {
    let message = `코칭 리포트 생성에 실패했습니다 (HTTP ${response.status}).`;
    try {
      const body = await response.json();
      const detail = body?.detail;
      if (detail?.error_code === "DATABRICKS_CONFIG_MISSING") {
        message = `Databricks 설정이 필요합니다: ${(detail.missing ?? []).join(", ")}`;
      } else if (detail?.message) {
        message = detail.message;
      }
    } catch (_) {
      // Keep the safe HTTP fallback when the server did not return JSON.
    }
    throw new Error(message);
  }

  return response.json();
}

async function askRag({ mode, question, gameName = "", tagLine = "", matchCount = 10 }) {
  const payload = { mode, question };
  if (mode === "personal") {
    payload.game_name = gameName;
    payload.tag_line = tagLine;
    payload.match_count = matchCount;
  }

  const response = await fetch("/api/rag", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });

  if (!response.ok) {
    let message = `정보 검색에 실패했습니다 (HTTP ${response.status}).`;
    try {
      const body = await response.json();
      const detail = body?.detail;
      if (detail?.message) {
        message = detail.message;
      } else if (detail?.error_code === "RAG_CONFIG_MISSING") {
        message = `RAG 설정이 필요합니다: ${(detail.missing ?? []).join(", ")}`;
      }
    } catch (_) {
      // Keep the HTTP fallback when the server did not return JSON.
    }
    throw new Error(message);
  }

  return response.json();
}

window.APP_API = { getPlayerAnalysis, getCoachingReport, askRag };
