// ─── RepoLens Frontend — TypeScript ───────────────────────────────────────────
// Compiled to static/app.js by tsc. No framework. No runtime dependencies.
// Backend API: FastAPI at /verify, /index-repository, /indexes, /ask/stream, /health

declare const marked: { parse(src: string): string; setOptions?: (opts: Record<string, unknown>) => void };
declare const hljs: { highlightElement(el: Element): void; highlightAuto(code: string): { value: string }; getLanguage(lang: string): unknown; highlight(code: string, opts: { language: string }): { value: string } };
declare const DOMPurify: { sanitize(dirty: string): string };

// ─── State ────────────────────────────────────────────────────────────────────

let currentIndexId: string | null = null;
let conversationHistory: ConversationMessage[] = [];
let isVerifying = false;

// ─── DOM Element References ───────────────────────────────────────────────────

function el<T extends HTMLElement>(id: string): T {
  const e = document.getElementById(id) as T | null;
  if (!e) throw new Error(`Element #${id} not found`);
  return e;
}

function qs<T extends HTMLElement>(selector: string, parent: Element | Document = document): T {
  const e = parent.querySelector<T>(selector);
  if (!e) throw new Error(`Selector '${selector}' not found`);
  return e;
}

// ─── API Client ───────────────────────────────────────────────────────────────

