marked.setOptions({
    highlight: function(code, lang) {
        if (lang && hljs.getLanguage(lang)) {
            return hljs.highlight(code, { language: lang }).value;
        }
        return hljs.highlightAuto(code).value;
    }
});

let currentIndexId = null;
let conversationHistory = [];

const elements = {
    repoUrl: document.getElementById('repo-url'),
    indexBtn: document.getElementById('index-btn'),
    indexStatus: document.getElementById('index-status'),
    statusText: document.getElementById('status-text'),
    spinner: document.querySelector('.spinner'),
    currentIndexInfo: document.getElementById('current-index-info'),
    chatHistory: document.getElementById('chat-history'),
    chatForm: document.getElementById('chat-form'),
    chatInput: document.getElementById('chat-input'),
    sendBtn: document.getElementById('send-btn')
};

function getFetchHeaders(headers = {}) {
    const key = localStorage.getItem('repolens_api_key');
    if (key) {
        headers['X-API-Key'] = key;
    }
    return headers;
}

// --- Indexing Logic ---
elements.indexBtn.addEventListener('click', async () => {
    const url = elements.repoUrl.value.trim();
    if (!url) return;

    setIndexingState(true, 'Starting...');

    try {
        const res = await fetch('/index-repository', {
            method: 'POST',
            headers: getFetchHeaders({ 'Content-Type': 'application/json' }),
            body: JSON.stringify({ repo_url: url })
        });
        
        const data = await res.json();
        
        if (!res.ok) {
            throw new Error(data.detail || 'Failed to start indexing');
        }

        pollStatus(data.index_id, url);
    } catch (err) {
        setIndexingState(false, err.message, true);
    }
});

function setIndexingState(isProcessing, message, isError = false) {
    elements.indexBtn.disabled = isProcessing;
    elements.repoUrl.disabled = isProcessing;
    elements.indexStatus.className = `status-message ${isError ? 'error' : (isProcessing ? '' : 'success')}`;
    elements.spinner.style.display = isProcessing ? 'block' : 'none';
    elements.statusText.textContent = message;
}

async function pollStatus(indexId, repoUrl) {
    try {
        const res = await fetch(`/index-repository/${indexId}`, {
            headers: getFetchHeaders()
        });
        const data = await res.json();
        
        if (data.status === 'processing') {
            setIndexingState(true, 'Processing repository...');
            setTimeout(() => pollStatus(indexId, repoUrl), 2000);
        } else if (data.status === 'completed') {
            setIndexingState(false, 'Indexing complete!');
            // Pass 1 as placeholder — loadIndexes() will refresh and show real count
            setActiveIndex(indexId, repoUrl, 1);
            loadIndexes();
        } else if (data.status === 'failed') {
            setIndexingState(false, `Failed: ${data.error || 'Unknown error'}`, true);
        }
    } catch (err) {
        setIndexingState(false, 'Error polling status', true);
    }
}

function setActiveIndex(indexId, repoUrl, chunkCount) {
    // BUG-7 FIX: Don't activate 0-chunk indexes for verification — they will always
    // return "No code chunks found in index." and confuse the user.
    if (chunkCount === 0) {
        const emptyMsg = `⚠️ This index is empty (0 chunks). It cannot be used for verification. Please delete it and re-index the repository.`;
        // Show warning in chat area without setting it as active
        const msgEl = createMessageElement('system');
        msgEl.querySelector('.message-content').textContent = emptyMsg;
        elements.chatHistory.appendChild(msgEl);
        scrollToBottom();
        return;
    }

    currentIndexId = indexId;
    conversationHistory = [];
    elements.currentIndexInfo.className = 'index-info';
    const cleanIndexId = DOMPurify.sanitize(indexId);
    const cleanRepoUrl = DOMPurify.sanitize(repoUrl);
    elements.currentIndexInfo.innerHTML = `<strong>ID:</strong> ${cleanIndexId}<br><strong>Repo:</strong> ${cleanRepoUrl}`;
    
    elements.chatInput.disabled = false;
    elements.sendBtn.disabled = false;
    
    addMessage('system', `Repository indexed! You can now verify claims about it.`);
    
    // Highlight the active item in the list
    document.querySelectorAll('.index-item').forEach(el => el.classList.remove('active'));
    const activeItem = Array.from(document.querySelectorAll('.index-item')).find(el => {
        const repoEl = el.querySelector('.repo-name');
        return (repoEl && repoEl.title === repoUrl) || el.innerHTML.includes(indexId);
    });
    if (activeItem) {
        activeItem.classList.add('active');
    }
}

