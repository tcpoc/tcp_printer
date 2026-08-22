const tokenInput = document.getElementById("admin-token");
const authPanel = document.getElementById("auth-panel");
const dashboard = document.getElementById("dashboard");
const authError = document.getElementById("auth-error");
const tokenKey = "tcp-printer-admin-token";
let adminToken = sessionStorage.getItem(tokenKey) || "";

function escapeHtml(value) {
  const element = document.createElement("span");
  element.textContent = value;
  return element.innerHTML;
}

function formatBytes(value) {
  if (value < 1024 * 1024) return `${Math.round(value / 1024)} KB`;
  if (value < 1024 * 1024 * 1024) return `${(value / 1024 / 1024).toFixed(1)} MB`;
  return `${(value / 1024 / 1024 / 1024).toFixed(1)} GB`;
}

function formatState(state) {
  return { pending: "等待打印", printing: "正在打印", completed: "已发送至打印机", cancelled: "已取消", stopped: "已停止", failed: "失败", ready: "待确认", converting: "转换中" }[state] || state;
}

async function request(url, options = {}) {
  const headers = { ...(options.headers || {}), "X-Admin-Token": adminToken };
  const response = await fetch(url, { ...options, headers });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.detail || "请求失败。");
  return payload;
}

function metric(label, value) {
  const element = document.createElement("div");
  element.className = "metric";
  element.innerHTML = `<span class="muted">${label}</span><strong>${value}</strong>`;
  return element;
}

function renderStatus(payload) {
    const metrics = document.getElementById("metrics");
  const queue = payload.queue || {};
  metrics.replaceChildren(
    metric("打印机", payload.printer.label),
    metric("运行模式", payload.mode),
    metric("队列", queue.paused ? "已暂停" : "运行中"),
    metric("等待任务", queue.pending || 0),
    metric("打印中", queue.printing || 0),
    metric("可用磁盘", formatBytes(payload.storage.free)),
  );
  document.getElementById("pause-queue").disabled = Boolean(queue.paused);
  document.getElementById("resume-queue").disabled = !queue.paused;
  const detail = payload.printer_details || {};
  document.getElementById("printer-detail").textContent = detail.error
    ? detail.error
    : `打印机队列：${detail.queue_name || "未配置"} · 原始状态：${detail.raw_status ?? "不适用"}`;
  const printerTable = document.getElementById("printer-job-table");
  printerTable.replaceChildren();
  (detail.jobs || []).forEach((job) => {
    const row = document.createElement("tr");
    row.innerHTML = `<td>${escapeHtml(job.id)}</td><td>${escapeHtml(job.document)}</td><td>${escapeHtml(job.user)}</td><td>${escapeHtml(job.status)}</td><td>${escapeHtml(`${job.pages_printed}/${job.total_pages}`)}</td><td></td>`;
    row.lastElementChild.append(action("取消", () => cancelPrinterJob(job.id)));
    printerTable.append(row);
  });
  if (!detail.jobs?.length) printerTable.innerHTML = '<tr><td colspan="6" class="muted">当前没有 Windows 打印作业</td></tr>';
  const table = document.getElementById("job-table");
  table.replaceChildren();
  payload.jobs.forEach((job) => {
    const row = document.createElement("tr");
    row.innerHTML = `<td>${escapeHtml(job.public_id)}</td><td>${escapeHtml(job.file_name)}</td><td>${formatState(job.state)}<div class="muted">${escapeHtml(job.message || "")}</div></td><td>${escapeHtml(job.printer_job_id || "-")}</td><td>${new Date(job.created_at).toLocaleString()}</td><td></td>`;
    const actionCell = row.lastElementChild;
    if (["pending", "ready", "converting"].includes(job.state)) {
      actionCell.append(action("取消", () => changeJob(job.id, "cancel")));
    } else if (job.state === "printing") {
      actionCell.append(action("停止", () => changeJob(job.id, "stop")));
    }
    table.append(row);
  });
  document.getElementById("last-updated").textContent = `上次更新：${new Date().toLocaleString()} · 自动保留 ${payload.retention_hours} 小时`;
}

function action(label, handler) {
  const button = document.createElement("button");
  button.className = "table-action";
  button.type = "button";
  button.textContent = label;
  button.addEventListener("click", handler);
  return button;
}

async function refresh() {
  try { renderStatus(await request("/api/admin/status")); }
  catch (error) { authError.textContent = error.message; }
}

async function changeJob(id, operation) {
  try { await request(`/api/admin/jobs/${id}/${operation}`, { method: "POST" }); await refresh(); }
  catch (error) { authError.textContent = error.message; }
}

async function cancelPrinterJob(id) {
  try { await request(`/api/admin/printer/jobs/${encodeURIComponent(id)}/cancel`, { method: "POST" }); await refresh(); }
  catch (error) { authError.textContent = error.message; }
}

async function setQueuePaused(paused) {
  try { await request(`/api/admin/queue/${paused ? "pause" : "resume"}`, { method: "POST" }); await refresh(); }
  catch (error) { authError.textContent = error.message; }
}

async function enter() {
  adminToken = tokenInput.value.trim();
  if (!adminToken) { authError.textContent = "请输入管理员令牌。"; return; }
  try {
    sessionStorage.setItem(tokenKey, adminToken);
    renderStatus(await request("/api/admin/status"));
    authPanel.classList.add("hidden");
    dashboard.classList.remove("hidden");
    authError.textContent = "";
  } catch (error) {
    sessionStorage.removeItem(tokenKey);
    authError.textContent = error.message;
  }
}

document.getElementById("save-token").addEventListener("click", enter);
document.getElementById("refresh-status").addEventListener("click", refresh);
document.getElementById("pause-queue").addEventListener("click", () => setQueuePaused(true));
document.getElementById("resume-queue").addEventListener("click", () => setQueuePaused(false));
document.getElementById("run-cleanup").addEventListener("click", async () => {
  try {
    const result = await request("/api/admin/cleanup", { method: "POST" });
    authError.textContent = result.message || `已清理 ${result.removed_jobs || 0} 个已结束任务`;
    await refresh();
  }
  catch (error) { authError.textContent = error.message; }
});
tokenInput.addEventListener("keydown", (event) => { if (event.key === "Enter") enter(); });
if (adminToken) { tokenInput.value = adminToken; enter(); }
