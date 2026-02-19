// --- Chart setup ---
const ctx = document.getElementById("capitalChart").getContext("2d");
const capitalChart = new Chart(ctx, {
    type: "line",
    data: {
        labels: [],
        datasets: [{
            label: "Capital ($)",
            data: [],
            borderColor: "#10b981",
            backgroundColor: "rgba(16,185,129,0.1)",
            fill: true,
            tension: 0.3,
            pointRadius: 2,
        }],
    },
    options: {
        responsive: true,
        scales: {
            x: {
                ticks: { color: "#64748b", maxTicksLimit: 10 },
                grid:  { color: "#1e293b" },
            },
            y: {
                ticks: { color: "#64748b", callback: v => "$" + v },
                grid:  { color: "#1e293b" },
            },
        },
        plugins: { legend: { display: false } },
    },
});

// --- Helpers ---
const $id = (id) => document.getElementById(id);

function pnlColor(val) { return val >= 0 ? "text-emerald-400" : "text-red-400"; }
function pnlSign(val)  { return val >= 0 ? "+" : ""; }
function esc(str) {
    return String(str).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
}
function formatTime(iso) {
    if (!iso) return "-";
    return new Date(iso).toLocaleTimeString("es", { hour: "2-digit", minute: "2-digit" });
}

function scoreBadge(score) {
    if (score === 0) return `<span class="score-badge score-none">—</span>`;
    const cls = score >= 80 ? "score-high" : score >= 60 ? "score-mid" : "score-low";
    return `<span class="score-badge ${cls}">${score}</span>`;
}

function zoneColor(zone) {
    if (zone === "A") return "text-emerald-400 font-bold";
    if (zone === "B") return "text-yellow-400 font-bold";
    if (zone === "C") return "text-orange-400";
    return "text-gray-500";
}

function trajLabel(pts) {
    if (pts === 30) return '<span class="text-emerald-400">━ estable</span>';
    if (pts === 20) return '<span class="text-cyan-400">↗ gradual</span>';
    if (pts === 10) return '<span class="text-yellow-400">↑ rápida</span>';
    return '<span class="text-gray-600">↓</span>';
}

