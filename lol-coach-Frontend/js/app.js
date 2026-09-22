const $ = (sel) => document.querySelector(sel);
const DDRAGON_VERSION = "16.18.1";

// 티어 평균 기준값 (백엔드에서 내려주면 analysis.tierAverage 로 대체됩니다)
const DEFAULT_TIER_AVG = { kda: 2.8, csPerMin: 6.5, goldPerMin: 400, killParticipation: 50, visionScore: 22 };

/* ===================== 테마 변경 ===================== */

const THEME_KEY = "riftcoach.theme";

const themeToggle = document.getElementById("themeToggle");

// 저장된 테마 불러오기
function loadTheme() {
    try {
        return localStorage.getItem(THEME_KEY) || "dark";
    } catch {
        return "dark";
    }
}

// 현재 테마에 맞게 버튼 글자 변경
function updateThemeButton() {
    if (!themeToggle) return;

    const isLightMode = document.body.classList.contains("light-mode");

    themeToggle.textContent = isLightMode ? "다크 모드로 전환" : "일반 모드로 전환";
}

// 테마 적용
function applyTheme(theme) {
    const isLightMode = theme === "light";
    document.body.classList.toggle("light-mode", isLightMode);
    updateThemeButton();
}

// 처음 페이지 열었을 때 저장된 테마 적용
applyTheme(loadTheme());

// 버튼 클릭
if (themeToggle) {

    themeToggle.addEventListener("click", () => {
        const isLightMode = document.body.classList.contains("light-mode");

        const nextTheme = isLightMode ? "dark" : "light";
        applyTheme(nextTheme);
        try {
            localStorage.setItem(THEME_KEY, nextTheme);
        } catch {
            // localStorage 사용 불가능한 환경은 무시
        }

    });

}

const escapeHtml = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const avg = (list, key) => list.reduce((sum, m) => sum + (m[key] ?? 0), 0) / list.length;
const clamp = (v, min, max) => Math.min(max, Math.max(min, v));

/* ===================== 화면 전환 ===================== */
function showView(name) {
    $("#homeView").hidden = name !== "home";
    $("#resultView").hidden = name !== "result";
    $("#styleView").hidden = name !== "style";
    $("#searchView").hidden = name !== "search";
    window.scrollTo(0, 0);
}

$("#logoLink").addEventListener("click", (e) => {
    e.preventDefault();
    showView("home");
});

/* ===================== 기능 카드 ===================== */
// 분석 결과가 있으면 해당 패널로, 없으면 검색창으로 보낸다
// 1. addEventlistener를 features 컨테이너에 붙여서 이벤트 위임함으로써 각 카드마다 개별 이벤트를 늘리지 않아도 된다.
// 2. window.APP_STATE.player && ...: 현재 전역 앱 상태(window.APP_STATE)에 player 정보가 존재하는지(예: 로그인 또는 캐릭터 선택이 완료되었는지) 먼저 확인한다.
document.querySelector(".features").addEventListener("click", (e) => { 
    const card = e.target.closest(".feature");
    if (!card) return;
// 1. 플레이어가 존재한다면, 클릭한 카드의 HTML 데이터 속성(data-target="아이디") 값을 이용해 화면에서 해당 ID를 가진 패널 요소를 찾는다. (여기서 $는 jQuery나 별도로 선언된 DOM 선택자 함수.)
// 2. 만약 panel이 존재하지 않는다면, 현재 전역 상태에 player 정보가 없거나 해당 패널이 DOM에 존재하지 않는다면, 검색 폼으로 스크롤하고 게임 이름 입력란에 포커스를 준다.
// 3. if (!panel): 만약 플레이어 상태가 없거나, 이동할 대상 패널을 찾지 못했다면 실행됩니다
    const panel = window.APP_STATE.player && $("#" + card.dataset.target);
    if (!panel) {
        document.querySelector(".search-form").scrollIntoView({ behavior: "smooth", block: "center" });
        $("#gameName").focus();
        return;
    }
// 4. showView("result"): 현재 화면을 "result" 뷰로 전환. (즉, 분석 결과 화면으로 이동)
// 5. panel.scrollIntoView({ behavior: "smooth", block: "center" }): 대상 패널을 화면 중앙으로 스크롤
// 6. panel.classList.add("flash"): 대상 패널에 "flash" 클래스를 추가하여 시각적 강조 효과를 줌
// 7. setTimeout(() => panel.classList.remove("flash"), 1200): 1.2초 후에 "flash" 클래스를 제거하여 강조 효과를 끝냄
    showView("result");
    panel.scrollIntoView({ behavior: "smooth", block: "center" });
    panel.classList.add("flash");
    setTimeout(() => panel.classList.remove("flash"), 1200);
});

