"use strict";

const $ = (id) => document.getElementById(id);
const activeStates = new Set(["queued", "preparing", "transcribing", "translating"]);
const state = { config: null, projects: [], current: null, draft: null, dirty: false,
  saving: false, uploading: false, filter: false, rows: new Map(), audioId: null,
  modelData: null, modelSignature: "", modelRequest: false };
let toastTimer;

function node(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}

function notify(message) {
  const dialog = document.querySelector("dialog[open]");
  if (dialog) {
    let feedback = dialog.querySelector(".dialog-feedback");
    if (!feedback) {
      feedback = node("p", "notice warning dialog-feedback");
      feedback.setAttribute("role", "alert");
      dialog.append(feedback);
    }
    feedback.textContent = message;
    feedback.scrollIntoView({ block: "nearest" });
    return;
  }
  $("toast").textContent = message;
  $("toast").hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { $("toast").hidden = true; }, 6500);
}

function on(element, event, action) {
  element.addEventListener(event, (e) => {
    Promise.resolve().then(() => action(e)).catch((error) => notify(error.message));
  });
}

async function api(path, method = "GET", body) {
  const headers = { "X-SNote-Request": "1" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const response = await fetch(path, { method, headers,
    body: body === undefined ? undefined : JSON.stringify(body) });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "The request failed. Please try again.");
  return result;
}

function busy() { return state.current && activeStates.has(state.current.status); }
function language(code) { return state.config.languages[code] || code || "Detecting…"; }
function clock(seconds) {
  const value = Math.floor(Math.max(0, seconds || 0));
  const minutes = Math.floor(value / 60);
  return `${String(minutes).padStart(2, "0")}:${String(value % 60).padStart(2, "0")}`;
}
function date(value) {
  return new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric" }).format(new Date(value));
}
function confirmLeave() {
  return !state.dirty || window.confirm("Leave this recording and discard your unsaved edits?");
}

function fillLanguages(id, first, selected) {
  const select = $(id);
  select.replaceChildren();
  if (first) select.add(new Option(first[1], first[0]));
  for (const [code, name] of Object.entries(state.config.languages)) select.add(new Option(name, code));
  select.value = selected;
}

async function refreshList() {
  state.projects = await api("/api/projects");
  const list = $("project-list");
  list.replaceChildren();
  $("project-count").textContent = state.projects.length;
  if (!state.projects.length) list.append(node("p", "sidebar-empty", "Your recordings will live here."));
  for (const project of state.projects) {
    const button = node("button", `project-item${project.id === state.current?.id ? " active" : ""}`);
    button.append(node("strong", "", project.title));
    const status = activeStates.has(project.status) ? `${project.status}…`
      : project.is_demo ? "sample workspace" : `${project.segment_count} lines · ${clock(project.duration)}`;
    button.append(node("small", "", `${date(project.created_at)} / ${status}`));
    button.setAttribute("aria-current", project.id === state.current?.id ? "page" : "false");
    on(button, "click", () => selectProject(project.id));
    list.append(button);
  }
}

async function selectProject(id) {
  if (state.saving || !confirmLeave()) return;
  const project = await api(`/api/projects/${id}`);
  $("search").value = "";
  state.filter = false;
  adopt(project);
  await refreshList();
}

function adopt(project) {
  state.current = project;
  state.draft = structuredClone(project);
  state.dirty = false;
  renderProject();
}

function renderSave() {
  $("save").disabled = !state.dirty || state.saving || busy();
  $("discard").hidden = !state.dirty;
  $("discard").disabled = state.saving;
  $("save").textContent = state.saving ? "Saving…" : "Save changes";
  $("save-state").textContent = state.saving ? "Saving your changes…" : state.dirty ? "Unsaved changes" : "All changes saved";
  $("save-dot").classList.toggle("dirty", state.dirty);
  const count = state.draft.segments.filter((s) => s.reviewed).length;
  $("review-count").textContent = `${count} of ${state.draft.segments.length} lines reviewed`;
}

function changed() { state.dirty = true; renderSave(); }
function autosize(textarea) {
  textarea.style.height = "0px";
  textarea.style.height = `${Math.max(58, textarea.scrollHeight + 3)}px`;
}

