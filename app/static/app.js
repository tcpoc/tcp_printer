const TEXT = {
  converting: "正在转换为 PDF",
  preparing: "正在生成预览",
  submitError: "提交任务失败。",
  pageRangeError: "页码范围应在 1 至 {pages} 之间。",
};

let currentJob = null;
let selectedFile = null;
let colorMode = "monochrome";
let rangeMode = "all";
let pollTimer = null;

const byId = (id) => document.getElementById(id);
const screens = ["upload", "processing", "settings", "job", "result"];

function showScreen(name) {
  screens.forEach((screen) => byId(`screen-${screen}`).classList.toggle("active", screen === name));
}

async function request(url, options = {}) {
  const response = await fetch(url, options);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.detail || "请求失败。");
  return payload;
}

function fileSize(size) {
  return size < 1024 * 1024 ? `${Math.ceil(size / 1024)} KB` : `${(size / 1024 / 1024).toFixed(1)} MB`;
}

function chooseFile(file) {
  selectedFile = file || null;
  byId("selected-file").classList.toggle("hidden", !selectedFile);
  byId("start-upload").classList.toggle("hidden", !selectedFile);
  if (selectedFile) {
    byId("selected-file-name").textContent = selectedFile.name;
    byId("selected-file-size").textContent = fileSize(selectedFile.size);
  }
}

function setProcessing(title, message) {
  byId("processing-title").textContent = title;
  byId("processing-message").textContent = message;
  showScreen("processing");
}

function selectedButton(container, value) {
  container.querySelectorAll("button").forEach((button) => button.classList.toggle("selected", button.dataset.value === value));
}

function pageCountForRange() {
  if (!currentJob) return 1;
  if (rangeMode === "all") return currentJob.pages;
  const raw = byId("page-range").value.trim();
  if (!raw) return 0;
  const pages = new Set();
  for (const part of raw.split(",")) {
    const item = part.trim();
    if (!item) return -1;
    if (item.includes("-")) {
      const [from, to] = item.split("-").map(Number);
      if (!Number.isInteger(from) || !Number.isInteger(to) || from < 1 || to < from || to > currentJob.pages) return -1;
      for (let page = from; page <= to; page += 1) pages.add(page);
    } else {
      const page = Number(item);
      if (!Number.isInteger(page) || page < 1 || page > currentJob.pages) return -1;
      pages.add(page);
    }
  }
  return pages.size;
}

function updateEstimate() {
  if (!currentJob) return;
  const pageCount = pageCountForRange();
  const error = byId("range-error");
  error.classList.toggle("hidden", rangeMode !== "custom" || pageCount >= 0);
  if (pageCount < 0) error.textContent = TEXT.pageRangeError.replace("{pages}", currentJob.pages);
  const copies = Math.max(1, Number(byId("copies").value) || 1);
  const sheets = Math.max(0, pageCount) * copies;
  const color = colorMode === "color" ? "彩色" : "黑白";
  byId("estimate").textContent = `${sheets} 张${color} A4`;
}

function renderSettings(job) {
  currentJob = job;
  byId("file-summary").textContent = `${job.file_name} · 已转换为 PDF · 共 ${job.pages} 页`;
  byId("pdf-preview").src = `/static/vendor/pdfjs/viewer.html?file=${encodeURIComponent(job.preview_url)}`;
  byId("page-indicator").textContent = `共 ${job.pages} 页，可在预览中滚动查看`;
  byId("page-range").value = "";
  byId("copies").value = "1";
  colorMode = "monochrome";
  rangeMode = "all";
  selectedButton(byId("color-mode"), colorMode);
  selectedButton(byId("range-mode"), rangeMode);
  byId("custom-range-wrap").classList.add("hidden");
  updateEstimate();
  showScreen("settings");
}


function formatState(state) {
  return {
    converting: "正在转换",
    ready: "等待确认",
    pending: "等待打印",
    printing: "正在打印",
    completed: "已发送至打印机",
    cancelled: "任务已取消",
    stopped: "已停止后续打印",
    failed: "任务失败",
  }[state] || state;
}

