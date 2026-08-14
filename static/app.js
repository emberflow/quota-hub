const $ = (id) => document.getElementById(id);

document.querySelectorAll(".tab").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((b) => b.classList.toggle("active", b === btn));
    document.querySelectorAll(".panel").forEach((p) => {
      p.classList.toggle("active", p.id === "panel-" + btn.dataset.tab);
    });
    if (btn.dataset.tab === "github") loadRepos();
  });
});

$("refresh").addEventListener("click", loadQuota);

function barClass(left) {
  if (left == null) return "";
  if (left <= 15) return "bad";
  if (left <= 35) return "warn";
  return "ok";
}

function fmtPct(n) {
  if (n == null || Number.isNaN(n)) return "—";
  return Math.round(n) + "%";
}

function renderProvider(p) {
  const rows = (p.windows || [])
    .map((w) => {
      const left = w.percent_remaining;
      const usedWidth = left == null ? 0 : Math.max(0, Math.min(100, 100 - left));
      return `<div class="row">
        <div class="row-top">
          <span>${escapeHtml(w.label)}</span>
          <span>剩余 ${fmtPct(left)} · ${escapeHtml(w.remaining_label || "")}</span>
        </div>
        <div class="bar ${barClass(left)}"><i style="width:${usedWidth}%"></i></div>
        ${w.extra ? `<div class="muted">${escapeHtml(w.extra)}</div>` : ""}
      </div>`;
    })
    .join("");
  const err =
    p.status !== "fresh"
      ? `<div class="status">${escapeHtml(p.error || p.status)}
          ${p.remedy ? `<div>${escapeHtml(p.remedy)}</div>` : ""}
          ${p.officialUrl ? `<a href="${p.officialUrl}" target="_blank" rel="noreferrer">官方页面</a>` : ""}
        </div>`
      : `<div class="status">${escapeHtml(p.source || "")}
          ${p.officialUrl ? ` · <a href="${p.officialUrl}" target="_blank" rel="noreferrer">官方页面</a>` : ""}
        </div>`;
  return `<article class="card">
    <h3>${escapeHtml(p.label)}</h3>
    <div class="plan">${escapeHtml(p.plan || "")} · ${escapeHtml(p.status)}</div>
    ${rows || "<p class='muted'>暂无窗口数据</p>"}
    ${err}
  </article>`;
}

function renderUseFirst(items) {
  const box = $("use-first");
  if (!items || !items.length) {
    box.classList.add("hidden");
    box.innerHTML = "";
    return;
  }
  box.classList.remove("hidden");
  box.innerHTML =
    "<strong>即将作废 / 建议先花</strong><ul>" +
    items
      .map(
        (x) =>
          `<li>${escapeHtml(x.provider)} · ${escapeHtml(x.window)}：剩余 ${fmtPct(x.percentRemaining)}，${escapeHtml(x.remainingLabel)}</li>`
      )
      .join("") +
    "</ul>";
}

function renderModels(providers) {
  const host = $("models");
  const chunks = [];
  for (const p of providers) {
    if (!p.models || !p.models.length) continue;
    chunks.push(`<h3 style="margin:12px 0 6px">${escapeHtml(p.label)}</h3>
      <table><thead><tr><th>模型</th><th>输入 token</th><th>输出 token</th><th>约 $</th><th></th></tr></thead><tbody>
      ${p.models
        .map(
          (m) => `<tr>
            <td>${escapeHtml(m.name)}</td>
            <td>${m.tokens_in || 0}</td>
            <td>${m.tokens_out || 0}</td>
            <td>${m.cost_usd == null ? "—" : m.cost_usd.toFixed(2)}</td>
            <td class="muted">${escapeHtml(m.extra || "")}</td>
          </tr>`
        )
        .join("")}
      </tbody></table>`);
  }
  host.innerHTML = chunks.join("") || "<p class='muted'>这一周期还没有按模型明细（Antigravity 未登录时常见）。</p>";
}

