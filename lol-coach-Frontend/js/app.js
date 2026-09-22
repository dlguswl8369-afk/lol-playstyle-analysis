const $ = (sel) => document.querySelector(sel);
const DDRAGON_VERSION = "16.18.1";

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

/* ===================== 화면 전환 ===================== */
function showView(name) {
    $("#homeView").hidden = name !== "home";
    $("#resultView").hidden = name !== "result";
    $("#styleView").hidden = name !== "style";
    $("#searchView").hidden = name !== "search";
    $("#reportView").hidden = name !== "report";
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
    if (card.dataset.view === "report") {
        showView("report");
        renderReportVault();
        return;
    }
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
        window.APP_STATE.summary = analysis.summary ?? {};
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
    const summary = analysis.summary ?? {};

    // --- 프로필 ---
    window.APP_STATE.rank = rank ?? null;
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

    const winRate = Number(summary.win_rate ?? 0);
    $("#pWin").textContent = `승률 ${winRate.toFixed(0)}%`;

    renderMetrics(summary.metrics ?? {});
    renderPlayStyleReport(null);
    renderTips(null);
    window.APP_STATE.coachingReport = null;
    window.APP_STATE.playStyle = null;
    window.APP_STATE.savedReportDraft = null;
    $("#styleSaveBtn").textContent = "리포트 생성 후 저장";
    $("#styleSaveBtn").disabled = true;
    $("#reportSaveBtn").textContent = "저장";
    $("#reportSaveBtn").disabled = true;
    $("#coachTarget").textContent = `${gameName} #${tagLine}`;
    $("#coachAnswer").innerHTML = `<p class="empty">Databricks 분석 결과를 보려면 코칭 리포트를 생성하세요.</p>`;
    renderTrend(matches.slice(0, 10).reverse());
    renderMatches(matches);
}
// 분석화면 
const METRIC_PRESENTATION = {
    kda: { label: "KDA", decimals: 2 },
    cs_per_min: { label: "분당 CS", decimals: 1 },
    gold_per_min: { label: "분당 골드", decimals: 0 },
    gold_share: { label: "팀 골드 점유율", decimals: 1, percent: true },
    kill_participation: { label: "킬 관여율", decimals: 1, percent: true },
    damage_per_min: { label: "분당 챔피언 피해", decimals: 0 },
    damage_share: { label: "팀 피해 점유율", decimals: 1, percent: true },
    damage_efficiency: { label: "피해 교환 효율", decimals: 2 },
    vision_score_per_min: { label: "분당 시야 점수", decimals: 2 },
    wards_placed_per_min: { label: "분당 와드 설치", decimals: 2 },
    wards_killed_per_min: { label: "분당 와드 제거", decimals: 2 },
    vision_wards_bought_per_min: { label: "분당 제어 와드 구매", decimals: 2 },
    objective_damage_per_min: { label: "분당 오브젝트 피해", decimals: 0 },
};

function metricValue(key, rawValue) {
    const config = METRIC_PRESENTATION[key] ?? { label: key, decimals: 2 };
    const value = Number(rawValue ?? 0) * (config.percent ? 100 : 1);
    return `${value.toFixed(config.decimals)}${config.percent ? "%" : ""}`;
}

