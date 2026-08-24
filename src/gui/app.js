// app.js — 前端逻辑 / frontend logic
let items = [];
let admin = false;
let apiReady = false;
let drives = ["C:"];
let currentDrive = "C:";
let largeFiles = [];
let duplicateGroups = [];
let detailMode = null;

function fmt(bytes) {
  if (bytes == null || isNaN(bytes)) return "—";
  if (bytes < 1024) return bytes + " B";
  const u = ["KB", "MB", "GB", "TB"];
  let i = -1, n = bytes;
  do { n /= 1024; i++; } while (n >= 1024 && i < u.length - 1);
  return n.toFixed(2) + " " + u[i];
}

function $(id) { return document.getElementById(id); }

function api() { return window.pywebview.api; }

function showView(name) {
  document.querySelectorAll(".view").forEach((el) => el.classList.add("hidden"));
  if (name === "dashboard") $("dashboard").classList.remove("hidden");
  if (name === "detail") $("detailView").classList.remove("hidden");
  if (name === "settings") $("settingsView").classList.remove("hidden");
  if (name === "placeholder") $("placeholderView").classList.remove("hidden");
}

function setNavActive(view) {
  document.querySelectorAll(".nav-item").forEach((el) => el.classList.remove("active"));
  const el = document.querySelector(`.nav-item[data-view="${view}"]`);
  if (el) el.classList.add("active");
}

// ---------------- 磁盘信息 / Disk info ----------------
async function loadDrives() {
  try {
    const r = await api().get_drives();
    if (r.ok && r.drives && r.drives.length) {
      drives = r.drives;
      renderDriveSelects();
    }
  } catch (e) { console.error(e); }
}

function renderDriveSelects() {
  const largeSel = $("largeDriveSelect");
  const dupSel = $("dupDriveSelect");
  const makeOption = (val, text) => {
    const opt = document.createElement("option");
    opt.value = val;
    opt.textContent = text;
    return opt;
  };
  [largeSel, dupSel].forEach((sel) => {
    sel.innerHTML = "";
    sel.appendChild(makeOption("all", "所有磁盘 / All"));
    drives.forEach((d) => sel.appendChild(makeOption(d, d + "\\")));
  });
  largeSel.value = currentDrive;
  dupSel.value = currentDrive;
}

async function loadDiskInfo(drive = "C:") {
  try {
    const r = await api().get_disk_info(drive);
    if (!r.ok) return;
    const info = r.info;
    $("diskTitle").textContent = `${info.drive}\\`;
    $("diskUsed").textContent = `已用 ${fmt(info.used)} / 共 ${fmt(info.total)}`;

    const bar = $("diskBar");
    bar.innerHTML = "";
    let acc = 0;
    info.categories.forEach((c) => {
      if (c.size <= 0) return;
      const pct = 100 * c.size / info.total;
      const seg = document.createElement("div");
      seg.className = "disk-bar-seg";
      seg.style.width = pct + "%";
      seg.style.background = c.color;
      bar.appendChild(seg);
      acc += c.size;
    });

    const legend = $("diskLegend");
    legend.innerHTML = "";
    info.categories.forEach((c) => {
      if (c.size <= 0) return;
      const item = document.createElement("div");
      item.className = "legend-item";
      item.innerHTML = `<span class="legend-dot" style="background:${c.color}"></span>${c.name} <span class="en">${c.name_en}</span> · ${fmt(c.size)}`;
      legend.appendChild(item);
    });
  } catch (e) { console.error(e); }
}

async function updateAdminChip() {
  try {
    const a = await api().is_admin();
    const chip = $("adminChip");
    if (a) {
      chip.textContent = "管理员权限 / Admin";
      chip.className = "admin-chip ok";
    } else {
      chip.textContent = "非管理员 / Not admin";
      chip.className = "admin-chip warn";
    }
  } catch (e) {}
}

// ---------------- 导航 / Navigation ----------------
function initNav() {
  document.querySelectorAll(".nav-item").forEach((el) => {
    el.addEventListener("click", () => {
      const view = el.dataset.view;
      setNavActive(view);
      if (view === "storage" || view === "home") {
        showView("dashboard");
      } else if (view === "settings") {
        showView("settings");
      } else {
        showView("placeholder");
      }
    });
  });
  $("placeholderBack").addEventListener("click", () => {
    setNavActive("storage");
    showView("dashboard");
  });
  $("backBtn").addEventListener("click", () => {
    detailMode = null;
    showView("dashboard");
  });
  $("analyzeBtn").addEventListener("click", () => openDetail("deep"));
  document.querySelectorAll(".action-card").forEach((card) => {
    card.addEventListener("click", () => openDetail(card.dataset.detail));
  });
  $("boostBtn").addEventListener("click", doBoost);
  $("boostDoneBtn").addEventListener("click", resetBoost);
}

