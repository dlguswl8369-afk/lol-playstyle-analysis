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
function setHidden(selector, hidden) {
    const element = $(selector);
    if (element) element.hidden = hidden;
}

function showView(name) {
    setHidden("#homeView", name !== "home");
    setHidden("#resultView", name !== "result");
    setHidden("#coachingView", name !== "coaching");
    setHidden("#metricsView", name !== "metrics");
    setHidden("#styleView", name !== "style");
    setHidden("#searchView", name !== "search");
    setHidden("#infoView", name !== "info");
    setHidden("#reportView", name !== "report");

    const activeLinkByView = {
        style: "styleLink",
        search: "searchLink",
        info: "infoLink",
        metrics: "benchLink",
        report: "coachLink",
    };
    document.querySelectorAll(".nav-menu a").forEach((link) => {
        link.classList.toggle("active", link.id === activeLinkByView[name]);
    });

    window.scrollTo(0, 0);
}

$("#logoLink").addEventListener("click", (e) => {
    e.preventDefault();
    showView("home");
});

$("#infoLink").addEventListener("click", (event) => {
    event.preventDefault();
    showView("info");
    $("#infoQuestion").focus();
});

function openMetricsComparison() {
    showView("metrics");
    renderMetricsVault();
}

function openStyleVault() {
    showView("style");
    renderStyleVault();
}

function openReportVault() {
    showView("report");
    renderReportVault();
}

$("#styleLink").addEventListener("click", (event) => {
    event.preventDefault();
    openStyleVault();
});

$("#benchLink").addEventListener("click", (event) => {
    event.preventDefault();
    openMetricsComparison();
});