/* --- CORE METRICS: backend summary, then Databricks benchmark --- */
function renderMetrics(summaryMetrics, priorityCandidates = null) {
    const hasBenchmark = Array.isArray(priorityCandidates) && priorityCandidates.length > 0;
    const rows = hasBenchmark
        ? priorityCandidates.filter((item) => METRIC_PRESENTATION[item.key]).map((item) => ({
            key: item.key,
            mine: Number(item.my_value ?? 0),
            benchmark: Number(item.benchmark_mean ?? 0),
        }))
        : Object.entries(summaryMetrics ?? {}).filter(([key]) => METRIC_PRESENTATION[key]).map(([key, value]) => ({
            key,
            mine: Number(value ?? 0) / (METRIC_PRESENTATION[key].percent ? 100 : 1),
            benchmark: null,
        }));

    if (!rows.length) {
        $("#metrics").innerHTML = `<p class="empty">표시할 백엔드 지표가 없습니다.</p>`;
        return;
    }

    $("#metricsMode").textContent = hasBenchmark ? "Databricks 벤치마크 대비" : "최근 경기 실측 평균";
    $("#metricsNote").textContent = hasBenchmark
        ? "점선은 Databricks가 선택한 동일 조건 벤치마크입니다."
        : "Riot 경기 데이터의 단순 평균이며 평가 문구를 생성하지 않습니다.";

    $("#metrics").innerHTML = rows.map(({ key, mine, benchmark }) => {
        const config = METRIC_PRESENTATION[key];
        const scaleMax = Math.max(mine, benchmark ?? 0, 0.000001);
        const mineWidth = Math.max(2, (mine / scaleMax) * 100);
        const benchmarkLeft = benchmark == null ? null : (benchmark / scaleMax) * 100;
        return `
            <div class="metric">
                <span>${escapeHtml(config.label)}</span>
                <strong>${metricValue(key, mine)}</strong>
                <div class="bar">
                    <div class="fill" style="width:${mineWidth}%"></div>
                    ${benchmarkLeft == null ? "" : `<i class="avg" style="left:${benchmarkLeft}%"></i>`}
                </div>
                <div class="diff"><span>${benchmark == null ? "" : `기준 ${metricValue(key, benchmark)}`}</span></div>
            </div>`;
    }).join("");
}

function renderPlayStyleReport(playstyleAnalysis, target = "#radar") {
    const container = $(target);
    if (!container) return;
    const style = playstyleAnalysis?.play_style;
    if (!style) {
        container.innerHTML = `<p class="empty">Databricks 코칭 리포트 생성 후 플레이스타일이 표시됩니다.</p>`;
        return;
    }

    const statusLabels = {
        single: "단일 스타일",
        mixed: "혼합 스타일",
        unstable: "변동형",
        insufficient: "경기 부족",
    };
    const styles = Array.isArray(style.styles) ? style.styles : [];
    const styleRows = styles.length
        ? styles.map((item) => `
            <div class="style-result-row">
                <div><strong>${escapeHtml(item.name ?? "분류 없음")}</strong><span>${Number(item.ratio ?? 0).toFixed(1)}%</span></div>
                <div class="style-result-bar"><i style="width:${Math.min(100, Math.max(0, Number(item.ratio ?? 0)))}%"></i></div>
            </div>`).join("")
        : `<p class="empty">${escapeHtml(style.message ?? "플레이스타일을 확정할 데이터가 부족합니다.")}</p>`;

    container.innerHTML = `
        <div class="style-result-head">
            <strong>${escapeHtml(statusLabels[style.status] ?? style.status ?? "분석 결과")}</strong>
            <span>${escapeHtml(playstyleAnalysis.main_position ?? "UNKNOWN")} · ${Number(playstyleAnalysis.games_analyzed ?? style.games ?? 0)}경기</span>
        </div>
        ${styleRows}`;
}