/* ===================== 최근 검색 ===================== */
const RECENT_KEY = "riftcoach.recent";

function loadRecent() {
    try { return JSON.parse(localStorage.getItem(RECENT_KEY)) ?? []; } catch { return []; }
}
// 1. 현재 검색한 사람을 맨 앞에 넣고, 중복은 제거하고, 최근 3명까지만 저장한다.
function saveRecent(gameName, tagLine) {
    const id = `${gameName}#${tagLine}`;
    const list = [id, ...loadRecent().filter((x) => x !== id)].slice(0, 3);
    try { localStorage.setItem(RECENT_KEY, JSON.stringify(list)); } catch { /* 저장 불가 환경 무시 */ }
    renderRecent();
}

function renderRecent() {
    const list = loadRecent();
    $("#recentList").innerHTML = list.length
    ? list.map((id) => `<button type="button" class="chip">${escapeHtml(id)}</button>`).join("")
    : `<span>없음</span>`;
    // innerHTML에 넣어 최근 검색 기록을 화면에 버튼으로 표시된다.
    // map()을 사용해서 배열의 각 검색 기록을 HTML 버튼으로 바꾼다.
    // 원하는 문자가 없다면, hello도 인식될 수 있어. id를 그대로 HTML에 넣으면 사용자가 특수한 HTML 코드를 입력하면 문제가 생길 수 있다. (<,>,&,")
}

$("#recentList").addEventListener("click", (e) => {
    const chip = e.target.closest(".chip");
    if (!chip) return;
    const [name, tag] = chip.textContent.split("#");
    $("#gameName").value = name;
    $("#tagLine").value = tag;
    $("#searchForm").requestSubmit();
});

/* ===================== 검색 ===================== */
$("#searchForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    let gameName = $("#gameName").value.trim();
    let tagLine = $("#tagLine").value.trim().replace(/^#/, "") || "KR1";

    // "이름#태그" 를 한 칸에 입력한 경우도 허용
    if (gameName.includes("#")) [gameName, tagLine] = gameName.split("#").map((s) => s.trim());

    if (!gameName) {
        showSearchError("소환사 이름을 입력해주세요.");
        return;
    }

    await runAnalysis(gameName, tagLine);
});

// 오류는 팝업 대신 검색창 아래에 표시한다
function showSearchError(message, view = "home") {
    const isLookup = view === "search";
    const el = $(isLookup ? "#lookupError" : "#searchError");
    el.textContent = message;
    el.hidden = false;
    showView(isLookup ? "search" : "home");
    el.scrollIntoView({ behavior: "smooth", block: "center" });
}

async function runAnalysis(gameName, tagLine, view = "result") {
    $("#searchError").hidden = true;
    $("#lookupError").hidden = true;
    const buttons = [$("#searchBtn"), $("#lookupBtn")];
    buttons.forEach((b) => { b.disabled = true; b.textContent = "분석 중..."; });

    try {
        const analysis = await window.APP_API.getPlayerAnalysis(gameName, tagLine, 10);
        window.APP_STATE.player = analysis.profile;
        window.APP_STATE.riotId = { gameName, tagLine };
        window.APP_STATE.matches = analysis.matches ?? [];
        saveRecent(gameName, tagLine);
        

        if (view === "search") {
            // 전적 분석 화면: 경기 목록만 보여준다
            $("#lookupTitle").textContent = `${gameName} #${tagLine}`;
            $("#lookupTitle").hidden = false;
            renderMatches(window.APP_STATE.matches, "#searchResult");
            showView("search");
            return;
        }

        renderResult(analysis, gameName, tagLine);
        showView("result");
    } catch (error) {
        console.error("조회 실패:", error);
        showSearchError(error.message, view);
    } finally {
        buttons.forEach((b) => { b.disabled = false; b.textContent = "분석"; });
    }
}