// --- Chat Logic ---
elements.chatForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    if (!currentIndexId) return;
    
    const question = elements.chatInput.value.trim();
    if (!question) return;
    
    elements.chatInput.value = '';
    elements.chatInput.disabled = true;
    elements.sendBtn.disabled = true;
    
    addMessage('user', `Claim Verification: "${question}"`);
    
    const messageEl = createMessageElement('assistant');
    const contentEl = messageEl.querySelector('.message-content');
    elements.chatHistory.appendChild(messageEl);
    contentEl.innerHTML = `<p>🔍 <em>Extracting claims, building call graphs, and collecting evidence...</em></p>`;
    
    try {
        const res = await fetch('/verify', {
            method: 'POST',
            headers: getFetchHeaders({ 'Content-Type': 'application/json' }),
            body: JSON.stringify({ index_id: currentIndexId, claim: question })
        });

        const data = await res.json();

        // Detect chatbot-style rejection (422 claim validation error)
        if (res.status === 422) {
            const detail = data.detail;
            // Check if it's the claim-type validator error
            const isClaimError = Array.isArray(detail)
                ? detail.some(d => d.loc && d.loc.includes('claim'))
                : false;
            if (isClaimError) {
                renderOnboardingGuide(contentEl);
                return;
            }
            // Generic 422
            const msg = Array.isArray(detail)
                ? detail.map(d => d.msg).join(' | ')
                : String(detail);
            throw new Error(msg);
        }

        if (!res.ok) {
            throw new Error(data.detail || 'Verification request failed');
        }

        renderVerificationReport(data, contentEl);
    } catch (err) {
        const cleanErrMessage = DOMPurify.sanitize(err.message);
        contentEl.innerHTML = `<p style="color: var(--danger)">Verification Error: ${cleanErrMessage}</p>`;
    } finally {
        elements.chatInput.disabled = false;
        elements.sendBtn.disabled = false;
        elements.chatInput.focus();
        scrollToBottom();
    }
});

/**
 * Renders a rich onboarding card when the user submits a chatbot-style prompt.
 * Explains what RepoLens is and shows example claims.
 */
function renderOnboardingGuide(containerEl) {
    containerEl.innerHTML = `
        <div style="border: 1px solid #3b82f6; border-radius: 10px; padding: 20px; background: rgba(59,130,246,0.06);">
            <div style="display:flex; align-items:center; gap:10px; margin-bottom:14px;">
                <span style="font-size:1.5rem;">🔬</span>
                <h3 style="margin:0; color:#60a5fa; font-size:1.05rem;">RepoLens — Evidence-Based Claim Verification</h3>
            </div>

            <p style="margin:0 0 12px; color:#d1d5db; line-height:1.6;">
                RepoLens is <strong>not a chatbot</strong>. It is an <strong>engineering verification platform</strong>.
                Instead of explaining or summarising code, it <em>verifies specific claims about the codebase</em>
                using real evidence — exact file paths, line numbers, and code snippets.
            </p>

            <div style="background:rgba(255,255,255,0.04); border-radius:8px; padding:14px; margin-bottom:14px;">
                <p style="margin:0 0 8px; color:#9ca3af; font-size:0.78rem; text-transform:uppercase; letter-spacing:0.05em;">✅ Good claims to verify</p>
                <ul style="margin:0; padding-left:18px; color:#d1d5db; line-height:1.9; font-size:0.92rem;">
                    <li>The rate limiter uses Redis to track request counts</li>
                    <li>JWT tokens are validated on every authenticated route</li>
                    <li>Database queries are protected against SQL injection</li>
                    <li>The embedding service batches API calls to avoid rate limits</li>
                    <li>CloneService sanitises the repository URL before cloning</li>
                </ul>
            </div>

            <div style="background:rgba(239,68,68,0.06); border-radius:8px; padding:14px; margin-bottom:14px;">
                <p style="margin:0 0 8px; color:#9ca3af; font-size:0.78rem; text-transform:uppercase; letter-spacing:0.05em;">❌ What RepoLens is not designed for</p>
                <ul style="margin:0; padding-left:18px; color:#d1d5db; line-height:1.9; font-size:0.92rem;">
                    <li>Explaining code in plain English ("What does this do?")</li>
                    <li>Summarising the repository for a new engineer</li>
                    <li>Answering open-ended questions ("How does X work?")</li>
                    <li>Writing or generating new code</li>
                </ul>
            </div>

            <p style="margin:0; color:#6b7280; font-size:0.88rem;">
                💡 <strong>Tip:</strong> Frame your input as a specific, testable statement — not a question.
                RepoLens will find the code, evaluate the claim, and return a verdict with citations.
            </p>
        </div>
    `;
}