async function apiFetch(path: string, options: RequestInit = {}): Promise<Response> {
  const headers = new Headers(options.headers as HeadersInit | undefined);
  if (!headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  const apiKey = localStorage.getItem("repolens_api_key");
  if (apiKey && !headers.has("X-API-Key")) {
    headers.set("X-API-Key", apiKey);
  }
  return fetch(path, { ...options, headers });
}

// ─── Toast Notification ──────────────────────────────────────────────────────

function showToast(message: string, type: "success" | "error" | "info" = "info"): void {
  const existing = document.getElementById("rl-toast");
  if (existing) existing.remove();

  const toast = document.createElement("div");
  toast.id = "rl-toast";
  toast.className = `rl-toast rl-toast--${type}`;
  toast.textContent = message;
  document.body.appendChild(toast);
  requestAnimationFrame(() => toast.classList.add("rl-toast--visible"));
  setTimeout(() => {
    toast.classList.remove("rl-toast--visible");
    setTimeout(() => toast.remove(), 300);
  }, 3200);
}

// ─── Health Monitor ───────────────────────────────────────────────────────────

async function checkHealth(): Promise<void> {
  const dot = document.getElementById("health-dot");
  const label = document.getElementById("health-label");

  const setState = (state: "checking" | "ok" | "error") => {
    if (dot) dot.className = `health-dot health-dot--${state}`;
    if (label) label.textContent = state === "ok" ? "Connected" : state === "error" ? "Offline" : "Checking…";
  };

  setState("checking");
  try {
    const res = await fetch("/health");
    const data: HealthResponse = await res.json();
    setState(data.status === "ok" ? "ok" : "error");
  } catch {
    setState("error");
  }
}

// ─── Index Manager ────────────────────────────────────────────────────────────

async function loadIndexes(): Promise<void> {
  const listEl = document.getElementById("indexes-list");
  if (!listEl) return;

  listEl.innerHTML = `<div class="indexes-loading"><span class="mini-spinner"></span> Loading…</div>`;

  try {
    const res = await apiFetch("/indexes");
    if (res.status === 401) {
      listEl.innerHTML = `<div class="indexes-empty">Authentication required.</div>`;
      return;
    }
    if (!res.ok) {
      listEl.innerHTML = `<div class="indexes-empty">Failed to load indexes (${res.status})</div>`;
      return;
    }

    const data: IndexEntry[] = await res.json();

    if (data.length === 0) {
      listEl.innerHTML = `
        <div class="indexes-empty">
          <svg width="32" height="32" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5">
            <path stroke-linecap="round" stroke-linejoin="round" d="M20 13V6a2 2 0 00-2-2H6a2 2 0 00-2 2v7m16 0v5a2 2 0 01-2 2H6a2 2 0 01-2-2v-5m16 0H4" />
          </svg>
          <span>No indexes yet</span>
        </div>`;
      return;
    }

    listEl.innerHTML = "";
    data.forEach((idx, i) => {
      const item = buildIndexItem(idx);
      item.style.animationDelay = `${i * 40}ms`;
      listEl.appendChild(item);
    });
  } catch (err) {
    const msg = err instanceof Error ? err.message : "Unknown error";
    listEl.innerHTML = `<div class="indexes-empty">Error: ${DOMPurify.sanitize(msg)}</div>`;
  }
}

function buildIndexItem(idx: IndexEntry): HTMLElement {
  const chunkCount = idx.vector_count || 0;
  const isEmpty = chunkCount === 0;
  const isActive = idx.index_id === currentIndexId;

  let shortName = idx.repo_url || "Unknown";
  try {
    const parts = new URL(shortName).pathname.split("/").filter(Boolean);
    if (parts.length >= 2) shortName = `${parts[parts.length - 2]}/${parts[parts.length - 1]}`;
  } catch { /* ignore */ }

  const created = idx.created_at ? new Date(idx.created_at).toLocaleDateString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "N/A";
  const cleanName = DOMPurify.sanitize(shortName);
  const cleanUrl = DOMPurify.sanitize(idx.repo_url || "");
  const cleanCreated = DOMPurify.sanitize(created);

  const item = document.createElement("div");
  item.className = `index-item ${isActive ? "index-item--active" : ""} ${isEmpty ? "index-item--empty" : ""} fade-in`;
  item.setAttribute("role", "button");
  item.setAttribute("tabindex", "0");
  item.setAttribute("aria-label", `Select index: ${cleanName}`);

  item.innerHTML = `
    <div class="index-item__icon">
      <svg width="14" height="14" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
        <path stroke-linecap="round" stroke-linejoin="round" d="M20 7l-8-4-8 4m16 0l-8 4m8-4v10l-8 4m0-10L4 7m8 4v10M4 7v10l8 4"/>
      </svg>
    </div>
    <div class="index-item__body">
      <span class="index-item__name" title="${cleanUrl}">${cleanName}</span>
      <span class="index-item__meta">
        ${isEmpty
          ? `<span class="badge badge--warn">Empty</span>`
          : `<span class="badge badge--info">${chunkCount} chunks</span>`
        }
        <span class="index-item__date">${cleanCreated}</span>
      </span>
    </div>
    <button class="btn-icon btn-delete" title="Delete index" aria-label="Delete index">
      <svg width="13" height="13" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
        <polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6m3 0V4a2 2 0 012-2h4a2 2 0 012 2v2"/>
        <line x1="10" y1="11" x2="10" y2="17"/><line x1="14" y1="11" x2="14" y2="17"/>
      </svg>
    </button>
  `;

  item.addEventListener("click", (e) => {
    if ((e.target as Element).closest(".btn-delete")) return;
    setActiveIndex(idx.index_id, idx.repo_url, chunkCount);
  });

  item.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      setActiveIndex(idx.index_id, idx.repo_url, chunkCount);
    }
  });

  const delBtn = item.querySelector<HTMLButtonElement>(".btn-delete")!;
  delBtn.addEventListener("click", async (e) => {
    e.stopPropagation();
    await deleteIndex(idx.index_id);
  });

  return item;
}