function renderProject() {
  const project = state.draft;
  $("welcome").hidden = true;
  $("workspace").hidden = false;
  document.title = `${project.title} · SNote`;
  $("recording-kicker").textContent = project.is_demo ? "A PAGE TO EXPLORE" : "YOUR RECORDING";
  $("project-title").value = project.title;
  $("project-title").disabled = busy();
  $("delete-project").disabled = busy();
  $("recording-meta").replaceChildren(
    node("span", "language-tag", `${language(project.detected_language)}${project.target ? ` → ${language(project.target)}` : ""}`),
    node("span", "", clock(project.duration)),
    node("span", "", date(project.created_at)),
    node("span", "", project.is_demo ? "Illustrative sample" : `${project.model} model`),
  );
  $("sample-note").hidden = !project.is_demo;
  const failed = ["error", "cancelled", "interrupted"].includes(project.status);
  $("job-notice").hidden = !busy() && !failed;
  $("job-title").textContent = failed ? (project.status === "error" ? "This recording needs attention" : "Processing stopped") : "Working on your recording";
  $("job-message").textContent = project.message;
  $("job-progress").hidden = !busy();
  $("job-progress").value = project.progress;
  $("cancel-job").hidden = !busy();
  $("retry-job").hidden = !failed || project.is_demo;
  $("warning").hidden = !project.warning;
  $("warning").textContent = project.warning;
  $("audio-duration").textContent = clock(project.duration);
  $("audio").hidden = !project.has_audio;
  $("waveform").hidden = !project.has_audio;
  $("no-audio").hidden = project.has_audio;
  $("no-audio").textContent = project.is_demo ? "The sample has no attached audio. Add a recording to listen along." : "Playback becomes available after the audio is prepared.";
  if (project.has_audio && state.audioId !== project.id) {
    $("audio").src = `/api/projects/${project.id}/audio`;
    state.audioId = project.id;
  } else if (!project.has_audio && state.audioId) {
    $("audio").pause();
    $("audio").removeAttribute("src");
    $("audio").load();
    state.audioId = null;
  }
  $("line-count").textContent = project.segments.length;
  $("source-heading").textContent = `ORIGINAL / ${language(project.detected_language).toUpperCase()}`;
  $("translation-heading").textContent = `TRANSLATION${project.target ? ` / ${language(project.target).toUpperCase()}` : ""}`;
  $("retranslate-target").value = project.target || "es";
  $("retranslate-target").disabled = busy() || project.is_demo;
  $("retranslate").disabled = busy() || project.is_demo || !project.segments.length;
  $("export-open").disabled = busy() || !project.segments.length;
  $("filter-review").setAttribute("aria-pressed", String(state.filter));
  const container = $("segments");
  container.replaceChildren();
  state.rows.clear();
  for (const segment of project.segments) {
    const row = node("article", "segment-row");
    const time = node("button", "time-button");
    time.append(node("small", "", String(segment.id + 1).padStart(2, "0")), node("span", "", clock(segment.start)));
    time.disabled = !project.has_audio;
    time.setAttribute("aria-label", `Play line ${segment.id + 1} from ${clock(segment.start)}`);
    time.title = `${clock(segment.start)} – ${clock(segment.end)}`;
    on(time, "click", async () => { $("audio").currentTime = segment.start; await $("audio").play(); });
    const source = node("div", "source-cell");
    const translation = node("div", "translation-cell");
    source.append(node("span", "cell-caption", "ORIGINAL"));
    translation.append(node("span", "cell-caption", "TRANSLATION"));
    const originalInput = node("textarea");
    originalInput.value = segment.text;
    originalInput.dir = "auto";
    originalInput.maxLength = 5000;
    originalInput.setAttribute("aria-label", `Original text, line ${segment.id + 1}`);
    const translatedInput = node("textarea");
    translatedInput.value = segment.translation;
    translatedInput.dir = "auto";
    translatedInput.maxLength = 5000;
    translatedInput.placeholder = project.target ? "Translation pending…" : "Choose a translation language above.";
    translatedInput.setAttribute("aria-label", `Translation, line ${segment.id + 1}`);
    originalInput.disabled = busy();
    translatedInput.disabled = busy() || !project.target;
    const review = node("label", "review-check");
    const checkbox = node("input");
    checkbox.type = "checkbox";
    checkbox.checked = segment.reviewed;
    checkbox.disabled = busy();
    checkbox.setAttribute("aria-label", `Mark line ${segment.id + 1} reviewed`);
    review.append(checkbox);
    on(originalInput, "input", () => {
      segment.text = originalInput.value;
      segment.reviewed = false;
      checkbox.checked = false;
      changed(); autosize(originalInput);
    });
    on(translatedInput, "input", () => {
      segment.translation = translatedInput.value;
      segment.reviewed = false;
      checkbox.checked = false;
      changed(); autosize(translatedInput);
    });
    on(checkbox, "change", () => { segment.reviewed = checkbox.checked; changed(); applyFilter(); });
    source.append(originalInput);
    translation.append(translatedInput);
    row.append(time, source, translation, review);
    container.append(row);
    state.rows.set(segment.id, row);
  }
  applyFilter(); renderSave(); drawWaveform();
}