function openDetail(mode) {
  detailMode = mode;
  showView("detail");
  const titleMap = {
    deep: "深度清理 / Deep Clean",
    downloads: "下载文件 / Downloads",
    large: "大文件 / Large Files",
    duplicate: "重复文件 / Duplicate Files",
    checkup: "全面体检 / System Checkup",
    processes: "进程管理 / Process Manager",
    startup: "开机管理 / Startup Manager",
  };
  $("detailTitle").textContent = titleMap[mode] || "详情 / Detail";
  const content = $("detailContent");
  content.innerHTML = "";

  if (mode === "deep") renderDeepClean(content);
  else if (mode === "large") renderLargeFiles(content);
  else if (mode === "duplicate") renderDuplicateFiles(content);
  else if (mode === "downloads") renderDownloads(content);
  else if (mode === "checkup") renderCheckup(content);
  else if (mode === "processes") renderProcesses(content);
  else if (mode === "startup") renderStartup(content);
}

// ---------------- 深度清理 / Deep clean ----------------
function flattenItems(list) {
  const out = [];
  for (const it of list) {
    out.push(it);
    if (it.children) out.push(...it.children);
  }
  return out;
}

function renderDeepClean(container) {
  container.innerHTML = `
    <div class="toolbar">
      <label class="selectall"><input type="checkbox" id="selectAll" checked /> 全选 / Select all</label>
      <span id="totalLabel" class="total">可选清理总计 / Total: —</span>
      <button id="cleanBtn" class="btn danger" disabled>清理所选 / Clean selected</button>
    </div>
    <div id="deepList" class="list"><div class="empty">点击扫描开始。<br><span class="en">Click scan to start.</span></div></div>
  `;
  $("selectAll").addEventListener("change", onSelectAll);
  $("cleanBtn").addEventListener("click", openConfirm);
  doScan();
}

async function doScan() {
  const list = $("deepList");
  if (!list) return;
  list.innerHTML = '<div class="empty">正在扫描，请稍候…<br><span class="en">Scanning, please wait…</span></div>';
  try {
    const r = await api().scan();
    if (!r.ok) {
      list.innerHTML = '<div class="empty err">扫描失败 / Scan failed: ' + r.error + "</div>";
      return;
    }
    items = r.items;
    admin = r.admin;
    renderList();
    $("deepSub").textContent = `发现 ${items.length} 类可清理项 / ${items.length} categories found`;
  } catch (e) {
    list.innerHTML = '<div class="empty err">错误 / Error: ' + e + "</div>";
  }
}

function renderList() {
  const list = $("deepList");
  if (!list) return;
  list.innerHTML = "";
  if (!items.length) {
    list.innerHTML = '<div class="empty">未发现可清理项 / No cleanable items found.</div>';
    return;
  }
  items.forEach((it) => {
    if (it.children && it.children.length) {
      list.appendChild(renderAppCard(it));
    } else {
      list.appendChild(renderPlainCard(it));
    }
  });
  bindCheckboxes();
  updateTotal();
}

function renderPlainCard(it) {
  const card = document.createElement("div");
  card.className = "card" + (it.requires_admin ? " need-admin" : "");
  card.innerHTML = `
    <label class="card-main">
      <input type="checkbox" class="item-cb" data-id="${it.id}" ${it.size > 0 ? "checked" : ""} />
      <div class="card-text">
        <div class="title">${it.name} <span class="en">${it.name_en}</span></div>
        <div class="note">${it.note} <span class="en">${it.note_en}</span></div>
      </div>
      <div class="size">${fmt(it.size)}</div>
    </label>
    ${it.requires_admin ? '<div class="tag">需管理员 / needs admin</div>' : ""}
  `;
  return card;
}

function renderAppCard(parent) {
  const card = document.createElement("div");
  card.className = "card app-card";
  card.dataset.id = parent.id;
  const checked = parent.children.every((c) => c.size > 0) ? "checked" : "";
  card.innerHTML = `
    <div class="card-main app-main">
      <input type="checkbox" class="item-cb parent-cb" data-id="${parent.id}" ${checked} />
      <div class="app-toggle">
        <span class="arrow">▸</span>
        <div class="card-text">
          <div class="title">${parent.name} <span class="en">${parent.name_en}</span></div>
          <div class="note">${parent.note} <span class="en">${parent.note_en}</span></div>
        </div>
      </div>
      <div class="size">${fmt(parent.size)}</div>
    </div>
    <div class="children hidden">
      ${parent.children.map((c) => `
        <label class="child-row">
          <input type="checkbox" class="item-cb child-cb" data-id="${c.id}" data-parent="${parent.id}" ${c.size > 0 ? "checked" : ""} />
          <div class="child-text">
            <div class="child-name">${c.name} <span class="en">${c.name_en}</span></div>
            <div class="child-note">${c.note} <span class="en">${c.note_en}</span></div>
          </div>
          <div class="child-size">${fmt(c.size)}</div>
        </label>
      `).join("")}
    </div>
  `;
  const toggle = card.querySelector(".app-toggle");
  const children = card.querySelector(".children");
  const arrow = card.querySelector(".arrow");
  toggle.addEventListener("click", (e) => {
    if (e.target.tagName === "INPUT") return;
    children.classList.toggle("hidden");
    arrow.textContent = children.classList.contains("hidden") ? "▸" : "▾";
  });
  return card;
}