function setActiveIndex(indexId: string, repoUrl: string, chunkCount: number): void {
  if (chunkCount === 0) {
    appendMessage("system", "⚠️ This index is empty (0 chunks) and cannot be used for verification. Delete and re-index the repository.");
    return;
  }

  currentIndexId = indexId;
  conversationHistory = [];

  // Update sidebar active state
  document.querySelectorAll(".index-item").forEach(el => el.classList.remove("index-item--active"));
  const items = Array.from(document.querySelectorAll<HTMLElement>(".index-item"));
  const match = items.find(el => el.querySelector(".index-item__name")?.getAttribute("title") === DOMPurify.sanitize(repoUrl));
  if (match) match.classList.add("index-item--active");

  // Update current-index display
  let displayName = repoUrl;
  try {
    const parts = new URL(displayName).pathname.split("/").filter(Boolean);
    if (parts.length >= 2) displayName = `${parts[parts.length - 2]}/${parts[parts.length - 1]}`;
  } catch { /* ignore */ }

  const infoEl = document.getElementById("current-index-display");
  if (infoEl) {
    infoEl.innerHTML = `
      <span class="current-index__label">Active Index</span>
      <span class="current-index__name">${DOMPurify.sanitize(displayName)}</span>
    `;
    infoEl.classList.add("current-index--active");
  }

  // Enable chat input
  const chatInput = document.getElementById("chat-input") as HTMLInputElement | null;
  const sendBtn = document.getElementById("send-btn") as HTMLButtonElement | null;
  if (chatInput) { chatInput.disabled = false; chatInput.placeholder = "Enter a claim to verify…"; }
  if (sendBtn) sendBtn.disabled = false;

  const safeName: string = DOMPurify.sanitize(displayName);
  appendMessage("system", `✓ Index activated — you can now verify claims about **${safeName}**.`);
  showToast(`Index activated: ${displayName}`, "success");
}

async function deleteIndex(indexId: string): Promise<void> {
  const dialog = document.getElementById("confirm-dialog");
  if (!dialog) {
    if (!confirm("Delete this index? This cannot be undone.")) return;
    await doDelete(indexId);
    return;
  }

  // Use custom confirm dialog
  dialog.classList.add("dialog--visible");
  const confirmBtn = dialog.querySelector<HTMLButtonElement>("#dialog-confirm")!;
  const cancelBtn = dialog.querySelector<HTMLButtonElement>("#dialog-cancel")!;

  const cleanup = () => dialog.classList.remove("dialog--visible");

  confirmBtn.onclick = async () => {
    cleanup();
    await doDelete(indexId);
  };
  cancelBtn.onclick = cleanup;
}

async function doDelete(indexId: string): Promise<void> {
  try {
    const res = await apiFetch(`/index-repository/${indexId}`, { method: "DELETE" });
    if (res.ok) {
      if (currentIndexId === indexId) {
        currentIndexId = null;
        conversationHistory = [];
        const infoEl = document.getElementById("current-index-display");
        if (infoEl) {
          infoEl.innerHTML = `<span class="current-index__empty">No active index</span>`;
          infoEl.classList.remove("current-index--active");
        }
        const chatInput = document.getElementById("chat-input") as HTMLInputElement | null;
        const sendBtn = document.getElementById("send-btn") as HTMLButtonElement | null;
        if (chatInput) { chatInput.disabled = true; chatInput.placeholder = "Select an index to verify claims…"; }
        if (sendBtn) sendBtn.disabled = true;
      }
      await loadIndexes();
      showToast("Index deleted", "info");
    } else {
      const err = await res.json();
      showToast("Delete failed: " + (err.detail || "Unknown error"), "error");
    }
  } catch (err) {
    showToast("Error deleting index", "error");
  }
}

// ─── Indexing ─────────────────────────────────────────────────────────────────

function setIndexingState(processing: boolean, message: string, isError = false): void {
  const btn = document.getElementById("index-btn") as HTMLButtonElement | null;
  const input = document.getElementById("repo-url") as HTMLInputElement | null;
  const statusEl = document.getElementById("index-status");
  const spinner = document.getElementById("index-spinner");
  const statusText = document.getElementById("index-status-text");

  if (btn) btn.disabled = processing;
  if (input) input.disabled = processing;

  if (statusEl) {
    statusEl.className = `index-status ${isError ? "index-status--error" : processing ? "index-status--processing" : "index-status--success"}`;
    statusEl.style.display = message ? "flex" : "none";
  }
  if (spinner) spinner.style.display = processing ? "block" : "none";
  if (statusText) statusText.textContent = message;
}