/* ===================== 결과 화면 ===================== */
function renderResult(analysis, gameName, tagLine) {
    const matches = analysis.matches ?? [];
    const rank = analysis.rank;
    const tierAvg = { ...DEFAULT_TIER_AVG, ...(analysis.tierAverage ?? {}) };

    // --- 프로필 ---
    $("#pName").textContent = gameName;
    $("#pTag").textContent = `#${tagLine}`;
    const iconId = analysis.profile?.profileIconId;
    $("#profileIcon").innerHTML = iconId != null
        ? `<img src="https://ddragon.leagueoflegends.com/cdn/${DDRAGON_VERSION}/img/profileicon/${iconId}.png" alt="프로필 아이콘">`
        : "아이콘";

    const tier = rank?.tier ?? "UNRANKED";
    $("#pTier").innerHTML = rank
        ? `<img src="./assets/image/tier_images/${tier.toLowerCase()}.png" alt="">${tier} ${rank.rank} / ${rank.leaguePoints} LP`
        : "UNRANKED";
    $("#pSub").textContent = `솔로랭크 · 최근 ${matches.length}경기`;

    if (matches.length === 0) {
        $("#pWin").textContent = "";
        ["#metrics", "#radar", "#tips", "#trend", "#matchList"].forEach((sel) => {
            $(sel).innerHTML = `<p class="empty">최근 솔로랭크 경기 기록이 없습니다.</p>`;
        });
        return;
    }

    const wins = matches.filter((m) => m.win).length;
    const winRate = (wins / matches.length) * 100;
    $("#pWin").textContent = `승률 ${winRate.toFixed(0)}%`;

    const my = {
        kda: avg(matches, "kda"),
        csPerMin: avg(matches, "csPerMin"),
        goldPerMin: avg(matches, "goldPerMin"),
        killParticipation: avg(matches, "killParticipation"),
        visionScore: avg(matches, "visionScore"),
    };

    renderMetrics(my, tierAvg);
    const style = calcPlayStyle(matches, my, tierAvg);
    renderRadar(style);
    // 저장은 사용자가 "저장하기"를 눌렀을 때만 한다
    window.APP_STATE.playStyle = { gameName, tagLine, style, winRate, matchCount: matches.length };
    $("#styleSaveBtn").textContent = "저장하기";
    $("#styleSaveBtn").disabled = false;
    renderTips(analysis.aiCoach, my, tierAvg, matches);
    renderTrend(matches.slice(0, 10).reverse());
    renderMatches(matches);
}

/* --- CORE METRICS --- */
function renderMetrics(my, tierAvg) {
    const rows = [
        { label: "KDA", key: "kda", fmt: (v) => v.toFixed(2) },
        { label: "분당 CS", key: "csPerMin", fmt: (v) => v.toFixed(1) },
        { label: "분당 골드", key: "goldPerMin", fmt: (v) => v.toFixed(0) },
        { label: "킬 관여율", key: "killParticipation", fmt: (v) => `${v.toFixed(0)}%` },
        { label: "시야 점수", key: "visionScore", fmt: (v) => v.toFixed(1) },
    ];

    $("#metrics").innerHTML = rows.map(({ label, key, fmt }) => {
        const mine = my[key];
        const base = tierAvg[key];
        // 티어 평균을 바의 60% 지점에 두고 비례 표시
        const pct = clamp((mine / base) * 60, 2, 100);
        const ratio = (mine - base) / base;
        let diff = `<span>평균</span>`;
        if (Math.abs(ratio) >= 0.03) {
            const sign = ratio > 0 ? "+" : "-";
            diff = `<span class="${ratio > 0 ? "c-teal" : "c-red"}">${sign}${fmt(Math.abs(mine - base)).replace("%", "%p")}</span>`;
        }
        return `
            <div class="metric">
                <span>${label}</span>
                <strong>${fmt(mine)}</strong>
                <div class="bar"><div class="fill" style="width:${pct}%"></div><i class="avg" style="left:60%"></i></div>
                <div class="diff">${diff}</div>
            </div>`;
    }).join("");
}