function applyFilter() {
  if (!state.draft) return;
  const query = $("search").value.toLocaleLowerCase().trim();
  let count = 0;
  for (const segment of state.draft.segments) {
    const row = state.rows.get(segment.id);
    row.hidden = (state.filter && segment.reviewed) || !(segment.text + " " + segment.translation).toLocaleLowerCase().includes(query);
    if (!row.hidden) {
      count++;
      row.querySelectorAll("textarea").forEach(autosize);
    }
  }
  $("no-segments").hidden = count > 0;
  $("no-segments").textContent = state.draft.segments.length ? "No lines match this view." : busy() ? "Your transcript will appear here as it is processed." : "No transcript lines yet.";
}

function drawWaveform() {
  const canvas = $("waveform");
  const ctx = canvas.getContext("2d");
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  const peaks = state.current?.peaks || [];
  if (!peaks.length) return;
  const progress = $("audio").currentTime / Math.max(state.current.duration, 0.01);
  const maximum = Math.max(0.02, ...peaks);
  const step = canvas.width / peaks.length;
  peaks.forEach((peak, index) => {
    const height = Math.max(3, peak / maximum * 53);
    ctx.fillStyle = index / peaks.length <= progress ? "#bd5637" : "#c3cbb6";
    ctx.fillRect(index * step + 2, (canvas.height - height) / 2, Math.max(2, step - 5), height);
  });
}

async function save() {
  if (!state.dirty || !state.current || busy() || state.saving) return;
  state.saving = true; renderSave();
  // Snapshot the edit buffer. Editing is locked until this save settles.
  const projectId = state.current.id;
  const payload = { revision: state.current.revision, title: state.draft.title, segments: structuredClone(state.draft.segments) };
  $("workspace").querySelectorAll("textarea, #project-title, .review-check input").forEach((input) => { input.disabled = true; });
  try {
    const result = await api(`/api/projects/${projectId}`, "PATCH", payload);
    adopt(result);
    await refreshList();
  } finally {
    state.saving = false;
    $("workspace").querySelectorAll("textarea, #project-title, .review-check input").forEach((input) => { input.disabled = false; });
    if (!state.draft.target) $("workspace").querySelectorAll(".translation-cell textarea").forEach((input) => { input.disabled = true; });
    renderSave();
  }
}

function openDialog(id) {
  if (state.uploading) return;
  for (const dialog of document.querySelectorAll("dialog[open]")) dialog.close();
  $(id).querySelector(".dialog-feedback")?.remove();
  $(id).showModal();
  if (id === "setup-dialog" || id === "import-dialog") refreshModels().catch((error) => notify(error.message));
}

function updateFile() {
  const file = $("audio-file").files[0];
  $("file-label").textContent = file ? file.name : "Choose an audio file";
  $("upload-status").textContent = "";
}

async function upload(event) {
  event.preventDefault();
  if (state.uploading || state.saving) return;
  const file = $("audio-file").files[0];
  if (!file) throw new Error("Choose an audio file first.");
  if (file.size > state.config.max_upload) throw new Error("Choose a recording smaller than 100 MB.");
  if (!file.size) throw new Error("That file is empty.");
  if (!confirmLeave()) return;
  const query = new URLSearchParams({ filename: file.name, source: $("source-language").value,
    target: $("target-language").value, model: $("model").value });
  state.uploading = true;
  $("upload-submit").disabled = true;
  $("upload-status").textContent = "Adding the audio…";
  try {
    const project = await new Promise((resolve, reject) => {
      const request = new XMLHttpRequest();
      request.open("POST", `/api/projects?${query}`);
      request.setRequestHeader("X-SNote-Request", "1");
      request.setRequestHeader("Content-Type", "application/octet-stream");
      request.upload.addEventListener("progress", (e) => {
        if (e.lengthComputable) $("upload-status").textContent = `Adding the audio… ${Math.round(100 * e.loaded / e.total)}%`;
      });
      request.addEventListener("load", () => {
        try {
          const result = JSON.parse(request.responseText);
          if (request.status >= 400) reject(new Error(result.error || "Upload failed."));
          else resolve(result);
        } catch { reject(new Error("The server returned an unreadable response.")); }
      });
      request.addEventListener("error", () => reject(new Error("Could not reach SNote. Is the server still running?")));
      request.send(file);
    });
    $("import-dialog").close();
    $("audio-file").value = ""; updateFile();
    adopt(project);
    await refreshList();
  } catch (error) {
    $("upload-status").textContent = error.message;
  } finally {
    state.uploading = false;
    updateUploadAvailability();
  }
}