function renderVerificationReport(report, containerEl) {
    const statusColor = report.verification_status === 'Likely True' ? '#10b981' : (report.verification_status === 'Likely False' ? '#ef4444' : '#f59e0b');
    
    let html = `
        <div style="border: 1px solid var(--border-color); border-radius: 8px; padding: 16px; margin-bottom: 12px; background: rgba(255,255,255,0.03);">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px;">
                <h3 style="margin: 0; font-size: 1.1rem; color: #fff;">Verification Verdict</h3>
                <span style="background: ${statusColor}; color: #000; font-weight: 700; padding: 4px 12px; border-radius: 12px; font-size: 0.85rem;">
                    ${DOMPurify.sanitize(report.verification_status)}
                </span>
            </div>

            <p style="margin-bottom: 8px;"><strong>Confidence Score:</strong> ${report.confidence_score}%</p>
            <div style="width: 100%; background: #333; height: 8px; border-radius: 4px; overflow: hidden; margin-bottom: 16px;">
                <div style="width: ${report.confidence_score}%; background: ${statusColor}; height: 100%;"></div>
            </div>
    `;

    if (report.supporting_evidence && report.supporting_evidence.length > 0) {
        html += `<h4 style="color: #10b981; margin-top: 12px;">Supporting Evidence</h4><ul>`;
        report.supporting_evidence.forEach(item => {
            html += `<li><strong>[${DOMPurify.sanitize(item.file_path)} #${DOMPurify.sanitize(item.line_range)}]</strong> <code>${DOMPurify.sanitize(item.symbol_name)}</code>: ${DOMPurify.sanitize(item.relevance)}<br><pre><code class="language-python">${DOMPurify.sanitize(item.snippet)}</code></pre></li>`;
        });
        html += `</ul>`;
    }

    if (report.contradicting_evidence && report.contradicting_evidence.length > 0) {
        html += `<h4 style="color: #ef4444; margin-top: 12px;">Contradicting Evidence</h4><ul>`;
        report.contradicting_evidence.forEach(item => {
            html += `<li><strong>[${DOMPurify.sanitize(item.file_path)} #${DOMPurify.sanitize(item.line_range)}]</strong> <code>${DOMPurify.sanitize(item.symbol_name)}</code>: ${DOMPurify.sanitize(item.relevance)}<br><pre><code class="language-python">${DOMPurify.sanitize(item.snippet)}</code></pre></li>`;
        });
        html += `</ul>`;
    }

    if (report.potential_risks && report.potential_risks.length > 0) {
        html += `<h4 style="color: #f59e0b; margin-top: 12px;">Potential Risks</h4><ul>`;
        report.potential_risks.forEach(risk => {
            html += `<li>${DOMPurify.sanitize(risk)}</li>`;
        });
        html += `</ul>`;
    }

    if (report.missing_information && report.missing_information.length > 0) {
        html += `<h4 style="color: #9ca3af; margin-top: 12px;">Missing Information / Gaps</h4><ul>`;
        report.missing_information.forEach(info => {
            html += `<li>${DOMPurify.sanitize(info)}</li>`;
        });
        html += `</ul>`;
    }

    if (report.recommended_tests && report.recommended_tests.length > 0) {
        html += `<h4 style="color: #60a5fa; margin-top: 12px;">Recommended Tests</h4><ul>`;
        report.recommended_tests.forEach(test => {
            html += `<li><strong>${DOMPurify.sanitize(test.test_type)}:</strong> ${DOMPurify.sanitize(test.description)}</li>`;
        });
        html += `</ul>`;
    }

    html += `</div>`;
    containerEl.innerHTML = html;
    
    // Highlight code blocks
    containerEl.querySelectorAll('pre code').forEach((block) => {
        hljs.highlightElement(block);
    });
}