/* --- PLAY STYLE (0~100, 티어 평균 = 50) --- */
function calcPlayStyle(matches, my, tierAvg) {
    const score = (mine, base) => clamp((mine / base) * 50, 5, 100);
    const deathsAvg = avg(matches, "deaths");
    const dmg = avg(matches, "damageToChampions");
    return [
        { label: "전투", value: score(my.kda, tierAvg.kda) * 0.6 + score(dmg, 18000) * 0.4 },
        { label: "성장", value: score(my.csPerMin, tierAvg.csPerMin) },
        { label: "운영", value: score(my.goldPerMin, tierAvg.goldPerMin) },
        { label: "시야", value: score(my.visionScore, tierAvg.visionScore) },
        { label: "오브젝트", value: score(my.killParticipation, tierAvg.killParticipation) },
        { label: "안정성", value: clamp(100 - deathsAvg * 9, 5, 100) },
    ];
}

function renderRadar(style, target = "#radar") {
    const size = 300, c = size / 2, r = 100;
    const n = style.length;
    const pt = (i, v) => {
        const a = (Math.PI * 2 * i) / n - Math.PI / 2;
        return [c + Math.cos(a) * r * v, c + Math.sin(a) * r * v];
    };
    const poly = (vals) => vals.map((v, i) => pt(i, v).join(",")).join(" ");

    const rings = [0.33, 0.66, 1].map((k) =>
        `<polygon points="${poly(Array(n).fill(k))}" fill="none" style="stroke:var(--line)"/>`).join("");
    const axes = style.map((_, i) => {
        const [x, y] = pt(i, 1);
        return `<line x1="${c}" y1="${c}" x2="${x}" y2="${y}" style="stroke:var(--line)"/>`;
    }).join("");
    const labels = style.map((s, i) => {
        const [x, y] = pt(i, 1.2);
        return `<text x="${x}" y="${y + 4}" text-anchor="middle" font-size="10" style="fill:var(--muted)">${s.label}</text>`;
    }).join("");

    $(target).innerHTML = `
        <svg viewBox="0 0 ${size} ${size}">
            ${rings}${axes}
            <polygon points="${poly(Array(n).fill(0.5))}" fill="none" stroke-dasharray="4 3" style="stroke:var(--muted)"/>
            <polygon points="${poly(style.map((s) => s.value / 100))}" stroke-width="2" style="fill:color-mix(in srgb, var(--teal) 35%, transparent);stroke:var(--teal)"/>
            ${labels}
        </svg>`;
}