/* --- NEXT GAME: validated Databricks coaching only --- */
function renderTips(coaching) {
    if (!coaching) {
        $("#tips").innerHTML = `<li><div><strong>리포트 대기 중</strong><p>Databricks 분석이 완료되면 검증된 개선점이 표시됩니다.</p></div></li>`;
        return;
    }

    const items = [];
    if (coaching.primary_goal) items.push(coaching.primary_goal);
    for (const improvement of coaching.improvements ?? []) {
        if (!items.some((item) => item.key === improvement.key)) items.push(improvement);
    }
    if (!items.length) {
        $("#tips").innerHTML = `<li><div><strong>확정된 개선점 없음</strong><p>${escapeHtml(coaching.overall_comment ?? "백엔드 분석에서 우선 개선 항목을 확정하지 않았습니다.")}</p></div></li>`;
        return;
    }

    $("#tips").innerHTML = items.slice(0, 3).map((item) => {
        const detail = [item.description, item.action].filter(Boolean).join(" ");
        return `<li><div><strong>${escapeHtml(item.title)}</strong><p>${escapeHtml(detail)}</p></div></li>`;
    }).join("");
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
    return m.comment ?? "";
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

/* ===================== Databricks 코칭 리포트 ===================== */
$("#coachForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const { gameName = "", tagLine = "" } = window.APP_STATE.riotId ?? {};
    if (!gameName || !tagLine) return;
    const btn = $("#coachBtn");
    btn.disabled = true;
    btn.textContent = "생성 중...";
    $("#reportSaveBtn").disabled = true;
    $("#reportSaveBtn").textContent = "저장";
    const startedAt = Date.now();
    const updateWaitingMessage = () => {
        const elapsed = Math.floor((Date.now() - startedAt) / 1000);
        const stage = elapsed < 5
            ? "전적 데이터를 준비하고 있습니다."
            : elapsed < 20
                ? "Databricks 분석을 실행하고 있습니다."
                : "첫 실행은 Databricks 시작 때문에 시간이 더 걸릴 수 있습니다.";
        $("#coachAnswer").innerHTML = `<p class="empty">${stage} (${elapsed}초)</p>`;
    };
    updateWaitingMessage();
    const waitingTimer = window.setInterval(updateWaitingMessage, 1000);

    try {
        const result = await window.APP_API.getCoachingReport(gameName, tagLine, 10);
        applyCoachingReport(result, gameName, tagLine);
    } catch (error) {
        console.error("코칭 실패:", error);
        $("#coachAnswer").innerHTML = `<p class="empty">${escapeHtml(error.message)}</p>`;
    } finally {
        window.clearInterval(waitingTimer);
        btn.disabled = false;
        btn.textContent = "코칭 리포트 생성";
    }
});

function applyCoachingReport(payload, gameName, tagLine) {
    const report = payload.report ?? {};
    const analysis = report.analysis ?? {};
    const coaching = report.coaching ?? null;
    const priorityCandidates = analysis.priority?.priority_candidates ?? [];
    const playstyle = analysis.playstyle ?? null;

    window.APP_STATE.coachingReport = report;
    renderMetrics({}, priorityCandidates);
    renderPlayStyleReport(playstyle);
    renderTips(coaching);
    renderCoachReport(payload);

    const summary = window.APP_STATE.summary ?? {};
    window.APP_STATE.playStyle = playstyle ? {
        gameName,
        tagLine,
        playstyle,
        winRate: Number(summary.win_rate ?? 0),
        matchCount: Number(payload.match_count ?? summary.games ?? 0),
    } : null;
    $("#styleSaveBtn").textContent = playstyle ? "저장하기" : "저장할 결과 없음";
    $("#styleSaveBtn").disabled = !playstyle;

    window.APP_STATE.savedReportDraft = coaching ? {
        gameName, tagLine,
        payload: {
            cached: payload.cached,
            run_id: payload.run_id,
            match_count: payload.match_count,
            report: { coaching: report.coaching, player: { tactical_role: report.player?.tactical_role } },
        },
        winRate: Number(summary.win_rate ?? 0),
        tier: window.APP_STATE.rank?.tier ?? null,
    } : null;
    $("#reportSaveBtn").textContent = coaching ? "저장" : "저장할 리포트 없음";
    $("#reportSaveBtn").disabled = !coaching;
}

function reportItems(title, items, kind) {
    if (!Array.isArray(items) || !items.length) return "";
    return `
        <section class="coach-report-section ${kind}">
            <h3>${escapeHtml(title)}</h3>
            <ul>${items.map((item) => `
                <li>
                    <strong>${escapeHtml(item.title ?? item.key ?? "")}</strong>
                    <p>${escapeHtml([item.description, item.action].filter(Boolean).join(" "))}</p>
                </li>`).join("")}
            </ul>
        </section>`;
}