function createMessageElement(role) {
    const div = document.createElement('div');
    div.className = `message ${role}`;
    div.innerHTML = `<div class="message-content"></div>`;
    return div;
}

function addMessage(role, text) {
    const el = createMessageElement(role);
    if (role === 'user' || role === 'system') {
        el.querySelector('.message-content').textContent = text;
    } else {
        el.querySelector('.message-content').innerHTML = DOMPurify.sanitize(marked.parse(text));
    }
    elements.chatHistory.appendChild(el);
    scrollToBottom();
}

function scrollToBottom() {
    elements.chatHistory.scrollTop = elements.chatHistory.scrollHeight;
}

async function streamAnswer(indexId, question, contentEl) {
    const response = await fetch('/ask/stream', {
        method: 'POST',
        headers: getFetchHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({ index_id: indexId, question: question, history: conversationHistory })
    });
    
    if (!response.ok) {
        throw new Error('Failed to connect to stream endpoint');
    }
    
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let markdownContent = '';
    
    while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n\n');
        buffer = lines.pop(); // keep incomplete chunk
        
        for (const chunk of lines) {
            const eventMatch = chunk.match(/event: (.*)/);
            const dataMatch = chunk.match(/data: (.*)/);
            
            if (eventMatch && dataMatch) {
                const eventName = eventMatch[1];
                const eventData = JSON.parse(dataMatch[1]);
                
                if (eventName === 'token') {
                    markdownContent += eventData.text;
                    contentEl.innerHTML = DOMPurify.sanitize(marked.parse(markdownContent));
                    scrollToBottom();
                } else if (eventName === 'error') {
                    throw new Error(eventData.message);
                } else if (eventName === 'sources') {
                    appendSources(contentEl, eventData);
                    scrollToBottom();
                }
            }
        }
    }

    conversationHistory.push({ role: 'user', content: question });
    conversationHistory.push({ role: 'assistant', content: markdownContent });
    if (conversationHistory.length > 12) {
        conversationHistory = conversationHistory.slice(-12);
    }
}

function appendSources(contentEl, sources) {
    if (!sources || sources.length === 0) return;
    
    const sourcesHtml = sources.map(s => {
        const cleanFilePath = DOMPurify.sanitize(s.file_path);
        const cleanSymbolName = DOMPurify.sanitize(s.symbol_name);
        return `<div class="source-item">
            <span class="file">${cleanFilePath}</span>
            <span class="symbol">${cleanSymbolName}</span>
        </div>`;
    }).join('');
    
    const containerHtml = `
        <div class="sources-container">
            <h4>Sources</h4>
            ${sourcesHtml}
        </div>
    `;
    
    contentEl.insertAdjacentHTML('beforeend', DOMPurify.sanitize(containerHtml));
}