// --- Update UI ---
function updateUI(data) {
    // Status badge
    const running = data.bot_status === "running";
    const badge = $id("bot-status-badge");
    badge.className = "flex items-center gap-2 px-3 py-1 rounded-full text-sm font-medium " +
        (running ? "bg-emerald-900/50 text-emerald-300" : "bg-red-900/50 text-red-300");
    $id("status-dot").className = "w-2 h-2 rounded-full " + (running ? "bg-emerald-400 pulse-dot" : "bg-red-400");
    $id("status-text").textContent = running ? "Corriendo" : "Detenido";
    $id("btn-start").classList.toggle("hidden", running);
    $id("btn-stop").classList.toggle("hidden", !running);

    // Metrics
    $id("m-capital").textContent    = "$" + data.capital_total.toFixed(2);
    $id("m-disponible").textContent = "$" + data.capital_disponible.toFixed(2);

    const pnlEl = $id("m-pnl");
    pnlEl.textContent = pnlSign(data.pnl) + "$" + data.pnl.toFixed(2);
    pnlEl.className   = "text-xl font-bold mt-1 " + pnlColor(data.pnl);

    const roiEl = $id("m-roi");
    roiEl.textContent = pnlSign(data.roi) + data.roi.toFixed(2) + "%";
    roiEl.className   = "text-xl font-bold mt-1 " + pnlColor(data.roi);

    const wlParts = [data.won + " / " + data.lost];
    if (data.trail_stop) wlParts.push(data.trail_stop + " TS");
    if (data.hard_stop)  wlParts.push(data.hard_stop  + " HS");
    if (data.partial)    wlParts.push(data.partial     + " P");
    $id("m-wl").textContent = wlParts.join(" · ");

    $id("m-top-score").textContent = data.top_score || 0;
    $id("m-tracked").textContent   = data.tracked_markets || 0;
    $id("m-scans").textContent     = data.scan_count;

    // Price freshness
    lastPriceUpdateISO = data.last_price_update || null;
    priceThreadAlive   = data.price_thread_alive ?? true;
    updatePriceBadge();

    // Insights
    updateInsights(data.insights);

    // Chart
    const hist = data.capital_history || [];
    capitalChart.data.labels              = hist.map(h => formatTime(h.time));
    capitalChart.data.datasets[0].data   = hist.map(h => h.capital);
    capitalChart.update();

    // Open positions
    const openTb  = $id("table-open");
    const openPos = data.open_positions || [];
    if (openPos.length === 0) {
        openTb.innerHTML = "";
        $id("no-open").classList.remove("hidden");
    } else {
        $id("no-open").classList.add("hidden");
        openTb.innerHTML = openPos.map(p => {
            const partialTag = p.partial_done
                ? '<span class="text-xs text-cyan-400 ml-1">P50%✓</span>'
                : '';
            return `
            <tr class="border-b border-gray-800">
                <td class="q py-2 pr-3">${esc(p.question)}${partialTag}</td>
                <td class="num py-2 pr-3">${scoreBadge(p.score || 0)}</td>
                <td class="num py-2 pr-3">${(p.entry_no * 100).toFixed(1)}&cent;</td>
                <td class="num py-2 pr-3">${(p.current_no * 100).toFixed(1)}&cent;</td>
                <td class="num py-2 pr-3 text-orange-400 font-mono">${(p.trail_stop * 100).toFixed(1)}&cent;</td>
                <td class="num py-2 pr-3">$${p.allocated.toFixed(2)}</td>
                <td class="num py-2 ${pnlColor(p.pnl)}">${pnlSign(p.pnl)}$${p.pnl.toFixed(2)}</td>
            </tr>`;
        }).join("");
    }

    // Opportunities
    const oppsTb = $id("table-opps");
    const opps   = data.last_opportunities || [];
    if (opps.length === 0) {
        oppsTb.innerHTML = "";
        $id("no-opps").classList.remove("hidden");
    } else {
        $id("no-opps").classList.add("hidden");
        oppsTb.innerHTML = opps.map(o => {
            const inRange  = o.no_price >= 0.78 && o.no_price <= 0.93;
            const eligible = inRange && o.score_total >= 60;
            const rowClass = eligible
                ? "border-b border-emerald-900/40 bg-emerald-900/10"
                : inRange
                    ? "border-b border-yellow-900/20"
                    : "border-b border-gray-800";
            return `
            <tr class="${rowClass}">
                <td class="q py-2 pr-3">${esc(o.question)}</td>
                <td class="num py-2 pr-3">${scoreBadge(o.score_total)}</td>
                <td class="num py-2 pr-3 ${inRange ? 'text-emerald-300 font-semibold' : ''}">${(o.no_price*100).toFixed(1)}&cent;</td>
                <td class="num py-2 pr-3 ${zoneColor(o.score_zone)}">${o.score_zone || '-'}</td>
                <td class="num py-2">$${(o.volume||0).toLocaleString()}</td>
            </tr>`;
        }).join("");
    }

    // Top Scores table (rebuilt from last_opportunities — best we have without /api/scores per cycle)
    const scoresTb = $id("table-scores");
    const scorable = (data.last_opportunities || [])
        .filter(o => o.clob_ok && o.score_total > 0)
        .sort((a, b) => b.score_total - a.score_total)
        .slice(0, 10);
    if (scorable.length === 0) {
        scoresTb.innerHTML = "";
        $id("no-scores").classList.remove("hidden");
    } else {
        $id("no-scores").classList.add("hidden");
        scoresTb.innerHTML = scorable.map(o => `
            <tr class="border-b border-gray-800">
                <td class="num py-2 pr-3">${scoreBadge(o.score_total)}</td>
                <td class="num py-2 pr-3">${(o.no_price*100).toFixed(1)}&cent;</td>
                <td class="num py-2 pr-3 ${zoneColor(o.score_zone)}">${o.score_zone || '-'}</td>
                <td class="num py-2 pr-3 text-gray-400">+${o.score_zone !== '-' ? (o.score_zone === 'A' ? 30 : o.score_zone === 'B' ? 20 : 10) : 0}</td>
                <td class="num py-2 pr-3">${trajLabel(o.score_traj)}</td>
                <td class="num py-2 pr-3 text-gray-400">$${(o.volume||0).toLocaleString()}</td>
                <td class="num py-2 text-gray-500">${o.score_obs || 0} obs</td>
            </tr>
        `).join("");
    }

    // Closed trades
    const closedTb = $id("table-closed");
    const closed   = data.closed_positions || [];
    if (closed.length === 0) {
        closedTb.innerHTML = "";
        $id("no-closed").classList.remove("hidden");
    } else {
        $id("no-closed").classList.add("hidden");
        closedTb.innerHTML = closed.map(c => {
            const statusColor =
                c.status === "WON"        ? "text-emerald-400" :
                c.status === "PARTIAL"    ? "text-cyan-400"    :
                c.status === "TRAIL_STOP" ? "text-yellow-400"  :
                c.status === "HARD_STOP"  ? "text-orange-400"  :
                c.status === "LOST"       ? "text-red-400"     : "text-gray-400";
            return `
            <tr class="border-b border-gray-800">
                <td class="q py-2 pr-3">${esc(c.question)}</td>
                <td class="num py-2 pr-3">${scoreBadge(c.score || 0)}</td>
                <td class="num py-2 pr-3">${(c.entry_no * 100).toFixed(1)}&cent;</td>
                <td class="num py-2 pr-3">$${c.allocated.toFixed(2)}</td>
                <td class="num py-2 pr-3 ${pnlColor(c.pnl)}">${pnlSign(c.pnl)}$${c.pnl.toFixed(2)}</td>
                <td class="num py-2 pr-3 font-semibold ${statusColor}">${c.status}</td>
                <td class="res py-2 pr-3">${esc(c.resolution || "-")}</td>
                <td class="num py-2">${formatTime(c.close_time)}</td>
            </tr>`;
        }).join("");
    }
}

