marked.setOptions({
    highlight: function(code, lang) {
        if (lang && hljs.getLanguage(lang)) {
            return hljs.highlight(code, { language: lang }).value;
        }
        return hljs.highlightAuto(code).value;
    }
});

let currentIndexId = null;

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

// --- Indexing Logic ---
elements.indexBtn.addEventListener('click', async () => {
    const url = elements.repoUrl.value.trim();
    if (!url) return;

    setIndexingState(true, 'Starting...');

    try {
        const res = await fetch('/index-repository', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
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
        const res = await fetch(`/index-repository/${indexId}`);
        const data = await res.json();
        
        if (data.status === 'processing') {
            setIndexingState(true, 'Processing repository...');
            setTimeout(() => pollStatus(indexId, repoUrl), 2000);
        } else if (data.status === 'completed') {
            setIndexingState(false, 'Indexing complete!');
            setActiveIndex(indexId, repoUrl);
        } else if (data.status === 'failed') {
            setIndexingState(false, `Failed: ${data.error || 'Unknown error'}`, true);
        }
    } catch (err) {
        setIndexingState(false, 'Error polling status', true);
    }
}

function setActiveIndex(indexId, repoUrl) {
    currentIndexId = indexId;
    elements.currentIndexInfo.className = 'index-info';
    elements.currentIndexInfo.innerHTML = `<strong>ID:</strong> ${indexId}<br><strong>Repo:</strong> ${repoUrl}`;
    
    elements.chatInput.disabled = false;
    elements.sendBtn.disabled = false;
    
    addMessage('system', `Repository indexed! You can now ask questions about it.`);
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
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ index_id: indexId, question: question })
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