async function download() {
  const format = $("export-format").value;
  const content = $("export-content").value;
  const response = await fetch(`/api/projects/${state.current.id}/export?format=${format}&content=${content}`);
  if (!response.ok) throw new Error((await response.json()).error);
  const url = URL.createObjectURL(await response.blob());
  const link = node("a");
  link.href = url;
  const filename = response.headers.get("Content-Disposition")?.match(/filename="([^"]+)"/);
  link.download = filename ? filename[1] : `transcript.${format}`;
  document.body.append(link); link.click(); link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  $("export-dialog").close();
}

function exportOptions() {
  const json = $("export-format").value === "json";
  $("export-content").disabled = json;
  const missing = state.current.segments.some((s) => !s.translation.trim());
  $("export-note").textContent = missing ? "Some translations are missing. Export the original text, or finish translating first." : "Both languages places each translation underneath its original line.";
}

const downloadStates = new Set(["fetching", "downloading", "installing"]);
function modelBusy() { return state.modelRequest || downloadStates.has(state.modelData?.job.status); }
function fileSize(bytes) {
  if (bytes === null || bytes === undefined) return "";
  if (bytes >= 1000000000) return `${(bytes / 1000000000).toFixed(2)} GB`;
  return `${(bytes / 1000000).toFixed(1)} MB`;
}

function updateUploadAvailability() {
  const hasModel = state.modelData?.speech.some((model) => model.installed);
  $("upload-submit").disabled = state.uploading || !state.config.speech_installed || !hasModel;
  $("dependency-note").hidden = state.config.speech_installed && hasModel;
  $("dependency-message").textContent = !state.config.speech_installed
    ? "The audio dependencies are missing. Open Models for the install command."
    : "Choose and download a speech model before adding audio.";
  $("first-run-notice").hidden = !!hasModel;
}

function modelCard(model, kind) {
  const card = node("article", "model-card");
  const description = node("div", "model-description");
  const heading = node("div", "model-card-heading");
  heading.append(node("h3", "", model.label));
  if (model.installed) heading.append(node("span", "model-badge", "Installed"));
  else if (kind === "speech" && model.id === "tiny") heading.append(node("span", "model-badge suggested", "Start here"));
  description.append(heading, node("p", "", model.note || `Language pack · version ${model.version}`));
  const current = state.modelData.job;
  const running = downloadStates.has(current.status) && current.kind === kind && current.id === model.id;
  const button = node("button", model.installed ? "secondary" : "primary", model.installed ? "Ready" : running ? "Downloading…" : "Download ↓");
  const dependency = kind === "speech" ? state.modelData.speech_installed : state.modelData.translation_installed;
  button.disabled = model.installed || modelBusy() || !dependency;
  button.setAttribute("aria-label", `${model.installed ? "Installed" : "Download"} ${model.label}`);
  on(button, "click", () => startModel(kind, model.id));
  card.append(description, button);
  return card;
}

function renderTranslationModels() {
  const container = $("translation-model-list");
  container.replaceChildren();
  if (!state.modelData) return;
  const query = $("model-search").value.toLocaleLowerCase().trim();
  const models = state.modelData.translations.filter((model) => `${model.label} ${model.id}`.toLocaleLowerCase().includes(query));
  for (const model of models) container.append(modelCard(model, "translation"));
  if (!models.length) container.append(node("p", "model-empty", state.modelData.catalog_loaded
    ? "No language pairs match this search." : "Choose Load language list to browse available pairs."));
}