function bindCheckboxes() {
  document.querySelectorAll(".item-cb").forEach((cb) => {
    cb.removeEventListener("change", onCbChange);
    cb.addEventListener("change", onCbChange);
  });
}

function onSelectAll() {
  const v = $("selectAll").checked;
  document.querySelectorAll(".item-cb").forEach((cb) => { cb.checked = v; });
  updateTotal();
}

function onCbChange(e) {
  const cb = e.target;
  if (cb.classList.contains("parent-cb")) {
    const pid = cb.dataset.id;
    document.querySelectorAll(`.child-cb[data-parent="${pid}"]`).forEach((c) => { c.checked = cb.checked; });
  } else if (cb.classList.contains("child-cb")) {
    const pid = cb.dataset.parent;
    const children = Array.from(document.querySelectorAll(`.child-cb[data-parent="${pid}"]`));
    const parent = document.querySelector(`.parent-cb[data-id="${pid}"]`);
    if (parent) parent.checked = children.every((c) => c.checked);
  }
  updateTotal();
}

function selectedIds() {
  return Array.from(document.querySelectorAll(".item-cb:checked")).map((cb) => cb.dataset.id);
}

function selectedItems() {
  const ids = selectedIds();
  const flat = flattenItems(items);
  return flat.filter((it) => ids.includes(it.id));
}

function updateTotal() {
  const sel = selectedItems();
  const sum = sel.reduce((a, b) => a + (b.size || 0), 0);
  $("totalLabel").textContent = "可选清理总计 / Total: " + fmt(sum);
  $("cleanBtn").disabled = sel.length === 0 || !apiReady;
}

function openConfirm() {
  const sel = selectedItems();
  if (!sel.length) return;
  const sum = sel.reduce((a, b) => a + (b.size || 0), 0);
  const ul = $("confirmList");
  ul.innerHTML = "";
  sel.forEach((it) => {
    const li = document.createElement("li");
    li.innerHTML = `<strong>${it.name}</strong> <span class="en">${it.name_en}</span> — ${fmt(it.size)}`;
    ul.appendChild(li);
  });
  $("confirmTotal").textContent = fmt(sum);
  $("confirmModal").classList.remove("hidden");
}

function closeConfirm() { $("confirmModal").classList.add("hidden"); }

async function doClean() {
  const ids = selectedIds();
  if (!ids.length) return;
  closeConfirm();
  const btn = $("cleanBtn");
  if (btn) btn.disabled = true;
  const content = $("detailContent");
  content.insertAdjacentHTML("beforeend", '<div id="cleanResult" class="result" style="margin-top:14px;"><div class="empty">清理中…<br><span class="en">Cleaning…</span></div></div>');
  try {
    const r = await api().clean(ids);
    const box = $("cleanResult");
    if (!r.ok) {
      box.innerHTML = '<div class="empty err">清理失败 / Cleanup failed: ' + r.error + "</div>";
      return;
    }
    let html = `<h3>清理完成 / Done</h3><p>共释放 / Freed: <strong>${fmt(r.total_freed)}</strong></p><ul class="result-list">`;
    const flat = flattenItems(items);
    r.results.forEach((res) => {
      const it = flat.find((x) => x.id === res.id);
      const name = res.name || (it ? it.name : res.id);
      html += `<li><strong>${name}</strong>: ${fmt(res.freed)}`;
      if (res.errors && res.errors.length) {
        html += `<div class="err">⚠ ${res.errors.join("; ")}</div>`;
      }
      html += "</li>";
    });
    html += "</ul>";
    box.innerHTML = html;
  } catch (e) {
    $("cleanResult").innerHTML = '<div class="empty err">错误 / Error: ' + e + "</div>";
  } finally {
    doScan();
    updateMemory();
  }
}