// --- List and Delete Indexes ---
async function loadIndexes() {
    const listEl = document.getElementById('indexes-list');
    if (!listEl) return;
    try {
        const res = await fetch('/indexes', {
            headers: getFetchHeaders()
        });
        if (!res.ok) {
            if (res.status === 401) {
                listEl.innerHTML = '<div class="index-item-empty" style="color: var(--danger)">Auth required. Enter API Key.</div>';
            } else {
                const cleanStatus = DOMPurify.sanitize(String(res.status));
                listEl.innerHTML = `<div class="index-item-empty">Failed to load indexes (${cleanStatus})</div>`;
            }
            return;
        }
        const data = await res.json();
        if (data.length === 0) {
            listEl.innerHTML = '<div class="index-item-empty">No indexes found.</div>';
            return;
        }
        
        listEl.innerHTML = '';
        data.forEach(idx => {
            const itemEl = document.createElement('div');
            itemEl.className = `index-item ${idx.index_id === currentIndexId ? 'active' : ''}`;
            
            let repoName = idx.repo_url;
            if (repoName) {
                try {
                    const parts = new URL(repoName).pathname.split('/').filter(p => p);
                    if (parts.length >= 2) {
                        repoName = `${parts[parts.length - 2]}/${parts[parts.length - 1]}`;
                    }
                } catch (_) {}
            } else {
                repoName = 'Unknown Repository';
            }
            
            const createdStr = idx.created_at ? new Date(idx.created_at).toLocaleString() : 'N/A';
            
            const cleanRepoUrl = DOMPurify.sanitize(idx.repo_url || '');
            const cleanRepoName = DOMPurify.sanitize(repoName);
            const cleanVectorCount = DOMPurify.sanitize(String(idx.vector_count));
            const cleanCreatedStr = DOMPurify.sanitize(createdStr);

            const chunkCount = idx.vector_count || 0;
            const isEmpty = chunkCount === 0;

            itemEl.innerHTML = `
                <div class="details" style="${isEmpty ? 'opacity: 0.5;' : ''}">
                    <span class="repo-name" title="${cleanRepoUrl}">${cleanRepoName}${isEmpty ? ' ⚠️' : ''}</span>
                    <span class="meta-info">${isEmpty ? '<span style="color:#f59e0b">Empty index</span>' : cleanVectorCount + ' chunks'} • ${cleanCreatedStr}</span>
                </div>
                <button class="btn-delete" title="Delete Index">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2 2v2"></path><line x1="10" y1="11" x2="10" y2="17"></line><line x1="14" y1="11" x2="14" y2="17"></line></svg>
                </button>
            `;
            
            itemEl.addEventListener('click', (e) => {
                if (e.target.closest('.btn-delete')) return;
                // BUG-2 FIX: Pass chunkCount so setActiveIndex can reject empty indexes
                setActiveIndex(idx.index_id, idx.repo_url, chunkCount);
            });
            
            itemEl.querySelector('.btn-delete').addEventListener('click', async (e) => {
                e.stopPropagation();
                if (!confirm('Are you sure you want to delete this index? This cannot be undone.')) return;
                try {
                    const delRes = await fetch(`/index-repository/${idx.index_id}`, {
                        method: 'DELETE',
                        headers: getFetchHeaders()
                    });
                    if (delRes.ok) {
                        if (currentIndexId === idx.index_id) {
                            currentIndexId = null;
                            elements.currentIndexInfo.className = 'index-info empty';
                            elements.currentIndexInfo.innerHTML = 'No active index.';
                            elements.chatInput.disabled = true;
                            elements.sendBtn.disabled = true;
                        }
                        loadIndexes();
                    } else {
                        const errData = await delRes.json();
                        alert('Delete failed: ' + (errData.detail || 'Unknown error'));
                    }
                } catch (err) {
                    alert('Error deleting index: ' + err.message);
                }
            });
            
            listEl.appendChild(itemEl);
        });
    } catch (err) {
        const cleanErr = DOMPurify.sanitize(err.message);
        listEl.innerHTML = `<div class="index-item-empty">Error: ${cleanErr}</div>`;
    }
}