async function pollStatus(indexId: string, repoUrl: string, retries = 0): Promise<void> {
  const MAX_RETRIES = 5;
  try {
    const res = await apiFetch(`/index-repository/${indexId}`);

    if (res.status === 401) {
      setIndexingState(false, "Authentication required: Missing or invalid API key", true);
      showToast("Authentication required — check API key in settings", "error");
      return;
    }

    if (res.status === 404) {
      if (retries < MAX_RETRIES) {
        setTimeout(() => pollStatus(indexId, repoUrl, retries + 1), 2000);
        return;
      }
      setIndexingState(false, "Indexing job not found", true);
      showToast("Indexing job not found", "error");
      return;
    }

    if (!res.ok) {
      const errData = await res.json().catch(() => ({}));
      const errMsg = errData.detail || errData.message || `Server error (${res.status})`;
      if (retries < MAX_RETRIES) {
        setTimeout(() => pollStatus(indexId, repoUrl, retries + 1), 3000);
        return;
      }
      setIndexingState(false, `Error polling status: ${errMsg}`, true);
      showToast("Error polling indexing status", "error");
      return;
    }

    const data: IndexJobStatus = await res.json();

    if (data.status === "processing") {
      setIndexingState(true, "Parsing files, building call graphs & generating embeddings…");
      setTimeout(() => pollStatus(indexId, repoUrl, 0), 2500);
    } else if (data.status === "completed") {
      setIndexingState(false, "Indexing complete!");
      showToast("Repository indexing completed!", "success");
      await loadIndexes();
      // Auto-activate the new index after loading
      const listRes = await apiFetch("/indexes");
      const allIndexes: IndexEntry[] = listRes.ok ? await listRes.json() : [];
      const entry = allIndexes.find(i => i.index_id === indexId);
      if (entry) setActiveIndex(indexId, repoUrl, entry.vector_count);
    } else if (data.status === "failed") {
      const errorDetail = data.error || "Unknown error during indexing";
      setIndexingState(false, `Failed: ${errorDetail}`, true);
      showToast(`Indexing failed: ${errorDetail}`, "error");
    } else {
      if (retries < MAX_RETRIES) {
        setTimeout(() => pollStatus(indexId, repoUrl, retries + 1), 2500);
      } else {
        setIndexingState(false, "Received invalid status response from server", true);
      }
    }
  } catch (err) {
    if (retries < MAX_RETRIES) {
      // Retry transient network connection errors up to 5 times
      setTimeout(() => pollStatus(indexId, repoUrl, retries + 1), 3000);
    } else {
      const errMsg = err instanceof Error ? err.message : "Network disconnected";
      setIndexingState(false, `Polling failed: ${errMsg}`, true);
      showToast("Network error while polling status", "error");
    }
  }
}

// ─── Message Helpers ──────────────────────────────────────────────────────────

function createMessageEl(role: "user" | "assistant" | "system"): HTMLElement {
  const div = document.createElement("div");
  div.className = `message message--${role} fade-in`;
  div.innerHTML = `<div class="message__content"></div>`;
  return div;
}

function appendMessage(role: "user" | "assistant" | "system", text: string): HTMLElement {
  const chatHistory = document.getElementById("chat-history")!;
  const el = createMessageEl(role);
  const content = el.querySelector<HTMLElement>(".message__content")!;

  if (role === "user") {
    content.textContent = text;
  } else if (role === "system") {
    content.innerHTML = DOMPurify.sanitize(marked.parse(text));
  } else {
    content.innerHTML = DOMPurify.sanitize(marked.parse(text));
  }

  chatHistory.appendChild(el);
  scrollToBottom();
  return el;
}