/* --- NEXT GAME 코칭 --- */
function renderTips(aiCoach, my, tierAvg, matches) {
    // 백엔드 AI 코칭이 있으면 우선 사용: [{ title, detail }]
    let tips = Array.isArray(aiCoach?.tips) ? aiCoach.tips : null;

    if (!tips) {
        const pct = (k) => ((my[k] - tierAvg[k]) / tierAvg[k]) * 100;
        const candidates = [
            { gap: pct("csPerMin"), title: "라인 CS를 먼저 챙기세요", detail: `분당 CS가 티어 평균보다 ${Math.abs(pct("csPerMin")).toFixed(0)}% 낮습니다. 귀환 전 웨이브를 밀고 가는 습관부터.` },
            { gap: pct("visionScore"), title: "오브젝트 30초 전에 시야부터", detail: `시야 점수가 티어 평균보다 ${Math.abs(pct("visionScore")).toFixed(0)}% 낮습니다. 드래곤 · 전령 스폰 전 와드를 먼저 까세요.` },
            { gap: pct("killParticipation"), title: "교전에 더 자주 합류하세요", detail: `킬 관여율이 티어 평균보다 ${Math.abs(pct("killParticipation")).toFixed(0)}%p 낮습니다. 미니맵 체크 주기를 줄여보세요.` },
            { gap: pct("kda"), title: "무리한 진입을 줄이세요", detail: `평균 데스 ${avg(matches, "deaths").toFixed(1)}회. 시야 없는 곳에서의 진입이 KDA를 깎고 있습니다.` },
        ].sort((a, b) => a.gap - b.gap);

        tips = candidates.slice(0, 3);
        if (tips[0].gap >= 0) tips = [{ title: "지금 페이스를 유지하세요", detail: "모든 핵심 지표가 티어 평균 이상입니다. 연패 시 휴식만 챙기면 됩니다." }];
    }

    $("#tips").innerHTML = tips.map((t) =>
        `<li><div><strong>${escapeHtml(t.title)}</strong><p>${escapeHtml(t.detail)}</p></div></li>`).join("");
}

/* --- RECENT TREND (KDA 라인 차트) --- */
function renderTrend(list) {
    const w = 1000, h = 230;
    const padding = { top: 18, right: 20, bottom: 58, left: 52 };
    const values = list.map((match) => Number(match.kda) || 0);

    if (!values.length) {
        $("#trend").innerHTML = `<p class="empty">표시할 경기 데이터가 없습니다.</p>`;
        return;
    }

    const plotWidth = w - padding.left - padding.right;
    const plotHeight = h - padding.top - padding.bottom;
    const plotBottom = padding.top + plotHeight;
    const rawMax = Math.max(...values, 1);
    const yMax = Math.ceil(rawMax / 2) * 2;
    const yTickCount = 4;
    const yStep = yMax / yTickCount;
    const xStep = list.length > 1 ? plotWidth / (list.length - 1) : 0;

    const points = values.map((value, index) => {
        const x = padding.left + index * xStep;
        const y = plotBottom - (value / yMax) * plotHeight;
        return [x, y];
    });

    const line = points.map(([x, y]) => `${x},${y}`).join(" ");
    const area = `${points[0][0]},${plotBottom} ${line} ${points.at(-1)[0]},${plotBottom}`;

    const yGrid = Array.from({ length: yTickCount + 1 }, (_, index) => {
        const value = index * yStep;
        const y = plotBottom - (value / yMax) * plotHeight;

        return `
            <line x1="${padding.left}" x2="${w - padding.right}" y1="${y}" y2="${y}" style="stroke:var(--line)"/>
            <text x="${padding.left - 10}" y="${y + 4}" text-anchor="end" style="fill:var(--muted);font-size:11px">${value.toFixed(1)}</text>
        `;
    }).join("");

    const xLabels = points.map(([x], index) =>
        `<text x="${x}" y="${plotBottom + 24}" text-anchor="middle" style="fill:var(--muted);font-size:11px">${index + 1}</text>`
    ).join("");

    const circles = points.map(([x, y], index) =>
        `<circle cx="${x}" cy="${y}" r="4" stroke-width="1.5" style="fill:var(--panel);stroke:var(--gold)"><title>KDA ${values[index].toFixed(2)}</title></circle>`
    ).join("");

    $("#trend").innerHTML = `
        <svg viewBox="0 0 ${w} ${h}" role="img" aria-label="최근 10경기 KDA 추세">
            <defs>
                <linearGradient id="trendFill" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0" stop-color="#c8aa6e" stop-opacity=".35"/>
                    <stop offset="1" stop-color="#c8aa6e" stop-opacity="0"/>
                </linearGradient>
            </defs>

            ${yGrid}

            <line x1="${padding.left}" x2="${padding.left}" y1="${padding.top}" y2="${plotBottom}" style="stroke:var(--line)"/>
            <line x1="${padding.left}" x2="${w - padding.right}" y1="${plotBottom}" y2="${plotBottom}" style="stroke:var(--line)"/>
            <polygon points="${area}" fill="url(#trendFill)"/>
            <polyline points="${line}" fill="none" stroke="#c8aa6e" stroke-width="1.8"/>

            ${circles}
            ${xLabels}

            <text x="18" y="${padding.top + plotHeight / 2}" text-anchor="middle" transform="rotate(-90 18 ${padding.top + plotHeight / 2})" style="fill:var(--muted);font-size:11px">KDA</text>
            <text x="${padding.left + plotWidth / 2}" y="${h - 8}" text-anchor="middle" style="fill:var(--muted);font-size:11px">경기 순서 · 오래된 경기 → 최근 경기</text>
        </svg>
    `;
}