// ---------------- 大文件 / Large files ----------------
async function scanLargePreview() {
  const sub = $("largeSub");
  sub.innerHTML = "扫描中… / Scanning…";
  try {
    const r = await api().scan_large_files(1.0, currentDrive);
    if (!r.ok) { sub.innerHTML = "扫描失败 / failed"; return; }
    const total = (r.files || []).reduce((a, f) => a + f.size, 0);
    sub.innerHTML = `超过 <strong>${fmt(total)}</strong> 大文件可清理`;
  } catch (e) { sub.innerHTML = "扫描失败 / failed"; }
}

function renderLargeFiles(container) {
  container.innerHTML = `
    <div class="toolbar">
      <div class="threshold">
        <label for="lfThreshold">大于 / larger than</label>
        <input id="lfThreshold" type="number" min="0.1" step="0.1" value="1" style="width:64px;padding:6px;border:1px solid var(--border);border-radius:6px;" />
        <span>GB</span>
      </div>
      <span id="lfCount" class="total"></span>
      <button id="lfScanBtn" class="btn primary">扫描 / Scan</button>
    </div>
    <div id="lfList" class="file-table"><div class="empty">设置阈值后点击扫描。<br><span class="en">Set threshold and click scan.</span></div></div>
    <div class="actions-right"><button id="lfDeleteBtn" class="btn danger" disabled>删除选中 / Delete selected</button></div>
    <div id="lfResult"></div>
  `;
  $("lfScanBtn").addEventListener("click", doScanLarge);
  $("lfDeleteBtn").addEventListener("click", doDeleteLarge);
}

async function doScanLarge() {
  const val = parseFloat($("lfThreshold").value);
  const threshold = isNaN(val) || val <= 0 ? 1 : val;
  $("lfScanBtn").disabled = true;
  $("lfScanBtn").textContent = "扫描中… / Scanning…";
  $("lfList").innerHTML = '<div class="empty">正在扫描大文件，请稍候…<br><span class="en">Scanning large files…</span></div>';
  $("lfDeleteBtn").disabled = true;
  try {
    const r = await api().scan_large_files(threshold, currentDrive);
    $("lfScanBtn").disabled = false;
    $("lfScanBtn").textContent = "扫描 / Scan";
    if (!r.ok) { $("lfList").innerHTML = '<div class="empty err">扫描失败 / Scan failed: ' + r.error + "</div>"; return; }
    largeFiles = r.files || [];
    renderLargeFileList();
  } catch (e) {
    $("lfScanBtn").disabled = false;
    $("lfScanBtn").textContent = "扫描 / Scan";
    $("lfList").innerHTML = '<div class="empty err">错误 / Error: ' + e + "</div>";
  }
}

function renderLargeFileList() {
  const container = $("lfList");
  const total = largeFiles.reduce((a, f) => a + f.size, 0);
  $("lfCount").textContent = `共 ${largeFiles.length} 个 / ${largeFiles.length} files · ${fmt(total)}`;
  if (!largeFiles.length) {
    container.innerHTML = '<div class="empty">未找到大于阈值的大文件。<br><span class="en">No large files found above threshold.</span></div>';
    $("lfDeleteBtn").disabled = true;
    return;
  }
  container.innerHTML = "";
  largeFiles.forEach((f, idx) => {
    const row = document.createElement("label");
    row.className = "file-row";
    row.innerHTML = `
      <input type="checkbox" class="lf-cb" data-idx="${idx}" />
      <div class="file-info"><div class="file-name" title="${f.path}">${f.name}</div><div class="file-path">${f.path}</div></div>
      <div class="file-size">${fmt(f.size)}</div>
    `;
    container.appendChild(row);
  });
  container.querySelectorAll(".lf-cb").forEach((cb) => cb.addEventListener("change", updateLargeActions));
  updateLargeActions();
}

function updateLargeActions() {
  const any = document.querySelectorAll(".lf-cb:checked").length > 0;
  $("lfDeleteBtn").disabled = !any;
}

async function doDeleteLarge() {
  const idxs = Array.from(document.querySelectorAll(".lf-cb:checked")).map((cb) => parseInt(cb.dataset.idx));
  const paths = idxs.map((i) => largeFiles[i].path);
  if (!paths.length) return;
  if (!confirm(`确认删除选中的 ${paths.length} 个大文件？文件将进入回收站可恢复。\nDelete ${paths.length} selected large files? They go to Recycle Bin.`)) return;
  $("lfDeleteBtn").disabled = true;
  $("lfResult").innerHTML = '<div class="result" style="margin-top:12px;"><div class="empty">删除中…<br><span class="en">Deleting…</span></div></div>';
  try {
    const r = await api().delete_large_files(paths);
    if (!r.ok) { $("lfResult").innerHTML = '<div class="result" style="margin-top:12px;"><div class="empty err">删除失败 / Delete failed: ' + r.error + "</div></div>"; return; }
    let html = `<h3>大文件删除完成 / Large files deleted</h3><p>释放 / Freed: <strong>${fmt(r.total_freed)}</strong></p><ul class="result-list">`;
    r.results.forEach((res) => {
      html += `<li>${res.path}<br><span class="en">${fmt(res.size)} — ${res.ok ? "ok" : res.msg}</span></li>`;
    });
    html += "</ul>";
    $("lfResult").innerHTML = '<div class="result" style="margin-top:12px;">' + html + "</div>";
    doScanLarge();
  } catch (e) {
    $("lfResult").innerHTML = '<div class="result" style="margin-top:12px;"><div class="empty err">错误 / Error: ' + e + "</div></div>";
  } finally {
    $("lfDeleteBtn").disabled = false;
  }
}