function scrollToBottom(): void {
  const h = document.getElementById("chat-history");
  if (h) h.scrollTop = h.scrollHeight;
}

// ─── Verification Renderer ────────────────────────────────────────────────────

function renderOnboardingGuide(container: HTMLElement): void {
  container.innerHTML = `
    <div class="guide-card">
      <div class="guide-card__header">
        <div class="guide-card__icon">
          <svg width="20" height="20" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
            <path stroke-linecap="round" stroke-linejoin="round" d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z"/>
          </svg>
        </div>
        <h3 class="guide-card__title">RepoLens — Evidence Verification</h3>
      </div>
      <p class="guide-card__desc">
        RepoLens is <strong>not a chatbot</strong>. It's an engineering verification platform that evaluates 
        specific technical claims against your codebase using real evidence — exact file paths, line numbers, and code snippets.
      </p>
      <div class="guide-examples">
        <div class="guide-examples__section guide-examples__section--good">
          <p class="guide-examples__label">✅ Valid claims to verify</p>
          <ul>
            <li>The rate limiter uses an in-memory store to track request counts</li>
            <li>JWT tokens are validated on every authenticated route</li>
            <li>Database queries are protected against SQL injection</li>
            <li>The embedding service batches API calls to avoid rate limits</li>
          </ul>
        </div>
        <div class="guide-examples__section guide-examples__section--bad">
          <p class="guide-examples__label">❌ Not supported</p>
          <ul>
            <li>Explaining code ("What does this function do?")</li>
            <li>Summarising the codebase</li>
            <li>Open-ended questions ("How does X work?")</li>
          </ul>
        </div>
      </div>
      <p class="guide-card__tip">
        <strong>Tip:</strong> Frame your input as a specific, testable statement — not a question.
      </p>
    </div>
  `;
}

function renderEvidenceCard(item: EvidenceItem, type: "supporting" | "contradicting"): string {
  const cleanPath = DOMPurify.sanitize(item.file_path);
  const cleanRange = DOMPurify.sanitize(item.line_range);
  const cleanSymbol = DOMPurify.sanitize(item.symbol_name);
  const cleanRelevance = DOMPurify.sanitize(item.relevance);
  const cleanSnippet = DOMPurify.sanitize(item.snippet);

  return `
    <div class="evidence-card evidence-card--${type}">
      <div class="evidence-card__meta">
        <div class="evidence-card__location">
          <svg width="12" height="12" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
            <path stroke-linecap="round" stroke-linejoin="round" d="M10 20l4-16m4 4l4 4-4 4M6 16l-4-4 4-4"/>
          </svg>
          <code class="evidence-card__path">${cleanPath}</code>
          <span class="evidence-card__range">${cleanRange}</span>
        </div>
        <span class="evidence-card__symbol">${cleanSymbol}</span>
      </div>
      <p class="evidence-card__relevance">${cleanRelevance}</p>
      <pre class="evidence-card__snippet"><code class="language-python">${cleanSnippet}</code></pre>
    </div>
  `;
}