/* --- MATCH HISTORY --- */
function matchComment(m) {
    if (m.comment) return m.comment; // 백엔드 코멘트 우선
    if (m.win && m.killParticipation >= 60) return "높은 킬 관여로 팀 교전 주도";
    if (m.win && m.visionScore >= 30) return "시야 점수 개인 최고치급";
    if (!m.win && m.deaths >= 8) return `데스 ${m.deaths}회 — 무리한 진입 점검 필요`;
    if (!m.win && m.csPerMin < 5) return "CS 손실이 큰 경기";
    return m.win ? "안정적인 운영으로 승리" : "초반 격차를 뒤집지 못함";
}

const POSITION_LABEL = {
    TOP: "탑", JUNGLE: "정글", MIDDLE: "미드", BOTTOM: "원딜", UTILITY: "서포터",
};

// 지표 한 칸: 값 위, 라벨 아래
const cell = (cls, value, label) =>
    `<span class="${cls}"><b>${value}</b><i>${label}</i></span>`;

function renderMatches(matches, target = "#matchList") {
    const container = $(target);
    const matchList = Array.isArray(matches) ? matches : [];

    if (!container) return;
    if (matchList.length === 0) {
        container.innerHTML = `<p class="empty">최근 솔로랭크 경기 기록이 없습니다.</p>`;
        return;
    }

    container.innerHTML = matchList.map((m) => {
        const totalSeconds = Math.round((Number(m.gameMinutes) || 0) * 60);
        const minutes = Math.floor(totalSeconds / 60);
        const seconds = totalSeconds % 60;
        const position = POSITION_LABEL[m.teamPosition] ?? "";
        const items = m.items ?? [];

        const spellImages = [m.summoner1Id, m.summoner2Id]
            .filter((id) => Number(id) > 0)
            .map((id) => `<img src="./assets/image/summoner_spell_images/${id}.png" alt="소환사 주문">`)
            .join("");

        const runeImages = [
            m.primaryRuneId ? `./assets/image/rune_images/runes/${m.primaryRuneId}.png` : "",
            m.subRuneStyle ? `./assets/image/rune_images/paths/${m.subRuneStyle}.png` : "",
        ].filter(Boolean).map((src) => `<img src="${src}" alt="룬">`).join("");

        const itemImages = Array.from({ length: 7 }, (_, index) => {
            const itemId = Number(m.items?.[index] ?? 0);
            return itemId
                ? `<img src="./assets/image/item_images/${itemId}.png" alt="아이템">`
                : `<span class="item-empty"></span>`;
        }).join("");

        return `
            <div class="match ${m.win ? "win" : "lose"}">
                <span class="result"><b>${m.win ? "승리" : "패배"}</b><i>${minutes}분 ${String(seconds).padStart(2, "0")}초</i></span>

                <div class="match-loadout">
                    <img class="champion-image" src="./assets/image/champion_images/${escapeHtml(m.championName)}.png" alt="${escapeHtml(m.championName)}">
                    <div class="spell-images">${spellImages}</div>
                    <div class="rune-images">${runeImages}</div>
                </div>

                <span class="champ"><b>${escapeHtml(m.championName)}</b><i>Lv ${m.championLevel ?? "-"}${position ? " · " + position : ""}</i></span>

                <div class="item-images">${itemImages}</div>

                ${cell("kdaline", `${m.kills} / ${m.deaths} / ${m.assists}`, `KDA ${(m.kda ?? 0).toFixed(2)}`)}
                ${cell("cs", m.totalCS ?? "-", `분당 ${(m.csPerMin ?? 0).toFixed(1)}`)}
                ${cell("kp", `${Math.round(m.killParticipation ?? 0)}%`, "킬 관여")}
                ${cell("dmg", `${((m.damageToChampions ?? 0) / 1000).toFixed(1)}k`, "딜량")}
                ${cell("gold", Math.round(m.goldPerMin ?? 0), "분당 골드")}
                ${cell("vision", m.visionScore ?? "-", "시야")}

                <span class="comment">${escapeHtml(matchComment(m))}</span>
            </div>
        `;
    }).join("");
}