function renderModels(force = false) {
  const data = state.modelData;
  if (!data) return;
  const signature = JSON.stringify([data.speech, data.translations, data.installed_translations,
    data.job.status, data.job.id, state.modelRequest, data.speech_installed, data.translation_installed]);
  if (force || signature !== state.modelSignature) {
    state.modelSignature = signature;
    $("speech-model-list").replaceChildren(...data.speech.map((model) => modelCard(model, "speech")));
    renderTranslationModels();
    $("model-dependencies").hidden = data.speech_installed && data.translation_installed;
    $("refresh-catalog").disabled = modelBusy();
    $("refresh-catalog").textContent = data.catalog_loaded ? "Refresh list" : "Load language list";
    $("installed-pairs").textContent = data.installed_translations.length
      ? `Installed: ${data.installed_translations.map((pair) => pair.label).join(" · ")}` : "No translation packs installed yet.";
    $("setup-status").textContent = `${data.speech.filter((model) => model.installed).length} speech models · ${data.installed_translations.length} translation packs installed`;
    const selection = $("model").value;
    $("model").replaceChildren();
    for (const model of data.speech) {
      const option = new Option(`${model.label}${model.installed ? "" : " · not installed"}`, model.id);
      option.disabled = !model.installed;
      $("model").add(option);
    }
    const selectable = data.speech.filter((model) => model.installed);
    $("model").value = selectable.some((model) => model.id === selection) ? selection : (selectable[0]?.id || "");
    updateUploadAvailability();
  }
  const job = data.job;
  $("model-job").hidden = job.status === "idle";
  $("model-job-label").textContent = job.label;
  $("model-job-message").textContent = job.message;
  $("model-job").classList.toggle("has-error", job.status === "error");
  $("model-progress").hidden = ["complete", "error", "cancelled"].includes(job.status);
  if (job.progress === null) $("model-progress").removeAttribute("value");
  else $("model-progress").value = job.progress;
  $("model-job-size").textContent = job.downloaded_bytes
    ? `${fileSize(job.downloaded_bytes)}${job.total_bytes ? ` / ${fileSize(job.total_bytes)}` : ""}` : "";
  $("cancel-model").hidden = !downloadStates.has(job.status);
  $("cancel-model").disabled = job.status === "installing" || state.modelRequest;
  $("retry-model").hidden = !["error", "cancelled"].includes(job.status);
  $("retry-model").disabled = state.modelRequest;
}

async function refreshModels() {
  state.modelData = await api("/api/models");
  renderModels();
}

async function startModel(kind, id = "") {
  if (modelBusy()) return;
  state.modelRequest = true; renderModels();
  try {
    state.modelData = kind === "catalog" ? await api("/api/models/catalog", "POST")
      : await api("/api/models/download", "POST", { kind, id });
  } finally { state.modelRequest = false; renderModels(); }
}