function renderVerificationReport(report: VerificationReport, container: HTMLElement): void {
  const statusConfig: Record<string, { cls: string; label: string; icon: string }> = {
    "Likely True":  { cls: "verdict--true",    label: "Likely True",  icon: "✓" },
    "Likely False": { cls: "verdict--false",   label: "Likely False", icon: "✗" },
    "Uncertain":    { cls: "verdict--uncertain", label: "Uncertain",   icon: "~" },
  };
  const cfg = statusConfig[report.verification_status] ?? statusConfig["Uncertain"];
  const score = Math.round(Math.max(0, Math.min(100, report.confidence_score)));

  let html = `
    <div class="report">
      <div class="report__header">
        <div class="report__verdict ${cfg.cls}">
          <span class="report__verdict-icon">${cfg.icon}</span>
          <span class="report__verdict-label">${DOMPurify.sanitize(cfg.label)}</span>
        </div>
        <div class="report__score">
          <span class="report__score-value">${score}%</span>
          <span class="report__score-label">confidence</span>
        </div>
      </div>

      <div class="report__confidence-bar">
        <div class="report__confidence-fill ${cfg.cls}" style="width: ${score}%"></div>
      </div>
  `;

  if (report.atomic_hypotheses?.length > 0) {
    html += `<div class="report__section">
      <h4 class="report__section-title">Atomic Hypotheses</h4>
      <div class="hypotheses">`;
    report.atomic_hypotheses.forEach(h => {
      const ok = h.status === "VERIFIED";
      html += `<div class="hypothesis hypothesis--${ok ? "verified" : "unverified"}">
        <span class="hypothesis__icon">${ok ? "✓" : "?"}</span>
        <span class="hypothesis__text">${DOMPurify.sanitize(h.statement)}</span>
      </div>`;
    });
    html += `</div></div>`;
  }

  if (report.supporting_evidence?.length > 0) {
    html += `<div class="report__section">
      <h4 class="report__section-title report__section-title--success">
        <svg width="14" height="14" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg>
        Supporting Evidence (${report.supporting_evidence.length})
      </h4>
      ${report.supporting_evidence.map(e => renderEvidenceCard(e, "supporting")).join("")}
    </div>`;
  }

  if (report.contradicting_evidence?.length > 0) {
    html += `<div class="report__section">
      <h4 class="report__section-title report__section-title--danger">
        <svg width="14" height="14" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2.5"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
        Contradicting Evidence (${report.contradicting_evidence.length})
      </h4>
      ${report.contradicting_evidence.map(e => renderEvidenceCard(e, "contradicting")).join("")}
    </div>`;
  }

  if (report.potential_risks?.length > 0) {
    html += `<div class="report__section">
      <h4 class="report__section-title report__section-title--warn">
        <svg width="14" height="14" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"/></svg>
        Potential Risks
      </h4>
      <ul class="report__list report__list--warn">
        ${report.potential_risks.map(r => `<li>${DOMPurify.sanitize(r)}</li>`).join("")}
      </ul>
    </div>`;
  }

  if (report.missing_information?.length > 0) {
    html += `<div class="report__section">
      <h4 class="report__section-title report__section-title--muted">Missing Information</h4>
      <ul class="report__list">
        ${report.missing_information.map(m => `<li>${DOMPurify.sanitize(m)}</li>`).join("")}
      </ul>
    </div>`;
  }

  if (report.recommended_tests?.length > 0) {
    html += `<div class="report__section">
      <h4 class="report__section-title report__section-title--primary">
        <svg width="14" height="14" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2"/></svg>
        Recommended Tests
      </h4>
      <ul class="report__list">
        ${report.recommended_tests.map(t => `<li><strong>${DOMPurify.sanitize(t.test_type)}:</strong> ${DOMPurify.sanitize(t.description)}</li>`).join("")}
      </ul>
    </div>`;
  }

  html += `</div>`;
  container.innerHTML = html;
  container.querySelectorAll<HTMLElement>("pre code").forEach(block => hljs.highlightElement(block));
}

// ─── Chat / Verification ─────────────────────────────────────────────────────