// ---------------- 重复文件 / Duplicate files ----------------
async function scanDupPreview() {
  const sub = $("dupSub");
  sub.innerHTML = "扫描中… / Scanning…";
  try {
    const r = await api().scan_duplicate_files(currentDrive);
    if (!r.ok) { sub.innerHTML = "扫描失败 / failed"; return; }
    const groups = r.groups || [];
    const total = groups.reduce((a, g) => a + (g.total_size - g.size), 0);
    sub.innerHTML = `超过 <strong>${fmt(total)}</strong> 重复文件可清理`;
  } catch (e) { sub.innerHTML = "扫描失败 / failed"; }
}

function renderDuplicateFiles(container) {
  container.innerHTML = `
    <div class="toolbar">
      <span class="total">每组保留 1 份，其余删除到回收站 / Keep 1 per group</span>
      <button id="dupScanBtn" class="btn primary">扫描 / Scan</button>
    </div>
    <div id="dupList" class="file-table"><div class="empty">点击扫描查找重复文件。<br><span class="en">Click scan to find duplicates.</span></div></div>
    <div class="actions-right"><button id="dupDeleteBtn" class="btn danger" disabled>删除重复项 / Delete duplicates</button></div>
    <div id="dupResult"></div>
  `;
  $("dupScanBtn").addEventListener("click", doScanDup);
  $("dupDeleteBtn").addEventListener("click", doDeleteDup);
}

async function doScanDup() {
  $("dupScanBtn").disabled = true;
  $("dupScanBtn").textContent = "扫描中… / Scanning…";
  $("dupList").innerHTML = '<div class="empty">正在扫描重复文件，请稍候…<br><span class="en">Scanning duplicate files…</span></div>';
  $("dupDeleteBtn").disabled = true;
  try {
    const r = await api().scan_duplicate_files(currentDrive);
    $("dupScanBtn").disabled = false;
    $("dupScanBtn").textContent = "扫描 / Scan";
    if (!r.ok) { $("dupList").innerHTML = '<div class="empty err">扫描失败 / Scan failed: ' + r.error + "</div>"; return; }
    duplicateGroups = r.groups || [];
    renderDupList();
  } catch (e) {
    $("dupScanBtn").disabled = false;
    $("dupScanBtn").textContent = "扫描 / Scan";
    $("dupList").innerHTML = '<div class="empty err">错误 / Error: ' + e + "</div>";
  }
}

function renderDupList() {
  const container = $("dupList");
  if (!duplicateGroups.length) {
    container.innerHTML = '<div class="empty">未找到重复文件。<br><span class="en">No duplicate files found.</span></div>';
    $("dupDeleteBtn").disabled = true;
    return;
  }
  container.innerHTML = "";
  duplicateGroups.forEach((g, gi) => {
    const div = document.createElement("div");
    div.className = "dup-group";
    const select = document.createElement("select");
    select.dataset.gi = gi;
    g.files.forEach((f, fi) => {
      const opt = document.createElement("option");
      opt.value = f.path;
      opt.textContent = `${f.name} (${fmt(g.size)})`;
      if (fi === 0) opt.selected = true;
      select.appendChild(opt);
    });
    const header = document.createElement("div");
    header.className = "dup-group-header";
    header.innerHTML = `<span>保留 / Keep:</span>`;
    header.appendChild(select);
    header.insertAdjacentHTML("beforeend", `<span style="margin-left:auto;color:var(--muted)">可释放 / Free: ${fmt(g.total_size - g.size)}</span>`);
    div.appendChild(header);
    g.files.forEach((f) => {
      div.insertAdjacentHTML("beforeend", `<div class="dup-row">${f.path}</div>`);
    });
    container.appendChild(div);
  });
  $("dupDeleteBtn").disabled = false;
}