function renderDaily(snapshots, logs) {
  const byDate = {};
  for (const row of snapshots || []) {
    if (row.usedPercent == null) continue;
    byDate[row.date] = (byDate[row.date] || 0) + row.usedPercent;
  }
  const dates = Object.keys(byDate).sort();
  const max = Math.max(1, ...dates.map((d) => byDate[d]));
  $("chart").innerHTML = dates
    .slice(-14)
    .map((d) => {
      const h = Math.round((byDate[d] / max) * 110);
      return `<div class="col"><b style="height:${h}px"></b><span>${d.slice(5)}</span></div>`;
    })
    .join("");

  const snapRows = (snapshots || [])
    .filter((r) => r.usedPercent != null)
    .slice(0, 40)
    .map(
      (r) =>
        `<tr><td>${r.date}</td><td>${r.provider}</td><td>${r.windowId}</td><td>${r.usedPercent}%</td><td class="muted">快照差值</td></tr>`
    );
  const logRows = (logs || []).slice(0, 40).map((r) => {
    const tok = (r.tokensIn || 0) + (r.tokensOut || 0);
    return `<tr><td>${r.date}</td><td>${r.provider}</td><td>${r.windowId}</td><td>${tok}</td><td class="muted">${r.source}</td></tr>`;
  });
  $("daily-table").innerHTML = `<table>
    <thead><tr><th>日期</th><th>产品</th><th>窗口 / 模型</th><th>用量</th><th>来源</th></tr></thead>
    <tbody>${snapRows.join("")}${logRows.join("") || ""}</tbody>
  </table>`;
}

async function loadQuota() {
  $("updated").textContent = "读取中…";
  try {
    const res = await fetch("/api/quota");
    const data = await res.json();
    $("cards").innerHTML = (data.providers || []).map(renderProvider).join("");
    renderUseFirst(data.useFirst);
    renderModels(data.providers || []);
    renderDaily(data.dailySnapshots, data.dailyLogs);
    $("updated").textContent = "更新于 " + new Date().toLocaleString();
  } catch (err) {
    $("updated").textContent = "刷新失败";
    $("cards").innerHTML = `<p class="muted">${escapeHtml(String(err))}</p>`;
  }
}

let reposLoaded = false;
async function loadRepos() {
  if (reposLoaded) return;
  $("repos").textContent = "读取仓库…";
  try {
    const res = await fetch("/api/github/repos");
    const data = await res.json();
    if (!data.ok) {
      $("repos").innerHTML = `<p class="muted">${escapeHtml(data.error || "失败")}<br>${escapeHtml(data.remedy || "")}</p>`;
      return;
    }
    reposLoaded = true;
    $("repos").innerHTML = (data.repos || [])
      .map((r) => {
        const name = r.nameWithOwner;
        const branch = (r.defaultBranchRef && r.defaultBranchRef.name) || "HEAD";
        return `<button type="button" data-repo="${escapeAttr(name)}" data-ref="${escapeAttr(branch)}">
          ${escapeHtml(name)}${r.isPrivate ? " · private" : ""}
        </button>`;
      })
      .join("");
    $("repos").querySelectorAll("button").forEach((btn) => {
      btn.addEventListener("click", () => {
        $("repos").querySelectorAll("button").forEach((b) => b.classList.toggle("active", b === btn));
        loadTree(btn.dataset.repo, btn.dataset.ref);
      });
    });
  } catch (err) {
    $("repos").textContent = String(err);
  }
}

async function loadTree(repo, ref) {
  $("tree-title").textContent = repo;
  $("tree-status").textContent = "加载目录…";
  $("tree").textContent = "";
  const qs = new URLSearchParams({ repo, ref: ref || "HEAD" });
  const res = await fetch("/api/github/tree?" + qs.toString());
  const data = await res.json();
  if (!data.ok) {
    $("tree-status").textContent = data.error || "失败";
    return;
  }
  $("tree-status").textContent = data.truncated ? "已截断（条目过多）" : `${data.entries.length} 项`;
  $("tree").innerHTML = (data.entries || [])
    .map((e) => {
      const cls = e.type === "tree" ? "dir" : "file";
      const mark = e.type === "tree" ? "/" : "";
      return `<div class="${cls}">${escapeHtml(e.path)}${mark}</div>`;
    })
    .join("");
}

function escapeHtml(s) {
  return String(s)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}
function escapeAttr(s) {
  return escapeHtml(s).replaceAll('"', "&quot;");
}

loadQuota();