async function handleVerification(claim: string): Promise<void> {
  if (!currentIndexId || isVerifying) return;
  isVerifying = true;

  const chatInput = document.getElementById("chat-input") as HTMLInputElement;
  const sendBtn = document.getElementById("send-btn") as HTMLButtonElement;
  chatInput.disabled = true;
  sendBtn.disabled = true;

  // User bubble
  appendMessage("user", claim);

  // Assistant loading bubble
  const responseEl = createMessageEl("assistant");
  const contentEl = responseEl.querySelector<HTMLElement>(".message__content")!;
  document.getElementById("chat-history")!.appendChild(responseEl);
  contentEl.innerHTML = `
    <div class="verifying-state">
      <span class="mini-spinner"></span>
      <span>Extracting claims, building call graphs, collecting evidence…</span>
    </div>`;
  scrollToBottom();

  try {
    const res = await apiFetch("/verify", {
      method: "POST",
      body: JSON.stringify({ index_id: currentIndexId, claim }),
    });

    const data = await res.json();

    if (res.status === 422) {
      const detail = data.detail;
      const isClaimError = Array.isArray(detail) && detail.some((d: { loc?: string[] }) => d.loc?.includes("claim"));
      if (isClaimError) {
        renderOnboardingGuide(contentEl);
      } else {
        const msg = Array.isArray(detail) ? detail.map((d: { msg: string }) => d.msg).join(" | ") : String(detail);
        contentEl.innerHTML = `<p class="error-text">Validation Error: ${DOMPurify.sanitize(msg)}</p>`;
      }
      return;
    }

    if (!res.ok) {
      throw new Error(data.detail || "Verification failed");
    }

    renderVerificationReport(data as VerificationReport, contentEl);
  } catch (err) {
    const msg = err instanceof Error ? err.message : "Unknown error";
    contentEl.innerHTML = `<p class="error-text">Verification Error: ${DOMPurify.sanitize(msg)}</p>`;
    showToast("Verification failed", "error");
  } finally {
    isVerifying = false;
    chatInput.disabled = false;
    sendBtn.disabled = false;
    chatInput.focus();
    scrollToBottom();
  }
}

// ─── Welcome Message ──────────────────────────────────────────────────────────

function showWelcomeMessage(): void {
  const chatHistory = document.getElementById("chat-history")!;
  const el = createMessageEl("assistant");
  const content = el.querySelector<HTMLElement>(".message__content")!;
  chatHistory.appendChild(el);
  content.innerHTML = `
    <div class="welcome-card">
      <div class="welcome-card__brand">
        <div class="welcome-card__logo">
          <svg width="22" height="22" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.8">
            <path stroke-linecap="round" stroke-linejoin="round" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"/>
          </svg>
        </div>
        <h2 class="welcome-card__title">RepoLens</h2>
      </div>
      <p class="welcome-card__tagline"><em>"Don't explain code. Verify claims about code."</em></p>
      <p class="welcome-card__desc">
        An evidence-based verification platform. Index a repository on the left, then submit a 
        specific, testable technical claim to receive a structured verdict with cited code evidence.
      </p>
      <div class="welcome-steps">
        <div class="welcome-step">
          <span class="welcome-step__num">1</span>
          <span class="welcome-step__text">Paste a GitHub URL and click <strong>Index</strong></span>
        </div>
        <div class="welcome-step">
          <span class="welcome-step__num">2</span>
          <span class="welcome-step__text">Wait for indexing — chunks appear in the sidebar</span>
        </div>
        <div class="welcome-step">
          <span class="welcome-step__num">3</span>
          <span class="welcome-step__text">Submit a claim to verify and receive a verdict with citations</span>
        </div>
      </div>
      <div class="welcome-examples">
        <p class="welcome-examples__label">Example claims</p>
        <div class="example-chips">
          <button class="chip" data-claim="The auth middleware validates API keys on every protected route">Auth middleware validates API keys</button>
          <button class="chip" data-claim="Database queries use parameterised statements to prevent SQL injection">SQL injection prevention</button>
          <button class="chip" data-claim="The rate limiter tracks requests per IP using an in-memory store">Rate limiter tracks per-IP</button>
          <button class="chip" data-claim="All secrets are loaded from environment variables, not hardcoded">Secrets in env vars</button>
        </div>
      </div>
    </div>
  `;

  // Chip click → populate input
  content.querySelectorAll<HTMLButtonElement>(".chip").forEach(chip => {
    chip.addEventListener("click", () => {
      const input = document.getElementById("chat-input") as HTMLInputElement | null;
      if (input && !input.disabled) {
        input.value = chip.dataset.claim ?? "";
        input.focus();
      } else {
        showToast("Select an index first to verify claims", "info");
      }
    });
  });

  scrollToBottom();
}