// 나브 "전적 분석" → 전용 전적 검색 화면
$("#searchLink").addEventListener("click", (event) => {
    event.preventDefault();
    showView("search");
    $("#lookupError").hidden = true;
    $("#lookupTitle").hidden = true;
    renderLookupRecent();
    $("#lookupName").focus();
});

$("#lookupForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    let gameName = $("#lookupName").value.trim();
    let tagLine = $("#lookupTag").value.trim().replace(/^#/, "") || "KR1";

    // "이름#태그"를 소환사 이름 입력란 하나에 입력한 경우도 허용한다.
    if (gameName.includes("#")) [gameName, tagLine] = gameName.split("#").map((value) => value.trim());

    if (!gameName) {
        showSearchError("소환사 이름을 입력해주세요.", "search");
        return;
    }

    await runAnalysis(gameName, tagLine, "search");
});

function renderLookupRecent() {
    const list = loadRecent();
    $("#searchResult").innerHTML = list.length
        ? `<div class="lookup-recent">
               <p class="lookup-label">최근 검색</p>
               <div>${list.map((id) => `<button type="button" class="chip">${escapeHtml(id)}</button>`).join("")}</div>
           </div>`
        : `<p class="empty">소환사 이름과 태그를 입력하면 최근 10경기를 분석합니다.</p>`;
}

$("#searchResult").addEventListener("click", (event) => {
    const chip = event.target.closest(".chip");
    if (!chip) return;

    const [gameName, tagLine = "KR1"] = chip.textContent.split("#");
    $("#lookupName").value = gameName;
    $("#lookupTag").value = tagLine;
    $("#lookupForm").requestSubmit();
});

/* ===================== 코칭 리포트 (RAG) ===================== */
$("#coachForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const input = $("#coachQuestion");
    const question = input.value.trim();
    if (!question) return;

    const { gameName = "", tagLine = "" } = window.APP_STATE.riotId ?? {};
    const btn = $("#coachBtn");
    btn.disabled = true;
    btn.textContent = "생각 중...";
    $("#coachAnswer").innerHTML = `<p class="empty">답변을 생성하고 있습니다...</p>`;

    try {
        const result = await window.APP_API.askCoach(question, gameName, tagLine, 10);
        renderCoachAnswer(question, result);
        input.value = "";
    } catch (error) {
        console.error("코칭 실패:", error);
        $("#coachAnswer").innerHTML = `<p class="empty">${escapeHtml(error.message)}</p>`;
    } finally {
        btn.disabled = false;
        btn.textContent = "질문";
    }
});

const ROUTE_LABEL = {
    official_information: "공식 문서",
    personal_analysis: "내 전적",
    mixed: "공식 문서 + 내 전적",
};