$("#coachLink").addEventListener("click", (event) => {
    event.preventDefault();
    openReportVault();
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
    if (card.dataset.target === "stylePanel") {
        openStyleVault();
        return;
    }
    if (card.dataset.target === "metricsPanel") {
        openMetricsComparison();
        return;
    }
    if (card.dataset.target === "trendPanel") {
        openReportVault();
        return;
    }
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
    if (!el) return;
    el.textContent = message;
    setHidden(isLookup ? "#lookupError" : "#searchError", false);
    showView(isLookup ? "search" : "home");
    el.scrollIntoView({ behavior: "smooth", block: "center" });
}

async function runAnalysis(gameName, tagLine, view = "result") {
    setHidden("#searchError", true);
    setHidden("#lookupError", true);
    const buttons = [$("#searchBtn"), $("#lookupBtn")];
    buttons.forEach((b) => { b.disabled = true; b.textContent = "분석 중..."; });

    try {
        const analysis = await window.APP_API.getPlayerAnalysis(gameName, tagLine, 10);
        window.APP_STATE.player = analysis.profile;
        window.APP_STATE.rank = analysis.rank ?? null;
        window.APP_STATE.riotId = { gameName, tagLine };
        saveInfoRiotContext({ gameName, tagLine });
        window.APP_STATE.matches = analysis.matches ?? [];
        window.APP_STATE.summary = analysis.summary ?? {};
        saveRecent(gameName, tagLine);


        if (view === "search") {
            // 전적 분석 화면: 경기 목록만 보여준다
            $("#lookupTitle").textContent = `${gameName} #${tagLine}`;
            setHidden("#lookupTitle", false);
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
    window.APP_STATE.currentMetricsComparison = null;

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
    window.APP_STATE.savedReportDraft = null;
    $("#reportSaveBtn").disabled = true;
    $("#reportSaveBtn").textContent = "리포트 생성 후 저장";

    if (matches.length === 0) {
        $("#pWin").textContent = "";
        $("#metricsSaveBtn").disabled = true;
        $("#metricsSaveBtn").textContent = "저장할 지표 없음";
        ["#metrics", "#radar", "#tips", "#trend", "#matchList"].forEach((sel) => {
            $(sel).innerHTML = `<p class="empty">최근 솔로랭크 경기 기록이 없습니다.</p>`;
        });
        return;
    }

    const winRate = Number(summary.win_rate ?? 0);
    $("#pWin").textContent = `승률 ${winRate.toFixed(0)}%`;

    renderMetrics(summary.metrics ?? {});
    syncMetricsSaveButton(gameName, tagLine);
    renderPlayStyleReport(null);
    renderTips(null);
    window.APP_STATE.coachingReport = null;
    window.APP_STATE.playStyle = null;
    $("#styleSaveBtn").textContent = "리포트 생성 후 저장";
    $("#styleSaveBtn").disabled = true;
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

/* --- 조건별 지표 저장 및 비교 --- */
const METRICS_STORAGE_KEY = "riftcoach.metrics";
const METRICS_STORAGE_MAX = 10;

function loadSavedMetrics() {
    try {
        const value = JSON.parse(localStorage.getItem(METRICS_STORAGE_KEY));
        return Array.isArray(value) ? value.slice(0, METRICS_STORAGE_MAX) : [];
    } catch {
        return [];
    }
}

function persistSavedMetrics(list) {
    try {
        localStorage.setItem(METRICS_STORAGE_KEY, JSON.stringify(list));
        return true;
    } catch {
        return false;
    }
}

function normalizedSummaryMetrics(summaryMetrics) {
    return Object.fromEntries(
        Object.entries(summaryMetrics ?? {})
            .filter(([key, value]) => METRIC_PRESENTATION[key] && Number.isFinite(Number(value)))
            .map(([key, value]) => [
                key,
                Number(value) / (METRIC_PRESENTATION[key].percent ? 100 : 1),
            ])
    );
}

function currentMetricsSnapshot() {
    const comparison = window.APP_STATE.currentMetricsComparison;
    if (Array.isArray(comparison?.rows) && comparison.rows.length) {
        return {
            mode: comparison.hasBenchmark ? "benchmark" : "average",
            metrics: Object.fromEntries(comparison.rows.map((row) => [row.key, row.mine])),
            benchmarks: Object.fromEntries(
                comparison.rows
                    .filter((row) => row.benchmark != null)
                    .map((row) => [row.key, row.benchmark])
            ),
        };
    }
    return {
        mode: "average",
        metrics: normalizedSummaryMetrics(window.APP_STATE.summary?.metrics),
        benchmarks: {},
    };
}

function currentMetricsId() {
    const { gameName = "", tagLine = "" } = window.APP_STATE.riotId ?? {};
    return gameName && tagLine ? `${gameName}#${tagLine}` : "";
}

function syncMetricsSaveButton(gameName, tagLine) {
    const button = $("#metricsSaveBtn");
    const { metrics } = currentMetricsSnapshot();
    const id = gameName && tagLine ? `${gameName}#${tagLine}` : "";
    const saved = loadSavedMetrics();
    const alreadySaved = saved.some((entry) => entry.id?.toLocaleLowerCase() === id.toLocaleLowerCase());

    if (!id || !Object.keys(metrics).length) {
        button.disabled = true;
        button.textContent = "저장할 지표 없음";
        return;
    }
    if (saved.length >= METRICS_STORAGE_MAX && !alreadySaved) {
        button.disabled = true;
        button.textContent = "저장 공간 가득 참 · 비교 화면에서 삭제하세요";
        return;
    }
    button.disabled = false;
    button.textContent = alreadySaved ? "저장된 조건별 지표 업데이트" : "조건별 지표 저장하기";
}

function saveCurrentMetrics() {
    const id = currentMetricsId();
    const { gameName = "", tagLine = "" } = window.APP_STATE.riotId ?? {};
    const summary = window.APP_STATE.summary ?? {};
    const snapshot = currentMetricsSnapshot();
    const metrics = snapshot.metrics;
    if (!id || !Object.keys(metrics).length) return false;

    const list = loadSavedMetrics();
    const existingIndex = list.findIndex(
        (entry) => entry.id?.toLocaleLowerCase() === id.toLocaleLowerCase()
    );
    if (existingIndex < 0 && list.length >= METRICS_STORAGE_MAX) return false;

    const rank = window.APP_STATE.rank;
    const entry = {
        id,
        gameName,
        tagLine,
        rank: rank ? {
            tier: rank.tier ?? "",
            division: rank.rank ?? "",
            leaguePoints: Number(rank.leaguePoints ?? 0),
        } : null,
        matchCount: Number(summary.games ?? 0),
        winRate: Number(summary.win_rate ?? 0),
        metrics,
        benchmarks: snapshot.benchmarks,
        mode: snapshot.mode,
        at: Date.now(),
    };
    const next = [entry, ...list.filter((_, index) => index !== existingIndex)];
    return persistSavedMetrics(next.slice(0, METRICS_STORAGE_MAX));
}

function savedRankText(entry) {
    if (!entry.rank?.tier) return "UNRANKED";
    return [
        entry.rank.tier,
        entry.rank.division,
        `${entry.rank.leaguePoints ?? 0} LP`,
    ].filter(Boolean).join(" ");
}

function renderMetricsVault() {
    const list = loadSavedMetrics();
    $("#metricsSavedCount").textContent = `${list.length} / ${METRICS_STORAGE_MAX} 저장`;
    if (!list.length) {
        $("#metricsVault").innerHTML = `
            <p class="empty">소환사를 분석한 뒤 결과 화면에서 조건별 지표를 저장해 주세요.</p>`;
        return;
    }

    const conditionRows = [
        ["티어", (entry) => savedRankText(entry)],
        ["분석 경기", (entry) => `${entry.matchCount ?? 0}경기`],
        ["승률", (entry) => `${Number(entry.winRate ?? 0).toFixed(1)}%`],
    ];
    const metricKeys = Object.keys(METRIC_PRESENTATION).filter((key) =>
        list.some((entry) => entry.metrics && entry.metrics[key] != null)
    );
    const header = list.map((entry) => `
        <th scope="col">
            <div class="metrics-player-head">
                <button type="button" class="metrics-remove" data-id="${escapeHtml(entry.id)}"
                        aria-label="${escapeHtml(entry.id)} 지표 삭제">삭제</button>
                <strong>${escapeHtml(entry.gameName)}<small>#${escapeHtml(entry.tagLine)}</small></strong>
                <small>${new Date(entry.at).toLocaleDateString("ko-KR")} 저장</small>
            </div>
        </th>`).join("");
    const detailHeader = list.map((entry) => `
        <th scope="col">
            <div class="metrics-player-head compact">
                <strong>${escapeHtml(entry.gameName)}<small>#${escapeHtml(entry.tagLine)}</small></strong>
            </div>
        </th>`).join("");
    const conditions = conditionRows.map(([label, value]) => `
        <tr class="condition-row">
            <th scope="row" class="metric-label">${label}</th>
            ${list.map((entry) => `<td>${escapeHtml(value(entry))}</td>`).join("")}
        </tr>`).join("");
    const metrics = metricKeys.map((key) => `
        <tr>
            <th scope="row" class="metric-label">${escapeHtml(METRIC_PRESENTATION[key].label)}</th>
            ${list.map((entry) => {
                if (entry.metrics?.[key] == null) return `<td>-</td>`;
                const benchmark = entry.benchmarks?.[key];
                return `<td>
                    <strong>${metricValue(key, entry.metrics[key])}</strong>
                    ${benchmark == null ? "" : `<small class="metrics-benchmark">기준 ${metricValue(key, benchmark)}</small>`}
                </td>`;
            }).join("")}
        </tr>`).join("");

    $("#metricsVault").innerHTML = `
        <div class="metrics-compare-scroll">
            <table class="metrics-compare-table">
                <thead><tr><th scope="col" class="metric-label">조건 / 지표</th>${header}</tr></thead>
                <tbody>${conditions}</tbody>
            </table>
        </div>
        <details class="metrics-detail">
            <summary>
                <span class="metrics-detail-open">상세 지표 더보기</span>
                <span class="metrics-detail-close">상세 지표 접기</span>
            </summary>
            <div class="metrics-compare-scroll">
                <table class="metrics-compare-table">
                    <thead><tr><th scope="col" class="metric-label">세부 지표</th>${detailHeader}</tr></thead>
                    <tbody>${metrics}</tbody>
                </table>
            </div>
        </details>`;
}

$("#metricsSaveBtn").addEventListener("click", () => {
    const button = $("#metricsSaveBtn");
    if (!saveCurrentMetrics()) {
        syncMetricsSaveButton(
            window.APP_STATE.riotId?.gameName,
            window.APP_STATE.riotId?.tagLine,
        );
        return;
    }
    button.textContent = "저장 완료";
    window.setTimeout(() => {
        syncMetricsSaveButton(
            window.APP_STATE.riotId?.gameName,
            window.APP_STATE.riotId?.tagLine,
        );
    }, 1200);
});

$("#metricsVault").addEventListener("click", (event) => {
    const removeButton = event.target.closest(".metrics-remove");
    if (!removeButton) return;
    const next = loadSavedMetrics().filter((entry) => entry.id !== removeButton.dataset.id);
    persistSavedMetrics(next);
    renderMetricsVault();
    syncMetricsSaveButton(
        window.APP_STATE.riotId?.gameName,
        window.APP_STATE.riotId?.tagLine,
    );
});

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
        window.APP_STATE.currentMetricsComparison = null;
        $("#metrics").innerHTML = `<p class="empty">표시할 백엔드 지표가 없습니다.</p>`;
        return;
    }

    window.APP_STATE.currentMetricsComparison = {
        hasBenchmark,
        rows: rows.map((row) => ({ ...row })),
    };

    $("#metricsMode").textContent = hasBenchmark ? "Databricks 벤치마크 대비" : "최근 경기 실측 평균";
    $("#metricsNote").textContent = hasBenchmark
        ? "점선은 Databricks가 선택한 동일 조건 벤치마크입니다."
        : "경기 데이터의 단순 평균이며 평가 문구를 생성하지 않습니다.";

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

function renderPlayStyleReport(playstyleAnalysis, priorityCandidates = [], target = "#radar") {
    if (typeof priorityCandidates === "string") {
        target = priorityCandidates;
        priorityCandidates = [];
    }

    const container = $(target);
    if (!container) return;

    const style = playstyleAnalysis?.play_style;

    if (!style) {
        container.innerHTML = `<p class="empty">코칭 리포트 생성 후 플레이스타일이 표시됩니다.</p>`;
        return;
    }

    const statusLabels = {
        single: "단일 스타일",
        mixed: "혼합 스타일",
        unstable: "변동형",
        insufficient: "경기 부족",
    };

    const styles = Array.isArray(style.styles) ? style.styles : [];
    const primaryStyle = styles[0] ?? null;

    const styleRows = styles.length
        ? styles.map((item) => `
        <div class="style-result-row">
            <div>
                <strong>${escapeHtml(item.name ?? "분류 없음")}</strong>
                <span>${Number(item.ratio ?? 0).toFixed(1)}%</span>
            </div>

            <div class="style-result-bar">
                <i style="width:${Math.min(100, Math.max(0, Number(item.ratio ?? 0)))}%"></i>
            </div>
        </div>
    `).join("")
        : `<p class="empty">${escapeHtml(style.message ?? "플레이스타일을 확정할 데이터가 부족합니다.")}</p>`;

    const benchmarkMap = Object.fromEntries(
        priorityCandidates.map((item) => [item.key, item])
    );

    const ratioScore = (key, inverse = false) => {
        const item = benchmarkMap[key];
        const mine = Number(item?.my_value ?? 0);
        const benchmark = Number(item?.benchmark_mean ?? 0);

        if (mine <= 0 || benchmark <= 0) return 50;

        const score = inverse
            ? 50 * (benchmark / mine)
            : 50 * (mine / benchmark);

        return Math.min(100, Math.max(5, score));
    };

    const average = (...values) =>
        values.reduce((sum, value) => sum + value, 0) / values.length;

    const radarData = [
        {
            label: "전투",
            value: average(
                ratioScore("kda"),
                ratioScore("damage_per_min")
            ),
        },
        {
            label: "성장",
            value: average(
                ratioScore("cs_per_min"),
                ratioScore("gold_per_min")
            ),
        },
        {
            label: "운영",
            value: average(
                ratioScore("kill_participation"),
                ratioScore("gold_share")
            ),
        },
        {
            label: "시야",
            value: average(
                ratioScore("vision_score_per_min"),
                ratioScore("wards_placed_per_min"),
                ratioScore("wards_killed_per_min")
            ),
        },
        {
            label: "오브젝트",
            value: ratioScore("objective_damage_per_min"),
        },
        {
            label: "안정성",
            value: ratioScore("deaths", true),
        },
    ];

    const size = 340;
    const center = size / 2;
    const radius = 105;
    const count = radarData.length;

    const point = (index, value) => {
        const angle = (Math.PI * 2 * index) / count - Math.PI / 2;

        return [
            center + Math.cos(angle) * radius * value,
            center + Math.sin(angle) * radius * value,
        ];
    };

    const polygon = (values) =>
        values.map((value, index) => point(index, value).join(",")).join(" ");

    const rings = [0.33, 0.66, 1]
        .map((value) => `
            <polygon
                points="${polygon(Array(count).fill(value))}"
                fill="none"
                style="stroke:var(--line)"
            />
        `)
        .join("");

    const axes = radarData
        .map((_, index) => {
            const [x, y] = point(index, 1);

            return `
                <line
                    x1="${center}"
                    y1="${center}"
                    x2="${x}"
                    y2="${y}"
                    style="stroke:var(--line)"
                />
            `;
        })
        .join("");

    const labels = radarData
        .map((item, index) => {
            const [x, y] = point(index, 1.22);

            return `
                <text
                    x="${x}"
                    y="${y + 4}"
                    text-anchor="middle"
                    font-size="12"
                    font-weight="600"
                    style="fill:var(--text)"
                >${item.label}</text>
            `;
        })
        .join("");

    container.innerHTML = `
        <div class="style-result-head">
            <strong>${escapeHtml(statusLabels[style.status] ?? style.status ?? "분석 결과")}</strong>

            <span class="style-result-meta">
                ${escapeHtml(playstyleAnalysis.main_position ?? "UNKNOWN")} ·
                ${Number(playstyleAnalysis.games_analyzed ?? style.games ?? 0)}경기
            </span>
        </div>

        ${styleRows}

        <div class="style-radar-chart">
            <svg viewBox="0 0 ${size} ${size}" role="img" aria-label="플레이스타일 육각형 그래프">
                ${rings}
                ${axes}

                <polygon
                    points="${polygon(Array(count).fill(0.5))}"
                    fill="none"
                    stroke-dasharray="4 3"
                    style="stroke:var(--muted)"
                />

                <polygon
                    points="${polygon(radarData.map((item) => item.value / 100))}"
                    stroke-width="2"
                    style="fill:color-mix(in srgb, var(--teal) 35%, transparent);stroke:var(--teal)"
                />

                ${labels}
            </svg>
        </div>
    `;
}

/* --- NEXT GAME: validated Databricks coaching only --- */
function renderTips(coaching) {
    if (!coaching) {
        $("#tips").innerHTML = `<li><div><strong>리포트 대기 중</strong><p>분석이 완료되면 검증된 개선점이 표시됩니다.</p></div></li>`;
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
    setHidden("#lookupError", true);
    setHidden("#lookupTitle", true);
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
        : `<div class="empty empty-guidance">
               <strong>최근 경기 분석을 시작해 보세요</strong>
               <span>소환사 이름과 태그를 입력하면 최근 10경기를 분석합니다.</span>
           </div>`;
}

$("#searchResult").addEventListener("click", (event) => {
    const chip = event.target.closest(".chip");
    if (!chip) return;

    const [gameName, tagLine = "KR1"] = chip.textContent.split("#");
    $("#lookupName").value = gameName;
    $("#lookupTag").value = tagLine;
    $("#lookupForm").requestSubmit();
});

/* ===================== 공식 정보 + 개인 경기 질문 ===================== */
const INFO_ROUTE_LABEL = {
    official_information: "공식 정보",
    personal_match: "개인 전적 분석",
    mixed: "공식 정보 + 개인 전적 분석",
};

const INFO_MODE_COPY = {
    official: {
        placeholder: "예) 아리의 스킬을 알려줘",
        empty: "공식 게임 정보에 대해 궁금한 내용을 입력해 주세요.",
        loading: "공식 문서에서 답변을 찾고 있습니다.",
    },
    personal: {
        placeholder: "예) 최근에 어떤 챔피언을 가장 많이 했어?",
        empty: "Riot ID와 질문을 입력하면 최근 솔로랭크 경기를 분석합니다.",
        loading: "최근 솔로랭크 경기와 개인 통계를 분석하고 있습니다.",
    },
};

let infoRequestPending = false;

function selectedInfoMode() {
    return document.querySelector('input[name="infoMode"]:checked')?.value ?? "official";
}

function setInfoMode(mode) {
    const personal = mode === "personal";
    setHidden("#infoPersonalFields", !personal);
    $("#infoQuestion").placeholder = INFO_MODE_COPY[mode].placeholder;
    $("#infoError").textContent = "";
    setHidden("#infoError", true);
    $("#infoLoading").textContent = "";
    setHidden("#infoLoading", true);
    $("#infoAnswer").innerHTML = `<p class="empty">${INFO_MODE_COPY[mode].empty}</p>`;
    (personal ? $("#infoGameName") : $("#infoQuestion")).focus();
}

document.querySelectorAll('input[name="infoMode"]').forEach((input) => {
    input.addEventListener("change", () => setInfoMode(input.value));
});

function personalStatisticsMarkup(result) {
    const evidence = result.statistics ?? {};
    const statistics = evidence.statistics ?? {};
    const matchCount = Number(statistics.match_count ?? evidence.match_count ?? 0);
    const champions = Array.isArray(evidence.most_played_champions)
        ? evidence.most_played_champions
        : [];
    const championRows = champions.length
        ? `<ul class="info-stat-list">${champions.slice(0, 5).map((champion) => `
               <li><strong>${escapeHtml(champion.champion_name ?? "알 수 없음")}</strong>
                   <span>${Number(champion.match_count ?? 0)}회</span></li>`).join("")}</ul>`
        : "";
    return `
        <dl class="info-personal-summary">
            <div><dt>분석 대상</dt><dd>${escapeHtml(
                `${result.subject?.game_name ?? ""}#${result.subject?.tag_line ?? ""}`,
            )}</dd></div>
            <div><dt>최근 경기</dt><dd>${matchCount}경기</dd></div>
        </dl>
        ${championRows}`;
}

function renderInfoAnswer(mode, question, result) {
    const answer = String(result.answer ?? "").trim() || "답변을 생성하지 못했습니다.";
    const citations = Array.isArray(result.citations) ? result.citations : [];
    const sources = citations.length
        ? `<section class="info-citations">
               <h3>출처</h3>
               <ul>${citations.map((citation) => {
                   const title = escapeHtml(citation.title || citation.document_id || "공식 문서");
                   return citation.source_url
                       ? `<li><a href="${escapeHtml(citation.source_url)}" target="_blank" rel="noopener">${title}</a></li>`
                       : `<li>${title}</li>`;
               }).join("")}</ul>
           </section>`
        : "";
    const route = INFO_ROUTE_LABEL[result.route] ?? result.route ?? "정보 검색";
    const status = result.status ? ` · ${result.status}` : "";
    const title = mode === "personal" ? "개인 경기 분석" : "공식 게임 정보";
    const statistics = mode === "personal" ? personalStatisticsMarkup(result) : "";

    $("#infoAnswer").innerHTML = `
        <h3 class="info-result-title">${title}</h3>
        <p class="info-question">${escapeHtml(question)}</p>
        ${statistics}
        <div class="info-response">${escapeHtml(answer).replace(/\n/g, "<br>")}</div>
        ${sources}
        <p class="info-meta">${escapeHtml(route + status)}</p>`;
}

$("#infoForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (infoRequestPending) return;

    const mode = selectedInfoMode();
    const gameName = $("#infoGameName").value.trim();
    const tagLine = $("#infoTagLine").value.trim().replace(/^#+/, "");
    const question = $("#infoQuestion").value.trim();
    const missingMessages = [];
    if (mode === "personal" && !gameName) missingMessages.push("소환사명을 입력해주세요.");
    if (mode === "personal" && !tagLine) missingMessages.push("태그를 입력해주세요.");
    if (!question) missingMessages.push("질문을 입력해주세요.");
    if (missingMessages.length) {
        $("#infoError").textContent = missingMessages.join(" ");
        setHidden("#infoError", false);
        return;
    }

    const button = $("#infoBtn");
    infoRequestPending = true;
    $("#infoError").textContent = "";
    setHidden("#infoError", true);
    $("#infoLoading").textContent = INFO_MODE_COPY[mode].loading;
    setHidden("#infoLoading", false);
    button.disabled = true;
    button.textContent = "검색 중...";
    $("#infoAnswer").innerHTML = `<p class="empty">${INFO_MODE_COPY[mode].loading}</p>`;

    try {
        const result = await window.APP_API.askRag({
            mode,
            question,
            gameName,
            tagLine,
            matchCount: 10,
        });
        renderInfoAnswer(mode, question, result);
    } catch (error) {
        $("#infoError").textContent = error.message;
        setHidden("#infoError", false);
        $("#infoAnswer").innerHTML = `<p class="empty">질문을 처리하지 못했습니다.</p>`;
    } finally {
        infoRequestPending = false;
        setHidden("#infoLoading", true);
        button.disabled = false;
        button.textContent = "검색";
    }
});

/* ===================== Databricks 코칭 리포트 ===================== */
function syncCoachingView() {
    const trend = $("#coachingTrend");
    const target = $("#coachingTarget");
    const answer = $("#coachingAnswer");
    const button = $("#coachingBtn");
    if (!trend || !target || !answer || !button) return;
    trend.innerHTML = $("#trend").innerHTML;
    target.textContent = $("#coachTarget").textContent;
    answer.innerHTML = $("#coachAnswer").innerHTML;
    button.disabled = $("#coachBtn").disabled;
    button.textContent = $("#coachBtn").textContent;
}
const coachingButton = $("#coachingBtn");
if (coachingButton) {
    coachingButton.addEventListener("click", () => {
        $("#coachForm").requestSubmit();
    });
}
$("#coachForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const { gameName = "", tagLine = "" } = window.APP_STATE.riotId ?? {};
    if (!gameName || !tagLine) return;
    const btn = $("#coachBtn");
    btn.disabled = true;
    btn.textContent = "생성 중...";
    const startedAt = Date.now();
    const updateWaitingMessage = () => {
        const elapsed = Math.floor((Date.now() - startedAt) / 1000);
        const stage = elapsed < 5
            ? "전적 데이터를 준비하고 있습니다."
            : elapsed < 20
                ? "Databricks 분석을 실행하고 있습니다."
                : "첫 실행은 Databricks 시작 때문에 시간이 더 걸릴 수 있습니다.";
        $("#coachAnswer").innerHTML = `<p class="empty">${stage} (${elapsed}초)</p>`;
        syncCoachingView();
    };
    updateWaitingMessage();
    const waitingTimer = window.setInterval(updateWaitingMessage, 1000);

    try {
        const result = await window.APP_API.getCoachingReport(gameName, tagLine, 10);
        applyCoachingReport(result, gameName, tagLine);
        syncCoachingView();
    } catch (error) {
        console.error("코칭 실패:", error);
        $("#coachAnswer").innerHTML = `<p class="empty">${escapeHtml(error.message)}</p>`;
        syncCoachingView();
    } finally {
        window.clearInterval(waitingTimer);
        btn.disabled = false;
        btn.textContent = "코칭 리포트 생성";
        syncCoachingView();
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
    renderPlayStyleReport(playstyle, priorityCandidates);
    renderTips(coaching);
    renderCoachReport(payload);

    const summary = window.APP_STATE.summary ?? {};
    window.APP_STATE.playStyle = playstyle ? {
        gameName,
        tagLine,
        // 분석 결과와 레이더 계산 기준을 한 시점의 스냅샷으로 보관한다.
        // 이후 응답 객체가 바뀌어도 저장 대상이 같이 변하지 않게 한다.
        playstyle: cloneForStorage(playstyle, null),
        priorityCandidates: cloneForStorage(priorityCandidates, []),
        winRate: Number(summary.win_rate ?? 0),
        matchCount: Number(payload.match_count ?? summary.games ?? 0),
    } : null;
    $("#styleSaveBtn").textContent = playstyle ? "저장하기" : "저장할 결과 없음";
    $("#styleSaveBtn").disabled = !playstyle;

    const rank = window.APP_STATE.rank;
    window.APP_STATE.savedReportDraft = coaching ? {
        gameName,
        tagLine,
        payload,
        winRate: Number(summary.win_rate ?? 0),
        tier: rank
            ? [rank.tier, rank.rank, `${rank.leaguePoints ?? 0} LP`].filter(Boolean).join(" ")
            : "UNRANKED",
    } : null;
    $("#reportSaveBtn").textContent = coaching ? "코칭 리포트 저장하기" : "저장할 리포트 없음";
    $("#reportSaveBtn").disabled = !coaching;
    syncMetricsSaveButton(gameName, tagLine);
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
    const container = $(target);
    if (!container) return;
    const report = payload.report ?? {};
    const coaching = report.coaching;
    if (!coaching) {
        const error = report.error?.message ?? "Databricks가 유효한 코칭 결과를 반환하지 않았습니다.";
        container.innerHTML = `<p class="empty">${escapeHtml(error)}</p>`;
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

    container.innerHTML = `
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

function cloneForStorage(value, fallback) {
    try {
        return JSON.parse(JSON.stringify(value));
    } catch {
        return fallback;
    }
}

function loadStyles() {
    try {
        const saved = JSON.parse(localStorage.getItem(STYLE_KEY));
        return Array.isArray(saved) ? saved : [];
    } catch {
        return [];
    }
}

function saveStyle(gameName, tagLine, playstyle, priorityCandidates, winRate, matchCount) {
    const id = `${gameName}#${tagLine}`;
    const entry = {
        id,
        gameName,
        tagLine,
        playstyle: cloneForStorage(playstyle, null),
        priorityCandidates: cloneForStorage(priorityCandidates, []),
        winRate: Number(winRate ?? 0),
        matchCount: Number(matchCount ?? 0),
        at: Date.now(),
        schemaVersion: 2,
    };
    const list = [entry, ...loadStyles().filter((x) => x.id !== id)].slice(0, STYLE_MAX);
    try { localStorage.setItem(STYLE_KEY, JSON.stringify(list)); } catch { /* 저장 불가 환경 무시 */ }
}

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
                <span>최근 ${Number(e.matchCount ?? 0)}경기 · 승률 ${Number(e.winRate ?? 0).toFixed(0)}%</span>
                <time>${new Date(e.at).toLocaleDateString("ko-KR")} 기준</time>
            </header>
            <div class="chart-box" id="vault-${escapeHtml(e.id)}"></div>
            <button type="button" class="style-refresh">다시 분석</button>
        </article>`).join("");

    // Databricks가 반환한 저장 결과만 표시한다 — 네트워크 호출 없음
    list.forEach((e) => {
        const target = `[id="vault-${CSS.escape(e.id)}"]`;
        if (e.playstyle) {
            renderPlayStyleReport(e.playstyle, e.priorityCandidates ?? [], target);
        } else {
            $(target).innerHTML = `<p class="empty">이전 프런트 계산 데이터입니다. 다시 분석해 주세요.</p>`;
        }
    });
}

$("#styleSaveBtn").addEventListener("click", () => {
    const current = window.APP_STATE.playStyle;
    if (!current) return;

    saveStyle(
        current.gameName,
        current.tagLine,
        current.playstyle,
        current.priorityCandidates,
        current.winRate,
        current.matchCount
    );
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