// ─── Keyboard Shortcuts ───────────────────────────────────────────────────────

document.addEventListener("keydown", (e: KeyboardEvent) => {
  if ((e.ctrlKey || e.metaKey) && e.key === "k") {
    e.preventDefault();
    const chatInput = document.getElementById("chat-input") as HTMLInputElement | null;
    if (chatInput && !chatInput.disabled) chatInput.focus();
    else showToast("Select an index to start verifying", "info");
  }
  if (e.key === "Escape") {
    const chatInput = document.getElementById("chat-input") as HTMLInputElement | null;
    if (chatInput === document.activeElement) chatInput?.blur();
  }
});

// ─── Init ─────────────────────────────────────────────────────────────────────

document.addEventListener("DOMContentLoaded", () => {
  // API Key input setup
  const apiKeyInput = document.getElementById("api-key-input") as HTMLInputElement | null;
  if (apiKeyInput) {
    apiKeyInput.value = localStorage.getItem("repolens_api_key") || "";
    apiKeyInput.addEventListener("change", () => {
      const val = apiKeyInput.value.trim();
      if (val) {
        localStorage.setItem("repolens_api_key", val);
        showToast("API Key saved", "success");
      } else {
        localStorage.removeItem("repolens_api_key");
        showToast("API Key removed", "info");
      }
    });
  }

  // Index button
  const indexBtn = document.getElementById("index-btn") as HTMLButtonElement | null;
  const repoInput = document.getElementById("repo-url") as HTMLInputElement | null;
  indexBtn?.addEventListener("click", async () => {
    const url = repoInput?.value.trim() ?? "";
    if (!url) { showToast("Enter a GitHub repository URL", "error"); return; }

    setIndexingState(true, "Connecting to repository…");
    try {
      const res = await apiFetch("/index-repository", {
        method: "POST",
        body: JSON.stringify({ repo_url: url }),
      });
      const data: IndexStartResponse = await res.json();
      if (!res.ok) throw new Error((data as unknown as { detail: string }).detail || "Failed to start indexing");
      pollStatus(data.index_id, url);
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Unknown error";
      setIndexingState(false, msg, true);
      showToast(msg, "error");
    }
  });

  repoInput?.addEventListener("keydown", (e) => {
    if (e.key === "Enter") indexBtn?.click();
  });

  // Chat form
  const chatForm = document.getElementById("chat-form") as HTMLFormElement | null;
  chatForm?.addEventListener("submit", (e) => {
    e.preventDefault();
    const chatInput = document.getElementById("chat-input") as HTMLInputElement | null;
    const claim = chatInput?.value.trim() ?? "";
    if (!claim || !currentIndexId) return;
    if (chatInput) chatInput.value = "";
    handleVerification(claim);
  });

  // Settings toggle
  const settingsBtn = document.getElementById("settings-toggle-btn");
  const settingsContent = document.getElementById("settings-content");
  settingsBtn?.addEventListener("click", () => {
    const open = settingsBtn.classList.toggle("open");
    settingsContent?.classList.toggle("hidden", !open);
  });

  // Confirm dialog close on backdrop click
  const dialog = document.getElementById("confirm-dialog");
  dialog?.addEventListener("click", (e) => {
    if (e.target === dialog) dialog.classList.remove("dialog--visible");
  });

  // Health check
  checkHealth();
  setInterval(checkHealth, 30_000);

  // Load indexes
  loadIndexes();

  // Show welcome
  showWelcomeMessage();
});