function renderCoachAnswer(question, result) {
    const answer = (result.answer ?? "").trim() || "답변을 생성하지 못했습니다.";
    const citations = Array.isArray(result.citations) ? result.citations : [];

    const sources = citations.length
        ? `<ul class="coach-cites">${citations.map((c) => {
            const title = escapeHtml(c.title || c.document_id || "출처");
            return c.source_url
                ? `<li><a href="${escapeHtml(c.source_url)}" target="_blank" rel="noopener">${title}</a></li>`
                : `<li>${title}</li>`;
        }).join("")}</ul>`
        : "";

    $("#coachAnswer").innerHTML = `
        <p class="coach-q">${escapeHtml(question)}</p>
        <div class="coach-a">${escapeHtml(answer).replace(/\n/g, "<br>")}</div>
        <div class="coach-meta">${escapeHtml(ROUTE_LABEL[result.route] ?? result.route ?? "")}</div>
        ${sources}`;
}

renderRecent();

/* ===================== 플레이스타일 보관함 ===================== */
// 검색한 소환사의 플레이스타일을 저장해, 다시 볼 때 API를 부르지 않는다
const STYLE_KEY = "riftcoach.styles";
const STYLE_MAX = 10;

function loadStyles() {
    try { return JSON.parse(localStorage.getItem(STYLE_KEY)) ?? []; } catch { return []; }
}

function saveStyle(gameName, tagLine, style, winRate, matchCount) {
    const id = `${gameName}#${tagLine}`;
    const entry = { id, gameName, tagLine, style, winRate, matchCount, at: Date.now() };
    const list = [entry, ...loadStyles().filter((x) => x.id !== id)].slice(0, STYLE_MAX);
    try { localStorage.setItem(STYLE_KEY, JSON.stringify(list)); } catch { /* 저장 불가 환경 무시 */ }
}

$("#styleLink").addEventListener("click", (event) => {
    event.preventDefault();
    showView("style");
    renderStyleVault();
});

function renderStyleVault() {
    const list = loadStyles();
    const vault = $("#styleVault");

    if (list.length === 0) {
        vault.innerHTML = `<p class="empty">먼저 소환사를 검색하면 플레이스타일이 여기에 저장됩니다.</p>`;
        return;
    }

    vault.innerHTML = list.map((e) => `
        <article class="style-card" data-id="${escapeHtml(e.id)}" data-name="${escapeHtml(e.gameName)}" data-tag="${escapeHtml(e.tagLine)}">
            <header>
                <button type="button" class="style-remove" aria-label="${escapeHtml(e.id)} 삭제">×</button>
                <strong>${escapeHtml(e.gameName)}<small>#${escapeHtml(e.tagLine)}</small></strong>
                <span>최근 ${e.matchCount}경기 · 승률 ${e.winRate.toFixed(0)}%</span>
                <time>${new Date(e.at).toLocaleDateString("ko-KR")} 기준</time>
            </header>
            <div class="chart-box" id="vault-${escapeHtml(e.id)}"></div>
            <button type="button" class="style-refresh">다시 분석</button>
        </article>`).join("");

    // 저장된 값으로 그린다 — 네트워크 호출 없음
    list.forEach((e) => renderRadar(e.style, `[id="vault-${CSS.escape(e.id)}"]`));
}

$("#styleSaveBtn").addEventListener("click", () => {
    const current = window.APP_STATE.playStyle;
    if (!current) return;

    saveStyle(current.gameName, current.tagLine, current.style, current.winRate, current.matchCount);
    const btn = $("#styleSaveBtn");
    btn.textContent = "저장됨 · 플레이스타일에서 보기";
    btn.disabled = true;
});

function removeStyle(id) {
    const list = loadStyles().filter((x) => x.id !== id);
    try { localStorage.setItem(STYLE_KEY, JSON.stringify(list)); } catch { /* 저장 불가 환경 무시 */ }
    renderStyleVault();
}

$("#styleVault").addEventListener("click", (event) => {
    const card = event.target.closest(".style-card");
    if (!card) return;

    if (event.target.closest(".style-remove")) {
        removeStyle(card.dataset.id);
        return;
    }
    // "다시 분석"을 눌렀을 때만 Riot API를 부른다
    if (event.target.closest(".style-refresh")) {
        runAnalysis(card.dataset.name, card.dataset.tag);
    }
});