// Initialize page load
document.addEventListener('DOMContentLoaded', async () => {
    const healthIndicator = document.getElementById('health-indicator');
    const healthStatusText = document.getElementById('health-status-text');

    async function checkHealth() {
        if (healthIndicator) {
            healthIndicator.className = 'status-indicator grey';
            healthIndicator.title = 'Checking server connection...';
        }
        if (healthStatusText) {
            healthStatusText.textContent = 'Checking...';
            healthStatusText.className = 'status-text';
        }

        try {
            const res = await fetch('/health');
            if (res.ok) {
                const data = await res.json();
                if (data.status === 'ok') {
                    if (healthIndicator) {
                        healthIndicator.className = 'status-indicator green';
                        healthIndicator.title = 'Connected to server';
                    }
                    if (healthStatusText) {
                        healthStatusText.textContent = 'Connected';
                        healthStatusText.className = 'status-text green';
                    }
                    return;
                }
            }
            throw new Error('Invalid response');
        } catch (err) {
            if (healthIndicator) {
                healthIndicator.className = 'status-indicator red';
                healthIndicator.title = 'Server connection error';
            }
            if (healthStatusText) {
                healthStatusText.textContent = 'Error';
                healthStatusText.className = 'status-text red';
            }
        }
    }

    // Toggle collapsible settings
    const settingsToggleBtn = document.getElementById('settings-toggle-btn');
    const settingsContent = document.getElementById('settings-content');
    if (settingsToggleBtn && settingsContent) {
        settingsToggleBtn.addEventListener('click', () => {
            const isOpen = settingsToggleBtn.classList.toggle('open');
            if (isOpen) {
                settingsContent.classList.remove('hidden');
            } else {
                settingsContent.classList.add('hidden');
            }
        });
    }

    // Run health check initially
    await checkHealth();

    // Start periodic polling of health (every 30 seconds)
    setInterval(checkHealth, 30000);

    loadIndexes();

    // Show onboarding welcome message on first load
    showWelcomeMessage();
});

function showWelcomeMessage() {
    const msgEl = createMessageElement('assistant');
    const contentEl = msgEl.querySelector('.message-content');
    elements.chatHistory.appendChild(msgEl);
    contentEl.innerHTML = `
        <div style="border: 1px solid #374151; border-radius: 10px; padding: 20px; background: rgba(255,255,255,0.02);">
            <div style="display:flex; align-items:center; gap:10px; margin-bottom:14px;">
                <span style="font-size:1.4rem;">🔬</span>
                <h3 style="margin:0; color:#e5e7eb; font-size:1rem;">Welcome to RepoLens</h3>
            </div>

            <p style="margin:0 0 10px; color:#9ca3af; line-height:1.6; font-size:0.93rem;">
                RepoLens is an <strong style="color:#d1d5db;">evidence-based code verification platform</strong>.
                It doesn't explain or summarise code — it <em>verifies specific claims</em>
                about a codebase using real evidence: exact file paths, line numbers, and code snippets.
            </p>

            <div style="background:rgba(16,185,129,0.06); border:1px solid rgba(16,185,129,0.15); border-radius:8px; padding:12px; margin-bottom:10px;">
                <p style="margin:0 0 6px; color:#6ee7b7; font-size:0.78rem; text-transform:uppercase; letter-spacing:0.05em; font-weight:600;">✅ How to use it</p>
                <ol style="margin:0; padding-left:18px; color:#d1d5db; line-height:1.8; font-size:0.9rem;">
                    <li>Paste a GitHub repository URL on the left and click <strong>Index</strong></li>
                    <li>Wait for indexing to complete (chunks will appear in the sidebar)</li>
                    <li>Type a specific, testable claim below and press Send</li>
                </ol>
            </div>

            <div style="background:rgba(255,255,255,0.03); border-radius:8px; padding:12px;">
                <p style="margin:0 0 6px; color:#9ca3af; font-size:0.78rem; text-transform:uppercase; letter-spacing:0.05em;">💬 Example claims</p>
                <ul style="margin:0; padding-left:18px; color:#9ca3af; line-height:1.8; font-size:0.88rem; font-style:italic;">
                    <li>"The auth middleware validates JWT tokens on every request"</li>
                    <li>"Database queries use parameterised statements to prevent SQL injection"</li>
                    <li>"The rate limiter tracks requests per IP using an in-memory store"</li>
                    <li>"All API keys are stored in environment variables, not hardcoded"</li>
                </ul>
            </div>
        </div>
    `;
    scrollToBottom();
}