async function doDeleteDup() {
  if (!duplicateGroups.length) return;
  if (!confirm("确认删除重复文件？每组保留你选择的 1 份，其余进入回收站可恢复。\nDelete duplicates? Keep 1 per group; the rest go to Recycle Bin.")) return;
  $("dupDeleteBtn").disabled = true;
  $("dupResult").innerHTML = '<div class="result" style="margin-top:12px;"><div class="empty">删除中…<br><span class="en">Deleting…</span></div></div>';
  try {
    let totalFreed = 0;
    let totalResults = [];
    for (let gi = 0; gi < duplicateGroups.length; gi++) {
      const g = duplicateGroups[gi];
      const select = document.querySelector(`select[data-gi="${gi}"]`);
      const keep = select ? select.value : g.files[0].path;
      const paths = g.files.map((f) => f.path);
      const r = await api().delete_duplicate_files(keep, paths);
      if (r.ok) {
        totalFreed += r.total_freed;
        totalResults.push(...r.results);
      }
    }
    let html = `<h3>重复文件删除完成 / Duplicates deleted</h3><p>释放 / Freed: <strong>${fmt(totalFreed)}</strong></p><ul class="result-list">`;
    totalResults.forEach((res) => {
      html += `<li>${res.path}<br><span class="en">${fmt(res.size)} — ${res.ok ? "ok" : res.msg}</span></li>`;
    });
    html += "</ul>";
    $("dupResult").innerHTML = '<div class="result" style="margin-top:12px;">' + html + "</div>";
    doScanDup();
  } catch (e) {
    $("dupResult").innerHTML = '<div class="result" style="margin-top:12px;"><div class="empty err">错误 / Error: ' + e + "</div></div>";
  } finally {
    $("dupDeleteBtn").disabled = false;
  }
}

// ---------------- 下载文件（占位） ----------------
function renderDownloads(container) {
  container.innerHTML = `
    <div class="empty">
      下载文件清理即将上线。<br>
      <span class="en">Downloads cleanup is coming soon.</span>
    </div>
  `;
}

// ---------------- 快速加速 / Quick boost ----------------
async function updateMemory() {
  try {
    const r = await api().get_memory_info();
    if (!r.ok) return;
    const m = r.memory;
    $("memBar").style.width = m.percent + "%";
    $("memText").textContent = `${m.percent}% (${fmt(m.used)} / ${fmt(m.total)})`;
  } catch (e) {
    $("memText").textContent = "—";
  }
}

function resetBoost() {
  $("boostDoneBtn").classList.add("hidden");
  $("boostBtn").classList.remove("hidden");
  $("boostResult").classList.add("hidden");
  $("boostResult").textContent = "";
  updateMemory();
}

async function doBoost() {
  // 1. 显示动画
  $("boostBtn").classList.add("hidden");
  $("boostDoneBtn").classList.add("hidden");
  $("boostResult").classList.add("hidden");
  const anim = $("boostAnim");
  anim.classList.remove("hidden");
  const circle = $("boostCircleFg");
  const percent = $("boostPercent");

  const duration = 2200;
  const start = performance.now();
  const circumference = 2 * Math.PI * 42; // ~264
  circle.style.strokeDasharray = circumference;
  circle.style.strokeDashoffset = circumference;

  return new Promise((resolve) => {
    function frame(now) {
      const elapsed = now - start;
      const p = Math.min(1, elapsed / duration);
      const pct = Math.round(p * 100);
      circle.style.strokeDashoffset = circumference * (1 - p);
      percent.textContent = pct + "%";
      if (p < 1) {
        requestAnimationFrame(frame);
      } else {
        resolve();
      }
    }
    requestAnimationFrame(frame);
  }).then(async () => {
    // 2. 动画结束，调用实际加速
    try {
      const r = await api().quick_boost();
      anim.classList.add("hidden");
      if (!r.ok) {
        $("boostResult").textContent = "加速失败 / Boost failed: " + r.error;
        $("boostResult").classList.remove("hidden");
        $("boostBtn").classList.remove("hidden");
        return;
      }
      // 3. 显示加速完成按钮 + 结果
      $("boostDoneBtn").classList.remove("hidden");
      $("boostResult").innerHTML = `释放 ${fmt(r.freed)} · 内存 ${r.memory.percent}%`;
      $("boostResult").classList.remove("hidden");
      updateMemory();
      loadDiskInfo(currentDrive);
    } catch (e) {
      anim.classList.add("hidden");
      $("boostResult").textContent = "错误 / Error: " + e;
      $("boostResult").classList.remove("hidden");
      $("boostBtn").classList.remove("hidden");
    }
  });
}

