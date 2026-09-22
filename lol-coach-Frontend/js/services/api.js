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

async function askCoach(question, gameName, tagLine, matchCount = 10) {
  const response = await fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      question,
      riot_id: gameName || null,
      tag_line: tagLine || null,
      match_count: matchCount,
    }),
  });

  if (!response.ok) {
    throw new Error(`코칭 요청에 실패했습니다 (HTTP ${response.status}).`);
  }

  return response.json();
}

window.APP_API = { getPlayerAnalysis, askCoach };