function renderCoachReport(payload, target = "#coachAnswer") {
    const report = payload.report ?? {};
    const coaching = report.coaching;
    if (!coaching) {
        const error = report.error?.message ?? "Databricks가 유효한 코칭 결과를 반환하지 않았습니다.";
        $(target).innerHTML = `<p class="empty">${escapeHtml(error)}</p>`;
        return;
    }

    const primary = coaching.primary_goal
        ? `<section class="coach-report-section primary">
               <h3>다음 경기 최우선 목표</h3>
               <strong>${escapeHtml(coaching.primary_goal.title)}</strong>
               <p>${escapeHtml(coaching.primary_goal.action)}</p>
           </section>`
        : "";
    const actionList = Array.isArray(coaching.coaching) && coaching.coaching.length
        ? `<section class="coach-report-section"><h3>실전 코칭</h3><ul>${coaching.coaching.map((item) => `<li><p>${escapeHtml(item)}</p></li>`).join("")}</ul></section>`
        : "";
    const metadata = [
        payload.cached ? "캐시 결과" : `Databricks Run ${payload.run_id ?? "-"}`,
        `${Number(payload.match_count ?? 0)}경기 분석`,
        report.player?.tactical_role ? `역할 ${report.player.tactical_role}` : null,
    ].filter(Boolean).join(" · ");

    $(target).innerHTML = `
        <div class="coach-report-summary">
            <strong>${escapeHtml(coaching.play_summary)}</strong>
            ${coaching.playstyle_summary ? `<p>${escapeHtml(coaching.playstyle_summary)}</p>` : ""}
        </div>
        ${reportItems("강점", coaching.strengths, "strength")}
        ${reportItems("개선점", coaching.improvements, "improvement")}
        ${primary}
        ${coaching.recent_growth ? `<p class="coach-report-note">${escapeHtml(coaching.recent_growth)}</p>` : ""}
        ${coaching.combat_comment ? `<p class="coach-report-note">${escapeHtml(coaching.combat_comment)}</p>` : ""}
        ${actionList}
        <p class="coach-report-overall">${escapeHtml(coaching.overall_comment)}</p>
        <div class="coach-meta">${escapeHtml(metadata)}</div>`;
}

renderRecent();

/* ===================== 플레이스타일 보관함 ===================== */
// 검색한 소환사의 플레이스타일을 저장해, 다시 볼 때 API를 부르지 않는다
const STYLE_KEY = "riftcoach.styles";
const STYLE_MAX = 10;

function loadStyles() {
    try { return JSON.parse(localStorage.getItem(STYLE_KEY)) ?? []; } catch { return []; }
}

function saveStyle(gameName, tagLine, playstyle, winRate, matchCount) {
    const id = `${gameName}#${tagLine}`;
    const entry = { id, gameName, tagLine, playstyle, winRate, matchCount, at: Date.now() };
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
        vault.innerHTML = `<p class="empty">코칭 리포트를 생성한 뒤 플레이스타일을 저장할 수 있습니다.</p>`;
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

    // Databricks가 반환한 저장 결과만 표시한다 — 네트워크 호출 없음
    list.forEach((e) => {
        const target = `[id="vault-${CSS.escape(e.id)}"]`;
        if (e.playstyle) {
            renderPlayStyleReport(e.playstyle, target);
        } else {
            $(target).innerHTML = `<p class="empty">이전 프런트 계산 데이터입니다. 다시 분석해 주세요.</p>`;
        }
    });
}