// --- Polling ---
async function fetchStatus() {
    try {
        const res = await fetch("/api/status");
        if (res.ok) updateUI(await res.json());
    } catch (e) {
        console.error("Fetch error:", e);
    }
}

function winRateBar(rate) {
    const pct   = (rate * 100).toFixed(0);
    const color = rate >= 0.7 ? "bg-emerald-500" : rate >= 0.5 ? "bg-yellow-500" : "bg-red-500";
    return `<div class="flex items-center gap-2">
        <div class="flex-1 bg-gray-700 rounded-full h-1.5">
            <div class="${color} h-1.5 rounded-full" style="width:${pct}%"></div>
        </div>
        <span class="text-xs text-gray-300 w-8 text-right">${pct}%</span>
    </div>`;
}

function updateInsights(ins) {
    const panel = $id("insights-panel");
    if (!ins) { panel.classList.add("hidden"); return; }
    panel.classList.remove("hidden");
    $id("insights-trades").textContent =
        `Win rate global: ${(ins.overall_win_rate * 100).toFixed(0)}%  (${ins.total_trades} trades)`;
    $id("insights-city").innerHTML = ins.by_city.map(c =>
        `<div class="mb-1"><div class="flex justify-between text-xs text-gray-400 mb-0.5">
            <span>${c.city}</span><span class="text-gray-500">${c.trades} trades</span>
        </div>${winRateBar(c.win_rate)}</div>`
    ).join("") || '<p class="text-gray-600 text-xs">Mínimo 2 trades por ciudad</p>';
    $id("insights-hour").innerHTML = ins.by_hour.map(h =>
        `<div class="mb-1"><div class="flex justify-between text-xs text-gray-400 mb-0.5">
            <span>${String(h.hour).padStart(2,"0")}:00 UTC</span><span class="text-gray-500">${h.trades} trades</span>
        </div>${winRateBar(h.win_rate)}</div>`
    ).join("") || '<p class="text-gray-600 text-xs">Mínimo 2 trades por hora</p>';
}

async function startBot() {
    await fetch("/api/bot/start", { method: "POST" });
    fetchStatus();
}
async function stopBot() {
    await fetch("/api/bot/stop", { method: "POST" });
    fetchStatus();
}

// --- Price freshness badge ---
let lastPriceUpdateISO = null;
let priceThreadAlive   = false;

function updatePriceBadge() {
    const dot   = $id("price-badge-dot");
    const txt   = $id("price-badge-txt");
    const badge = $id("price-badge");

    if (!lastPriceUpdateISO) {
        badge.className = "flex items-center gap-1.5 text-xs px-2 py-0.5 rounded-full bg-gray-700 text-gray-400";
        dot.className   = "w-1.5 h-1.5 rounded-full bg-gray-500";
        txt.textContent = "Precios: sin datos";
        return;
    }

    const secAgo = Math.round((Date.now() - new Date(lastPriceUpdateISO).getTime()) / 1000);
    txt.textContent = "Precios: hace " + secAgo + "s";

    if (!priceThreadAlive) {
        badge.className = "flex items-center gap-1.5 text-xs px-2 py-0.5 rounded-full bg-red-900/60 text-red-300";
        dot.className   = "w-1.5 h-1.5 rounded-full bg-red-400";
        txt.textContent = "Precios: hilo caído " + secAgo + "s";
    } else if (secAgo < 60) {
        badge.className = "flex items-center gap-1.5 text-xs px-2 py-0.5 rounded-full bg-emerald-900/60 text-emerald-300";
        dot.className   = "w-1.5 h-1.5 rounded-full bg-emerald-400 pulse-dot";
    } else if (secAgo < 120) {
        badge.className = "flex items-center gap-1.5 text-xs px-2 py-0.5 rounded-full bg-yellow-900/60 text-yellow-300";
        dot.className   = "w-1.5 h-1.5 rounded-full bg-yellow-400";
    } else {
        badge.className = "flex items-center gap-1.5 text-xs px-2 py-0.5 rounded-full bg-red-900/60 text-red-300";
        dot.className   = "w-1.5 h-1.5 rounded-full bg-red-400";
    }
}

setInterval(updatePriceBadge, 1000);
fetchStatus();
setInterval(fetchStatus, 5000);