function renderJob(job) {
  currentJob = job;
  const isPending = job.state === "pending";
  const isPrinting = job.state === "printing";
  byId("job-title").textContent = isPending ? "任务已加入打印队列" : formatState(job.state);
  byId("job-message").textContent = job.message || "";
  const color = job.color_mode === "color" ? "彩色" : "黑白";
  byId("job-card").innerHTML = `<div><strong>${escapeHtml(job.file_name)}</strong><span class="muted">任务 #${escapeHtml(job.public_id)} · ${job.pages} 页 · ${color} · ${job.copies} 份</span></div><strong>${formatState(job.state)}</strong>`;
  const steps = ["已提交", "转换完成", "等待打印", "正在打印", "已发送至打印机"];
  const current = { pending: 2, printing: 3, completed: 4, cancelled: 2, stopped: 3, failed: 2 }[job.state] ?? 0;
  byId("timeline").innerHTML = steps.map((step, index) => `<li class="${index < current ? "complete" : ""} ${index === current ? "current" : ""}">${step}</li>`).join("");
  const actions = byId("job-actions");
  actions.innerHTML = "";
  if (isPending) actions.append(actionButton("取消任务", "danger-button", () => confirmAction("取消打印任务？", "任务取消后将不会进入打印队列。", "cancel", cancelJob)));
  if (isPrinting) actions.append(actionButton("停止后续打印", "danger-button", () => confirmAction("停止后续打印？", "已经送入打印机、正在出纸或已打印的页面无法撤回。", "stop", stopJob)));
  showScreen("job");
  if (!TERMINAL.includes(job.state)) startPolling(); else stopPolling();
}

const TERMINAL = ["completed", "cancelled", "stopped", "failed"];

function renderResult(job) {
  currentJob = job;
  const titles = {
    completed: "已发送至打印机",
    cancelled: "任务已取消",
    stopped: "已停止后续打印",
    failed: "任务失败",
  };
  byId("result-title").textContent = titles[job.state] || formatState(job.state);
  byId("result-message").textContent = job.message || (job.state === "completed" ? "打印机正在处理，请等待设备完成出纸。" : "");
  const actions = byId("result-actions");
  actions.innerHTML = "";
  actions.append(actionButton("打印另一份文件", "primary-button", resetToUpload));
  if (job.state === "failed") actions.append(actionButton("重新上传", "secondary-button", resetToUpload));
  showScreen("result");
}

function actionButton(label, className, handler) {
  const button = document.createElement("button");
  button.className = className;
  button.textContent = label;
  button.addEventListener("click", handler);
  return button;
}

function escapeHtml(value) {
  const element = document.createElement("span");
  element.textContent = value;
  return element.innerHTML;
}

function confirmAction(title, message, action, handler) {
  const dialog = byId("confirm-dialog");
  const closeDialog = () => dialog.classList.add("hidden");
  const cancelConfirm = byId("dialog-confirm-cancel");
  const stopConfirm = byId("dialog-confirm-stop");
  byId("dialog-title").textContent = title;
  byId("dialog-message").textContent = message;
  byId("dialog-cancel").textContent = "返回";
  cancelConfirm.classList.toggle("hidden", action !== "cancel");
  stopConfirm.classList.toggle("hidden", action !== "stop");
  byId("dialog-cancel").onclick = closeDialog;
  const confirm = action === "stop" ? stopConfirm : cancelConfirm;
  confirm.onclick = () => {
    closeDialog();
    void handler();
  };
  dialog.classList.remove("hidden");
}

async function cancelJob() {
  try { renderResult(await request(`/api/jobs/${currentJob.id}/cancel`, { method: "POST" })); }
  catch (error) { alert(error.message); }
}

async function stopJob() {
  try { renderResult(await request(`/api/jobs/${currentJob.id}/stop`, { method: "POST" })); }
  catch (error) { alert(error.message); }
}

async function refreshSessionJobs() {
  const jobs = await request("/api/jobs");
  byId("session-job-list").innerHTML = jobs.map((job) => `<div class="session-job"><strong>${escapeHtml(job.file_name)}</strong><div class="muted">${formatState(job.state)} · ${job.pages} 页</div></div>`).join("") || "<p class='muted'>当前设备还没有打印任务。</p>";
}

