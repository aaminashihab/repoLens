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
            setActiveIndex(indexId, repoUrl);
            loadIndexes();
        } else if (data.status === 'failed') {
            setIndexingState(false, `Failed: ${data.error || 'Unknown error'}`, true);
        }
    } catch (err) {
        setIndexingState(false, 'Error polling status', true);
    }
}

function setActiveIndex(indexId, repoUrl) {
    currentIndexId = indexId;
    conversationHistory = [];
    elements.currentIndexInfo.className = 'index-info';
    elements.currentIndexInfo.innerHTML = `<strong>ID:</strong> ${indexId}<br><strong>Repo:</strong> ${repoUrl}`;
    
    elements.chatInput.disabled = false;
    elements.sendBtn.disabled = false;
    
    addMessage('system', `Repository indexed! You can now ask questions about it.`);
    
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
    
    addMessage('user', question);
    
    const messageEl = createMessageElement('assistant');
    const contentEl = messageEl.querySelector('.message-content');
    elements.chatHistory.appendChild(messageEl);
    
    try {
        await streamAnswer(currentIndexId, question, contentEl);
    } catch (err) {
        contentEl.innerHTML = `<p style="color: var(--danger)">Error: ${err.message}</p>`;
    } finally {
        elements.chatInput.disabled = false;
        elements.sendBtn.disabled = false;
        elements.chatInput.focus();
        scrollToBottom();
    }
});

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
                listEl.innerHTML = `<div class="index-item-empty">Failed to load indexes (${res.status})</div>`;
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
            
            itemEl.innerHTML = `
                <div class="details">
                    <span class="repo-name" title="${idx.repo_url || ''}">${repoName}</span>
                    <span class="meta-info">${idx.vector_count} chunks • ${createdStr}</span>
                </div>
                <button class="btn-delete" title="Delete Index">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path><line x1="10" y1="11" x2="10" y2="17"></line><line x1="14" y1="11" x2="14" y2="17"></line></svg>
                </button>
            `;
            
            itemEl.addEventListener('click', (e) => {
                if (e.target.closest('.btn-delete')) return;
                setActiveIndex(idx.index_id, idx.repo_url);
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
        listEl.innerHTML = `<div class="index-item-empty">Error: ${err.message}</div>`;
    }
}

// Initialize API Key configuration and load index list on page load
document.addEventListener('DOMContentLoaded', () => {
    const apiKeyInput = document.getElementById('api-key-input');
    if (apiKeyInput) {
        apiKeyInput.value = localStorage.getItem('repolens_api_key') || '';
        apiKeyInput.addEventListener('input', (e) => {
            localStorage.setItem('repolens_api_key', e.target.value);
            loadIndexes();
        });
    }
    loadIndexes();
});