// ---------------- 首页预览 / Home previews ----------------
async function loadHomePreviews() {
  // 深度清理可清理大小
  try {
    const r = await api().scan();
    if (r.ok && r.items) {
      const flat = flattenItems(r.items);
      const total = flat.reduce((a, b) => a + (b.size || 0), 0);
      $("deepSize").textContent = fmt(total);
      // 也更新应用子项全局变量
      items = r.items;
      admin = r.admin;
    }
  } catch (e) { console.error(e); }

  // 大文件预览
  try {
    const r = await api().scan_large_files(1.0, currentDrive);
    if (r.ok && r.files) {
      const total = r.files.reduce((a, f) => a + f.size, 0);
      if (r.files.length) {
        $("largeSub").innerHTML = `发现 <strong>${r.files.length}</strong> 个大文件 · 共 <strong>${fmt(total)}</strong>`;
      } else {
        $("largeSub").textContent = "未发现大于 1 GB 的文件";
      }
    }
  } catch (e) { console.error(e); }

  // 重复文件预览
  try {
    const r = await api().scan_duplicate_files(currentDrive);
    if (r.ok && r.groups) {
      const total = r.groups.reduce((a, g) => a + (g.total_size - g.size), 0);
      if (r.groups.length) {
        $("dupSub").innerHTML = `发现 <strong>${r.groups.length}</strong> 组重复文件 · 可释放 <strong>${fmt(total)}</strong>`;
      } else {
        $("dupSub").textContent = "未发现重复文件";
      }
    }
  } catch (e) { console.error(e); }
}

// ---------------- 全面体检 / System checkup ----------------
function renderCheckup(container) {
  container.innerHTML = `
    <div class="toolbar">
      <span class="total" id="checkupScore" style="font-size:16px;font-weight:700;color:var(--accent-dark)">评分: —</span>
      <button id="checkupBtn" class="btn primary">立即体检 / Check now</button>
    </div>
    <div id="checkupList" class="list"><div class="empty">点击"立即体检"开始全面检查。<br><span class="en">Click "Check now" to run a full system check.</span></div></div>
  `;
  $("checkupBtn").addEventListener("click", doCheckup);
}

async function doCheckup() {
  const btn = $("checkupBtn");
  if (btn) { btn.disabled = true; btn.textContent = "体检中… / Checking…"; }
  const list = $("checkupList");
  list.innerHTML = '<div class="empty">正在全面体检，请稍候…<br><span class="en">Running system checkup…</span></div>';
  try {
    const r = await api().system_check();
    if (btn) { btn.disabled = false; btn.textContent = "重新体检 / Check again"; }
    if (!r.ok) {
      list.innerHTML = '<div class="empty err">体检失败 / Check failed: ' + r.error + "</div>";
      return;
    }
    $("checkupScore").textContent = `健康评分 / Score: ${r.score}`;
    $("checkupScore").style.color = r.score >= 80 ? "var(--ok)" : (r.score >= 60 ? "var(--warn)" : "var(--danger)");
    localStorage.setItem("cdcleaner_last_check", new Date().toLocaleDateString("zh-CN"));
    $("checkupDate").textContent = localStorage.getItem("cdcleaner_last_check");
    list.innerHTML = "";
    r.issues.forEach((it) => {
      const row = document.createElement("div");
      row.className = "card" + (it.bad ? " need-admin" : "");
      row.innerHTML = `
        <div class="card-main">
          <div class="card-text">
            <div class="title">${it.name} <span class="en">${it.name_en}</span></div>
            <div class="note">${it.tip} <span class="en">${it.tip_en}</span></div>
          </div>
          <div class="size" style="color:${it.bad ? 'var(--danger)' : 'var(--ok)'}">${it.bad ? "需优化" : "正常"}</div>
        </div>
      `;
      list.appendChild(row);
    });
  } catch (e) {
    if (btn) { btn.disabled = false; btn.textContent = "立即体检 / Check now"; }
    list.innerHTML = '<div class="empty err">错误 / Error: ' + e + "</div>";
  }
}

// ---------------- 进程管理 / Process manager ----------------
function renderProcesses(container) {
  container.innerHTML = `
    <div class="toolbar">
      <span class="total" id="procTotal">进程数 / Processes: —</span>
      <button id="procRefreshBtn" class="btn primary">刷新 / Refresh</button>
    </div>
    <div id="procList" class="file-table"><div class="empty">点击刷新获取进程列表。<br><span class="en">Click refresh to load processes.</span></div></div>
  `;
  $("procRefreshBtn").addEventListener("click", loadProcesses);
  loadProcesses();
}