function startPolling() {
  stopPolling();
  pollTimer = window.setInterval(async () => {
    try {
      const job = await request(`/api/jobs/${currentJob.id}`);
      await refreshSessionJobs();
      if (TERMINAL.includes(job.state)) renderResult(job); else renderJob(job);
    } catch (_) { stopPolling(); }
  }, 2000);
}

function stopPolling() { if (pollTimer) window.clearInterval(pollTimer); pollTimer = null; }

async function upload() {
  if (!selectedFile) return;
  byId("upload-progress").classList.remove("hidden");
  setProcessing("正在处理文件", "正在检查文件");
  const form = new FormData();
  form.append("file", selectedFile);
  try {
    const job = await request("/api/uploads", { method: "POST", body: form });
    if (job.state === "ready") renderSettings(job); else renderResult(job);
  } catch (error) {
    renderResult({ state: "failed", message: error.message });
  } finally {
    byId("upload-progress").classList.add("hidden");
  }
}

async function submitJob() {
  const pageCount = pageCountForRange();
  if (pageCount < 0) { updateEstimate(); return; }
  const options = {
    color_mode: colorMode,
    copies: Math.max(1, Number(byId("copies").value) || 1),
    page_range: rangeMode === "custom" ? byId("page-range").value.trim() : "all",
  };
  const button = byId("submit-job");
  button.disabled = true;
  button.textContent = "正在提交...";
  try {
    const job = await request(`/api/jobs/${currentJob.id}/submit`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(options) });
    await refreshSessionJobs();
    renderJob(job);
  } catch (error) {
    alert(error.message || TEXT.submitError);
  } finally {
    button.disabled = false;
    button.textContent = "确认并加入打印队列";
  }
}

function resetToUpload() {
  stopPolling();
  currentJob = null;
  chooseFile(null);
  byId("file-input").value = "";
  showScreen("upload");
}

async function refreshPrinterStatus() {
  try {
    const printer = await request("/api/printer");
    const target = byId("printer-status");
    target.dataset.state = printer.state;
    byId("printer-status-label").textContent = printer.label;
  } catch (_) { /* retain the last visible state */ }
}

byId("file-input").addEventListener("change", (event) => chooseFile(event.target.files[0]));
byId("remove-file").addEventListener("click", resetToUpload);
byId("start-upload").addEventListener("click", upload);
byId("drop-zone").addEventListener("dragover", (event) => { event.preventDefault(); byId("drop-zone").classList.add("dragging"); });
byId("drop-zone").addEventListener("dragleave", () => byId("drop-zone").classList.remove("dragging"));
byId("drop-zone").addEventListener("drop", (event) => { event.preventDefault(); byId("drop-zone").classList.remove("dragging"); chooseFile(event.dataTransfer.files[0]); upload(); });
byId("drop-zone").addEventListener("click", (event) => { if (event.target.id !== "file-input") byId("file-input").click(); });
byId("color-mode").addEventListener("click", (event) => { if (!event.target.dataset.value) return; colorMode = event.target.dataset.value; selectedButton(byId("color-mode"), colorMode); updateEstimate(); });
byId("range-mode").addEventListener("click", (event) => { if (!event.target.dataset.value) return; rangeMode = event.target.dataset.value; selectedButton(byId("range-mode"), rangeMode); byId("custom-range-wrap").classList.toggle("hidden", rangeMode !== "custom"); updateEstimate(); });
byId("page-range").addEventListener("input", updateEstimate);
byId("copies").addEventListener("input", updateEstimate);
byId("copies-minus").addEventListener("click", () => { byId("copies").value = Math.max(1, Number(byId("copies").value) - 1); updateEstimate(); });
byId("copies-plus").addEventListener("click", () => { byId("copies").value = Math.min(99, Number(byId("copies").value) + 1); updateEstimate(); });
byId("submit-job").addEventListener("click", submitJob);
refreshPrinterStatus();
window.setInterval(refreshPrinterStatus, 10000);