$("#styleSaveBtn").addEventListener("click", () => {
    const current = window.APP_STATE.playStyle;
    if (!current) return;

    saveStyle(current.gameName, current.tagLine, current.playstyle, current.winRate, current.matchCount);
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

/* ===================== 코칭 리포트 보관함 ===================== */
// 결과 화면의 코칭 리포트를 저장해, 다시 볼 때 API를 부르지 않는다
const REPORT_KEY = "riftcoach.reports";
const REPORT_MAX = 10;

function loadReports() {
    try { return JSON.parse(localStorage.getItem(REPORT_KEY)) ?? []; } catch { return []; }
}

function saveReport(draft) {
    const list = loadReports();
    const runId = draft.payload?.run_id;
    const isDuplicate = runId != null && list.some((x) =>
        x.gameName === draft.gameName && x.tagLine === draft.tagLine && x.payload?.run_id === runId);
    if (isDuplicate) return { added: false };

    const at = Date.now();
    const entry = { id: `${draft.gameName}#${draft.tagLine}@${at}`, ...draft, at };
    const next = [entry, ...list].slice(0, REPORT_MAX);
    try { localStorage.setItem(REPORT_KEY, JSON.stringify(next)); } catch { /* 저장 불가 환경 무시 */ }
    return { added: true };
}

function removeReport(id) {
    const list = loadReports().filter((x) => x.id !== id);
    try { localStorage.setItem(REPORT_KEY, JSON.stringify(list)); } catch { /* 저장 불가 환경 무시 */ }
    renderReportVault();
}

$("#reportLink").addEventListener("click", (event) => {
    event.preventDefault();
    showView("report");
    renderReportVault();
});

$("#reportSaveBtn").addEventListener("click", () => {
    const draft = window.APP_STATE.savedReportDraft;
    if (!draft) return;

    const { added } = saveReport(draft);
    const btn = $("#reportSaveBtn");
    btn.textContent = added ? "저장됨 · 코칭 리포트에서 보기" : "이미 저장된 리포트입니다";
    btn.disabled = true;
});

function renderReportVault() {
    const list = loadReports();
    const vault = $("#reportVault");

    if (list.length === 0) {
        vault.innerHTML = `<p class="empty">코칭 리포트를 생성한 뒤 저장할 수 있습니다.</p>`;
        return;
    }

    vault.innerHTML = list.map((e) => {
        const coaching = e.payload?.report?.coaching;
        const meta = [
            `최근 ${Number(e.payload?.match_count ?? 0)}경기 · 승률 ${Number(e.winRate ?? 0).toFixed(0)}%`,
            e.tier ? e.tier : null,
        ].filter(Boolean).join(" · ");
        return `
        <article class="report-card" data-id="${escapeHtml(e.id)}" data-name="${escapeHtml(e.gameName)}" data-tag="${escapeHtml(e.tagLine)}">
            <header>
                <button type="button" class="report-remove" aria-label="${escapeHtml(e.id)} 삭제">×</button>
                <strong>${escapeHtml(e.gameName)}<small>#${escapeHtml(e.tagLine)}</small></strong>
                <span>${escapeHtml(meta)}</span>
                <time>${new Date(e.at).toLocaleString("ko-KR", { dateStyle: "medium", timeStyle: "short" })} 기준</time>
            </header>
            <p class="report-summary">${escapeHtml(coaching?.play_summary ?? "")}</p>
            <details class="report-detail">
                <summary>리포트 전체 보기</summary>
                <div id="report-${escapeHtml(e.id)}"></div>
            </details>
            <button type="button" class="report-refresh">다시 분석</button>
        </article>`;
    }).join("");

    list.forEach((e) => {
        const target = `[id="report-${CSS.escape(e.id)}"]`;
        renderCoachReport(e.payload, target);
    });
}

$("#reportVault").addEventListener("click", (event) => {
    const card = event.target.closest(".report-card");
    if (!card) return;

    if (event.target.closest(".report-remove")) {
        removeReport(card.dataset.id);
        return;
    }
    // "다시 분석"을 눌렀을 때만 Riot API를 부른다
    if (event.target.closest(".report-refresh")) {
        runAnalysis(card.dataset.name, card.dataset.tag);
    }
});