async function init() {
  state.config = await api("/api/config");
  fillLanguages("source-language", ["auto", "Detect automatically"], "auto");
  fillLanguages("target-language", ["", "Transcript only"], "");
  fillLanguages("retranslate-target", null, "es");
  await refreshModels();
  await refreshList();
  for (const button of document.querySelectorAll("[data-open]")) on(button, "click", () => openDialog(button.dataset.open));
  for (const button of document.querySelectorAll("[data-close]")) on(button, "click", () => { if (!state.uploading) button.closest("dialog").close(); });
  for (const dialog of document.querySelectorAll("dialog")) dialog.addEventListener("cancel", (e) => { if (state.uploading) e.preventDefault(); });
  on($("home"), "click", async () => {
    if (state.saving || !confirmLeave()) return;
    $("audio").pause(); state.current = null; state.draft = null; state.dirty = false;
    $("workspace").hidden = true; $("welcome").hidden = false;
    document.title = "SNote · Your audio notebook";
    await refreshList();
  });
  on($("try-sample"), "click", async () => {
    $("try-sample").disabled = true;
    try { adopt(await api("/api/sample", "POST")); await refreshList(); }
    finally { $("try-sample").disabled = false; }
  });
  on($("project-title"), "input", () => { state.draft.title = $("project-title").value; changed(); });
  on($("search"), "input", applyFilter);
  on($("filter-review"), "click", () => {
    state.filter = !state.filter;
    $("filter-review").setAttribute("aria-pressed", String(state.filter)); applyFilter();
  });
  on($("save"), "click", save);
  on($("discard"), "click", () => { if (window.confirm("Discard your unsaved edits?")) adopt(state.current); });
  on($("delete-project"), "click", async () => {
    if (state.saving || !window.confirm(`Delete “${state.current.title}” and its saved audio and transcript? This cannot be undone.`)) return;
    await api(`/api/projects/${state.current.id}`, "DELETE");
    state.dirty = false; $("home").click();
  });
  on($("cancel-job"), "click", async () => { adopt(await api(`/api/projects/${state.current.id}/cancel`, "POST")); });
  on($("retry-job"), "click", async () => {
    if (state.saving || !window.confirm("Transcribe again? This replaces existing transcript lines, translations, and edits.")) return;
    adopt(await api(`/api/projects/${state.current.id}/retry`, "POST", { revision: state.current.revision }));
  });
  on($("retranslate"), "click", async () => {
    if (state.saving) return;
    const target = $("retranslate-target").value;
    if (!window.confirm("Translate all lines? This replaces the current translations and clears their review marks.")) return;
    await save();
    adopt(await api(`/api/projects/${state.current.id}/translate`, "POST", { target, revision: state.current.revision }));
  });
  on($("export-open"), "click", async () => {
    if (state.saving) return;
    await save(); exportOptions(); openDialog("export-dialog");
  });
  on($("export-format"), "change", exportOptions);
  on($("download"), "click", download);
  on($("speech-tab"), "click", () => {
    $("speech-model-panel").hidden = false; $("translation-model-panel").hidden = true;
    $("speech-tab").setAttribute("aria-pressed", "true"); $("translation-tab").setAttribute("aria-pressed", "false");
  });
  on($("translation-tab"), "click", () => {
    $("speech-model-panel").hidden = true; $("translation-model-panel").hidden = false;
    $("speech-tab").setAttribute("aria-pressed", "false"); $("translation-tab").setAttribute("aria-pressed", "true");
  });
  on($("refresh-catalog"), "click", () => startModel("catalog"));
  on($("model-search"), "input", renderTranslationModels);
  on($("cancel-model"), "click", async () => { state.modelData = await api("/api/models/cancel", "POST"); renderModels(); });
  on($("retry-model"), "click", () => startModel(state.modelData.job.kind, state.modelData.job.id));
  on($("audio-file"), "change", updateFile);
  // Submit must prevent navigation synchronously, before the async handler starts.
  $("import-form").addEventListener("submit", (e) => { e.preventDefault(); upload(e).catch((error) => notify(error.message)); });
  $("drop-zone").addEventListener("dragover", (e) => { e.preventDefault(); $("drop-zone").classList.add("dragover"); });
  $("drop-zone").addEventListener("dragleave", () => $("drop-zone").classList.remove("dragover"));
  $("drop-zone").addEventListener("drop", (e) => {
    e.preventDefault(); $("drop-zone").classList.remove("dragover");
    if (e.dataTransfer.files.length && !state.uploading) {
      const transfer = new DataTransfer(); transfer.items.add(e.dataTransfer.files[0]);
      $("audio-file").files = transfer.files; updateFile(); openDialog("import-dialog");
    }
  });
  $("audio").addEventListener("timeupdate", () => {
    if (!state.current) return;
    drawWaveform();
    for (const segment of state.current.segments) state.rows.get(segment.id)?.classList.toggle("playing", $("audio").currentTime >= segment.start && $("audio").currentTime < segment.end);
  });
  $("audio").addEventListener("error", () => { if (state.audioId) notify("The audio could not be loaded. Check that the server is still running."); });
  window.addEventListener("resize", applyFilter);
  window.addEventListener("beforeunload", (e) => { if (state.dirty || state.uploading) { e.preventDefault(); e.returnValue = ""; } });
  window.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s" && state.current) {
      e.preventDefault(); save().catch((error) => notify(error.message));
    }
  });
  poll();
}

async function poll() {
  try {
    const id = state.current?.id;
    if (id && busy() && !state.dirty && !state.saving) {
      const project = await api(`/api/projects/${id}`);
      if (state.current?.id === id && !state.dirty && !state.saving) adopt(project);
    }
    if (busy() || state.projects.some((p) => activeStates.has(p.status))) await refreshList();
    if (modelBusy() || $("setup-dialog").open) await refreshModels();
  } catch (error) {
    notify(`Connection interrupted: ${error.message}`);
  } finally { setTimeout(poll, 1800); }
}

init().catch((error) => notify(`Could not start the notebook: ${error.message}`));