async function loadProcesses() {
  const list = $("procList");
  list.innerHTML = '<div class="empty">正在获取进程列表…<br><span class="en">Loading processes…</span></div>';
  try {
    const r = await api().list_processes();
    if (!r.ok) { list.innerHTML = '<div class="empty err">获取失败 / Failed: ' + r.error + "</div>"; return; }
    $("procTotal").textContent = `进程数 / Processes: ${r.count}`;
    if (!r.processes.length) {
      list.innerHTML = '<div class="empty">未找到非系统进程。<br><span class="en">No non-system processes found.</span></div>';
      return;
    }
    list.innerHTML = "";
    r.processes.forEach((p) => {
      const row = document.createElement("div");
      row.className = "file-row";
      row.innerHTML = `
        <div class="file-info"><div class="file-name">${p.name}</div><div class="file-path">PID ${p.pid}</div></div>
        <div class="file-size">${fmt(p.memory)}</div>
        <button class="btn danger" data-pid="${p.pid}">结束 / End</button>
      `;
      row.querySelector("button").addEventListener("click", (e) => {
        e.stopPropagation();
        killProc(p.pid, p.name);
      });
      list.appendChild(row);
    });
  } catch (e) {
    list.innerHTML = '<div class="empty err">错误 / Error: ' + e + "</div>";
  }
}

async function killProc(pid, name) {
  if (!confirm(`确认结束进程 "${name}" (PID ${pid})？\nEnd process "${name}" (PID ${pid})?`)) return;
  try {
    const r = await api().kill_process(pid);
    if (!r.ok) { alert("结束进程失败 / Failed: " + (r.error || "unknown")); }
    loadProcesses();
  } catch (e) {
    alert("错误 / Error: " + e);
  }
}

// ---------------- 开机管理 / Startup manager ----------------
function renderStartup(container) {
  container.innerHTML = `
    <div class="toolbar">
      <span class="total" id="startupTotal">启动项 / Startup items: —</span>
      <button id="startupRefreshBtn" class="btn primary">刷新 / Refresh</button>
    </div>
    <div id="startupList" class="file-table"><div class="empty">点击刷新获取开机启动项。<br><span class="en">Click refresh to load startup items.</span></div></div>
  `;
  $("startupRefreshBtn").addEventListener("click", loadStartupItems);
  loadStartupItems();
}

async function loadStartupItems() {
  const list = $("startupList");
  list.innerHTML = '<div class="empty">正在获取开机启动项…<br><span class="en">Loading startup items…</span></div>';
  try {
    const r = await api().list_startup_items();
    if (!r.ok) { list.innerHTML = '<div class="empty err">获取失败 / Failed: ' + r.error + "</div>"; return; }
    $("startupTotal").textContent = `启动项 / Startup items: ${r.count}`;
    if (!r.items.length) {
      list.innerHTML = '<div class="empty">未发现注册表启动项。<br><span class="en">No registry startup items found.</span></div>';
      return;
    }
    list.innerHTML = "";
    r.items.forEach((it) => {
      const row = document.createElement("div");
      row.className = "file-row";
      row.innerHTML = `
        <div class="file-info" style="flex:1.2">
          <div class="file-name">${it.name}</div>
          <div class="file-path">${it.command}</div>
        </div>
        <div class="file-size" style="font-size:12px;color:var(--muted)">${it.location}</div>
        <div class="file-size" style="color:${it.enabled ? 'var(--ok)' : 'var(--muted)'}">${it.enabled ? "已启用" : "已禁用"}</div>
        <button class="btn ${it.enabled ? 'ghost' : 'primary'}" data-id="${it.id}">${it.enabled ? "禁用 / Disable" : "启用 / Enable"}</button>
      `;
      row.querySelector("button").addEventListener("click", (e) => {
        e.stopPropagation();
        toggleStartup(it, !it.enabled);
      });
      list.appendChild(row);
    });
  } catch (e) {
    list.innerHTML = '<div class="empty err">错误 / Error: ' + e + "</div>";
  }
}

async function toggleStartup(it, enabled) {
  try {
    const r = await api().set_startup_enabled(it.hkey, it.path, it.value_name, enabled);
    if (!r.ok) { alert("操作失败 / Failed: " + (r.error || "unknown")); }
    loadStartupItems();
    // 刷新首页计数
    const rr = await api().list_startup_items();
    if (rr.ok) $("startupCount").textContent = rr.count;
  } catch (e) {
    alert("错误 / Error: " + e);
  }
}

// ---------------- 弹窗 / Modal ----------------
$("cancelBtn").addEventListener("click", closeConfirm);
$("confirmBtn").addEventListener("click", doClean);

// ---------------- 初始化 / Init ----------------
function init() {
  apiReady = true;
  initNav();
  loadDrives().then(() => {
    currentDrive = drives.includes("C:") ? "C:" : drives[0];
    loadDiskInfo(currentDrive);
  });
  loadHomePreviews();
  updateAdminChip();
  updateMemory();
}

if (window.pywebview && window.pywebview.api) {
  init();
} else {
  window.addEventListener("pywebviewready", init);
}
