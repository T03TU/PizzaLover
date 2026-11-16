/* Local, buildless chat client. Reviews and model text are rendered as text only. */
'use strict';
const $ = id => document.getElementById(id);
const metrics = {faithfulness:'Faithfulness',answer_relevancy:'Answer Relevancy',context_precision:'Context Precision',context_utilization:'Context Utilization'};
let activeController = null;
let turns = [];
let selected = null;
let currentProfile = null;
let activeChatId = null;
let historyBusy = true;
const paneLabels = {sidebar:'controls & history','chat-pane':'chat','evidence-pane':'evidence & tests'};

function readPreference(key) {
  try { return localStorage.getItem(key); } catch (_) { return null; }
}
function savePreference(key, value) {
  try { if (value === null) localStorage.removeItem(key); else localStorage.setItem(key, value); } catch (_) { /* Storage may be disabled. */ }
}
function setPaneVisible(id, visible) {
  $(id).hidden = !visible;
  const button = document.querySelector(`[data-pane="${id}"]`);
  button.setAttribute('aria-expanded', String(visible));
  button.textContent = `${visible ? 'Hide' : 'Show'} ${paneLabels[id]}`;
  const key = {sidebar:'sidebarHidden','chat-pane':'chatHidden','evidence-pane':'evidenceHidden'}[id];
  $('workspace').dataset[key] = String(!visible);
  $('panes-empty').hidden = Object.keys(paneLabels).some(pane => !$(pane).hidden);
  savePreference(`pizzalover.pane.${id}`, visible ? 'open' : 'closed');
}
document.querySelectorAll('[data-pane]').forEach(button => {
  const id = button.dataset.pane;
  setPaneVisible(id, readPreference(`pizzalover.pane.${id}`) !== 'closed');
  button.addEventListener('click', () => setPaneVisible(id, $(id).hidden));
});

function updateNavigation() {
  const busy = historyBusy || !!activeController;
  for (const id of ['profile', 'add-profile', 'new-chat', 'send']) $(id).disabled = busy;
  document.querySelectorAll('.history-open, .history-delete').forEach(button => { button.disabled = busy; });
}
function rememberChat() {
  savePreference(`pizzalover.chat.${currentProfile.id}`, activeChatId);
}
function clearConversation() {
  turns.forEach(turn => { turn.assistant.previousSibling?.remove(); turn.assistant.remove(); });
  turns = []; selected = null; activeChatId = null; $('welcome').hidden = false;
  $('reviews').replaceChildren(); $('evidence-empty').hidden = false; $('review-count').textContent = '0 reviews';
  renderScores(null);
}
async function refreshHistory() {
  const profileId = currentProfile.id;
  const {chats} = await (await api('/api/chats')).json();
  if (currentProfile.id !== profileId) return [];
  $('chat-list').replaceChildren();
  for (const chat of chats) {
    const row = node('div', undefined, 'history-item');
    const open = node('button', undefined, 'history-open');
    open.type = 'button'; open.setAttribute('aria-current', String(chat.id === activeChatId));
    open.append(node('span', chat.title), node('small', `${chat.turn_count} turns · ${new Date(chat.updated_at).toLocaleDateString()}`));
    open.title = chat.title;
    open.addEventListener('click', () => navigateHistory(() => loadChat(chat.id)));
    const remove = node('button', '×', 'history-delete');
    remove.type = 'button'; remove.setAttribute('aria-label', `Delete chat: ${chat.title}`); remove.title = `Delete chat: ${chat.title}`;
    remove.addEventListener('click', () => navigateHistory(async () => {
      await api(`/api/chats/${chat.id}`, {method:'DELETE'});
      if (activeChatId === chat.id) { clearConversation(); rememberChat(); }
      await refreshHistory();
    }));
    row.append(open, remove); $('chat-list').append(row);
  }
  $('history-status').textContent = chats.length ? `${chats.length} saved chats for ${currentProfile.name}.` : 'No saved chats yet. Ask a question to start one.';
  updateNavigation();
  return chats;
}
async function loadChat(id, reveal = true) {
  const chat = await (await api(`/api/chats/${id}`)).json();
  clearConversation(); activeChatId = chat.id; rememberChat();
  for (const saved of chat.turns) {
    const turn = createTurn(saved.question);
    Object.assign(turn, {text:saved.text || saved.error || 'No answer was returned.', citations:saved.citations,
      metrics:saved.metrics, error:saved.error, complete:true});
    renderAnswer(turn); renderTurnFooter(turn); selectTurn(turn);
  }
  $('chat-status').textContent = 'Saved chat restored. Each question still uses independent review evidence.';
  if (reveal) setPaneVisible('chat-pane', true);
  await refreshHistory();
}
async function loadProfiles() {
  const data = await (await api('/api/profiles')).json();
  currentProfile = data.current;
  $('profile').replaceChildren();
  data.profiles.forEach(profile => $('profile').add(new Option(profile.name, profile.id)));
  $('profile').value = currentProfile.id;
}
async function restoreProfileChat() {
  clearConversation();
  const chats = await refreshHistory();
  const previous = readPreference(`pizzalover.chat.${currentProfile.id}`);
  if (chats.some(chat => chat.id === previous)) await loadChat(previous, false);
  else { rememberChat(); $('chat-status').textContent = `Ready for ${currentProfile.name}.`; }
}
async function navigateHistory(action) {
  if (activeController || historyBusy) return;
  historyBusy = true; updateNavigation();
  try { await action(); }
  catch (error) { showError(error.message); }
  finally { historyBusy = false; updateNavigation(); }
}

function node(tag, text, className) {
  const el = document.createElement(tag);
  if (text !== undefined) el.textContent = text;
  if (className) el.className = className;
  return el;
}
async function api(path, options = {}) {
  const headers = new Headers(options.headers);
  if (currentProfile && (path.startsWith('/api/chats') || path === '/api/chat/stream')) headers.set('X-Profile-ID', currentProfile.id);
  const response = await fetch(path, {...options, headers});
  if (!response.ok) {
    let detail = `Request failed (${response.status})`;
    try { const data = await response.json(); detail = typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail); } catch (_) { /* retain status */ }
    throw new Error(detail);
  }
  return response;
}
function showError(message) {
  $('connection-error').textContent = message;
  $('connection-error').hidden = false;
}
function renderScores(scores, status = '') {
  $('scores').replaceChildren();
  for (const [key, label] of Object.entries(metrics)) {
    const value = scores?.[key];
    const row = node('div', undefined, 'score');
    const line = node('div', undefined, 'score-label');
    line.append(node('span', label), node('span', Number.isFinite(value) ? value.toFixed(4) : '—'));
    const track = node('div', undefined, 'track');
    const fill = node('div', undefined, 'fill');
    fill.style.width = `${Number.isFinite(value) ? Math.max(0, Math.min(1, value)) * 100 : 0}%`;
    track.append(fill); row.append(line, track); $('scores').append(row);
  }
  $('score-status').textContent = status || 'Four ways to check an answer’s quality.';
}
function selectTurn(turn) {
  selected = turn;
  for (const t of turns) t.assistant.classList.toggle('selected', t === turn);
  $('reviews').replaceChildren();
  $('evidence-empty').hidden = turn.citations.length > 0;
  $('review-count').textContent = `${turn.citations.length} reviews`;
  turn.citations.forEach(review => {
    const card = node('article', undefined, 'review-card');
    card.dataset.feedbackId = review.Feedback_ID;
    const top = node('div', undefined, 'review-top');
    top.append(node('span', review.Feedback_ID), node('span', review.Franchise, 'franchise-pill'));
    const rating = Math.max(0, Math.min(5, Number(review.Rating) || 0));
    card.append(top, node('h3', review.Location), node('div', '★'.repeat(rating) + '☆'.repeat(5-rating), 'stars'), node('blockquote', review.Comment));
    $('reviews').append(card);
  });
  const states = {partial:'Some judge metrics were unavailable; missing scores are shown as —.',complete:'Scored locally against the retrieved reviews.',disabled:'Auto-evaluation was off for this answer.',failed:'Evaluation failed. Check the local Ollama server.',no_context:'No matching reviews to evaluate.'};
  renderScores(turn.metrics?.scores, turn.error || states[turn.metrics?.evaluation_status] || (turn.complete ? 'No evaluation available.' : 'Answer first. Quality check follows.'));
}
function renderAnswer(turn) {
  turn.body.replaceChildren();
  const expression = /\[(FB-[A-Za-z0-9-]+(?:\s*,\s*FB-[A-Za-z0-9-]+)*)\]/g;
  let last = 0;
  for (const match of turn.text.matchAll(expression)) {
    turn.body.append(document.createTextNode(turn.text.slice(last, match.index)));
    for (const id of match[1].split(/\s*,\s*/)) {
      if (turn.citations.some(review => review.Feedback_ID === id)) {
        const pill = node('button', `[${id}]`, 'citation');
        pill.addEventListener('click', event => {
          event.stopPropagation(); selectTurn(turn);
          setPaneVisible('evidence-pane', true);
          const card = [...$('reviews').children].find(el => el.dataset.feedbackId === id);
          if (card) { card.scrollIntoView({behavior:'smooth',block:'nearest'}); card.classList.add('highlight'); setTimeout(() => card.classList.remove('highlight'), 1800); }
        });
        turn.body.append(pill);
      } else turn.body.append(document.createTextNode(`[${id}]`));
    }
    last = match.index + match[0].length;
  }
  turn.body.append(document.createTextNode(turn.text.slice(last)));
}
function createTurn(question) {
  $('welcome').hidden = true;
  const user = node('article', undefined, 'message user');
  user.append(node('div', 'YOU', 'message-label'), node('div', question, 'message-body'));
  const assistant = node('article', undefined, 'message assistant');
  const body = node('div', 'Finding the right reviews…', 'message-body');
  const footer = node('div', '', 'message-footer');
  assistant.append(node('div', '◔  PIZZALOVER', 'message-label'), body, footer);
  const turn = {question,text:'',citations:[],metrics:null,assistant,body,footer,complete:false};
  assistant.addEventListener('click', () => selectTurn(turn));
  $('conversation').append(user, assistant); turns.push(turn); selectTurn(turn);
  return turn;
}
function renderTurnFooter(turn) {
  const data = turn.metrics;
  turn.footer.textContent = turn.error || (data ? `${data.generation_time.toFixed(2)}s generation · ${data.evaluation_time.toFixed(2)}s evaluation` : '');
}
/* Retain incomplete frames between reads; TextDecoder handles split UTF-8 bytes. */
async function consumeSSE(response, onEvent) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  function drain() {
    let match;
    while ((match = /\r?\n\r?\n/.exec(buffer))) {
      const frame = buffer.slice(0, match.index);
      buffer = buffer.slice(match.index + match[0].length);
      let name = 'message'; const data = [];
      for (const line of frame.split(/\r?\n/)) {
        if (line.startsWith('event:')) name = line.slice(6).trim();
        else if (line.startsWith('data:')) data.push(line.slice(5).trimStart());
        else if (line === ': evaluating') onEvent('status', 'Evaluating answer quality locally…');
      }
      if (data.length) onEvent(name, JSON.parse(data.join('\n')));
    }
  }
  try {
    while (true) {
      const {value, done} = await reader.read();
      if (done) { buffer += decoder.decode(); drain(); break; }
      buffer += decoder.decode(value, {stream:true}); drain();
    }
  } finally { reader.releaseLock(); }
}
async function sendQuestion(question) {
  if (activeController || historyBusy || !currentProfile || !question.trim()) return;
  $('connection-error').hidden = true;
  const turn = createTurn(question.trim());
  const controller = new AbortController(); activeController = controller;
  updateNavigation();
  $('send').hidden = true; $('stop').hidden = false; $('question').value = '';
  $('chat-status').textContent = 'Retrieving local evidence…';
  let receivedDone = false;
  try {
    const filters = {franchise:$('franchise').value || null,location:$('location').value || null,min_rating:Number($('rating').value) || null};
    const response = await api('/api/chat/stream', {method:'POST',headers:{'Content-Type':'application/json'},signal:controller.signal,body:JSON.stringify({question:question.trim(),filters,eval_enabled:$('eval').checked,chat_id:activeChatId})});
    activeChatId = response.headers.get('X-Chat-ID'); rememberChat();
    await consumeSSE(response, (name, data) => {
      if (name === 'status') $('chat-status').textContent = data;
      if (name === 'citations') { turn.citations = data; selectTurn(turn); }
      if (name === 'token') {
        turn.text += data; renderAnswer(turn);
        $('chat-status').textContent = $('eval').checked ? 'Answer streaming · local quality check follows…' : 'Answer streaming…';
        const view = $('conversation'); view.scrollTop = view.scrollHeight;
      }
      if (name === 'metrics') {
        turn.metrics = data;
        turn.footer.textContent = `${data.generation_time.toFixed(2)}s generation · ${data.evaluation_time.toFixed(2)}s evaluation`;
        selectTurn(turn);
      }
      if (name === 'error') { turn.error = data.message; showError(data.message); }
      if (name === 'done') receivedDone = true;
    });
    if (!receivedDone) throw new Error('The connection ended before the answer was complete.');
  } catch (error) {
    turn.error = error.name === 'AbortError' ? 'Turn stopped. A local evaluation already running may finish in the background.' : error.message;
    if (error.name !== 'AbortError') showError(turn.error);
  } finally {
    turn.complete = true;
    if (!turn.text) { turn.text = turn.error || 'No answer was returned.'; renderAnswer(turn); }
    if (turn.error) turn.footer.textContent = turn.error;
    activeController = null; $('send').hidden = false; $('stop').hidden = true; updateNavigation();
    $('chat-status').textContent = turn.error ? 'Turn ended. You can try another question.' : 'Ready for your next question.';
    if (selected === turn) selectTurn(turn);
    refreshSummary().catch(error => showError(error.message));
    refreshHistory().catch(error => showError(`Could not refresh chat history: ${error.message}`));
  }
}
function summaryRows(target, summary) {
  target.replaceChildren();
  if (!Object.keys(summary).length) { target.textContent = 'No evaluated answers yet.'; return; }
  for (const [key, stats] of Object.entries(summary)) {
    const row = node('div', undefined, 'summary-row');
    row.append(node('strong', `${metrics[key] || key.replaceAll('_',' ')} · ${Number(stats.mean).toFixed(4)}`), node('small', `min ${Number(stats.min).toFixed(4)} / max ${Number(stats.max).toFixed(4)} · n=${stats.count}`));
    target.append(row);
  }
}
async function refreshSummary() {
  const data = await (await api('/api/evaluation/summary')).json();
  summaryRows($('session-summary'), data.session);
  const count = Math.max(0, ...Object.values(data.session).map(stat => stat.count));
  $('session-count').textContent = `${count} turns`;
  if (Object.keys(data.golden).length) summaryRows($('golden-summary'), data.golden);
}
async function loadMetadata() {
  const data = await (await api('/api/metadata/options')).json();
  for (const [id, values] of [['franchise',data.franchises],['location',data.locations]]) {
    const el = $(id); const previous = el.value;
    while (el.options.length > 1) el.remove(1);
    values.forEach(value => el.add(new Option(value,value))); el.value = previous;
  }
  $('corpus-count').textContent = `${data.total_documents} customer reviews indexed`;
}
$('chat-form').addEventListener('submit', event => { event.preventDefault(); sendQuestion($('question').value); });
$('question').addEventListener('keydown', event => { if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) { event.preventDefault(); sendQuestion($('question').value); } });
document.querySelectorAll('.chips button').forEach(button => button.addEventListener('click', () => sendQuestion(button.firstChild.textContent)));
$('stop').addEventListener('click', () => activeController?.abort());
$('reset-filters').addEventListener('click', () => $('filters').reset());
$('new-chat').addEventListener('click', () => {
  if (activeController || historyBusy) return;
  clearConversation(); rememberChat(); setPaneVisible('chat-pane', true);
  $('chat-status').textContent = 'New chat. Previous conversations are saved in chat history.'; $('question').focus();
  refreshHistory().catch(error => showError(error.message));
});
$('profile').addEventListener('change', () => navigateHistory(async () => {
  try {
    await api(`/api/profiles/${$('profile').value}/select`, {method:'POST'});
    clearConversation();
    await loadProfiles(); await restoreProfileChat();
  } finally { $('profile').value = currentProfile.id; }
}));
$('add-profile').addEventListener('click', () => {
  $('profile-form').reset(); $('profile-error').textContent = ''; $('profile-dialog').showModal();
});
$('profile-cancel').addEventListener('click', () => $('profile-dialog').close());
$('profile-form').addEventListener('submit', async event => {
  event.preventDefault();
  const name = $('profile-name').value.trim();
  if (!name || historyBusy || activeController) return;
  historyBusy = true; updateNavigation(); $('profile-create').disabled = true;
  try {
    await api('/api/profiles', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({name})});
    clearConversation();
    await loadProfiles(); await restoreProfileChat(); $('profile-dialog').close();
  } catch (error) { $('profile-error').textContent = error.message; }
  finally { historyBusy = false; updateNavigation(); $('profile-create').disabled = false; }
});
$('save-history').addEventListener('click', async () => {
  $('save-history').disabled = true; $('save-status').textContent = 'Saving…';
  try { const result = await (await api('/api/evaluation/save',{method:'POST'})).json(); $('save-status').textContent = `${result.count} records saved to ${result.path}`; }
  catch (error) { $('save-status').textContent = error.message; }
  finally { $('save-history').disabled = false; }
});
const reviewFields = ['Feedback_ID', 'Franchise', 'Location', 'Rating', 'Comment'];
function chooseColumnMapping(preview) {
  const dialog = $('column-dialog');
  $('column-fields').replaceChildren();
  $('column-error').textContent = '';
  const selects = new Map();
  for (const field of reviewFields) {
    const label = node('label', field);
    const select = node('select');
    select.required = true;
    select.add(new Option('Choose a source column…', ''));
    preview.columns.forEach((column, index) => select.add(new Option(column, String(index))));
    const match = preview.columns.findIndex(column => column.toLowerCase() === field.toLowerCase());
    select.value = match < 0 ? '' : String(match);
    const sample = node('p', '', 'small muted');
    const update = () => {
      const column = preview.columns[Number(select.value)];
      sample.textContent = select.value === '' ? '' : `Example: ${String(preview.sample[0]?.[column] ?? '(empty)').slice(0, 160)}`;
    };
    select.addEventListener('change', update); update();
    label.append(select, sample); $('column-fields').append(label); selects.set(field, select);
  }
  return new Promise(resolve => {
    let mapping = null;
    const submit = event => {
      event.preventDefault();
      const indices = [...selects.values()].map(select => select.value);
      if (indices.includes('') || new Set(indices).size !== reviewFields.length) {
        $('column-error').textContent = 'Choose a different source column for each field.'; return;
      }
      mapping = Object.fromEntries([...selects].map(([field, select]) => [field, preview.columns[Number(select.value)]]));
      dialog.close();
    };
    const cancel = () => dialog.close();
    const close = () => {
      $('column-form').removeEventListener('submit', submit);
      $('column-cancel').removeEventListener('click', cancel);
      resolve(mapping);
    };
    $('column-form').addEventListener('submit', submit);
    $('column-cancel').addEventListener('click', cancel);
    dialog.addEventListener('close', close, {once:true});
    dialog.showModal();
  });
}
$('upload').addEventListener('click', async () => {
  const file = $('csv-file').files[0];
  if (!file) { $('upload-status').textContent = 'Choose a CSV or Excel (.xlsx) file first.'; return; }
  if (!/\.(csv|xlsx)$/i.test(file.name)) { $('upload-status').textContent = 'Choose a .csv or .xlsx file.'; return; }
  if (file.size > 2 * 1024 * 1024 - 1024) { $('upload-status').textContent = 'Please choose a file smaller than 2 MiB.'; return; }
  const form = new FormData(); form.append('file',file);
  $('upload').disabled = true; $('upload-status').textContent = 'Embedding and indexing locally…';
  try {
    $('upload-status').textContent = 'Checking file columns…';
    const preview = await (await api('/api/ingest/preview', {method:'POST', body:form})).json();
    if (preview.requires_mapping) {
      $('upload-status').textContent = 'Map the file columns to continue.';
      const mapping = await chooseColumnMapping(preview);
      if (!mapping) { $('upload-status').textContent = 'Upload cancelled. No reviews were indexed.'; return; }
      form.append('mapping', JSON.stringify(mapping));
    }
    $('upload-status').textContent = 'Embedding and indexing locally…';
    const result = await (await api('/api/ingest', {method:'POST', body:form})).json();
    $('upload-status').textContent = `${result.added} reviews indexed.${result.skipped_blank_comments ? ` ${result.skipped_blank_comments} reviews skipped because their comments were blank (review ${result.skipped_reviews.join(', ')}).` : ''}`;
    await loadMetadata();
  }
  catch (error) { $('upload-status').textContent = error.message; }
  finally { $('upload').disabled = false; }
});
renderScores(null);
updateNavigation();
(async () => {
  try { await loadProfiles(); await restoreProfileChat(); historyBusy = false; }
  catch (error) { showError(`Could not load local profiles and history: ${error.message}. Reload to retry.`); }
  finally { updateNavigation(); }
})();
Promise.all([loadMetadata(), refreshSummary()]).catch(() => {
  $('corpus-count').textContent = 'Review index unavailable';
  showError('Cannot connect to the local engine. Start FastAPI and check Ollama is running.');
});

$('google-ingest-form').addEventListener('submit', async event => {
  event.preventDefault();
  $('google-import').disabled = true;
  $('google-status').textContent = 'Fetching Google reviews and indexing locally…';
  try {
    const body = {country_code:$('google-country').value.trim().toUpperCase(), region:$('google-region').value.trim(), max_places:Number($('google-limit').value)};
    const result = await (await api('/api/ingest/google', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)})).json();
    $('google-status').textContent = `${result.added} reviews indexed from ${result.places} places; ${result.duplicates} already indexed, ${result.skipped} unavailable or invalid reviews skipped.`;
    await loadMetadata();
  } catch (error) { $('google-status').textContent = error.message; }
  finally { $('google-import').disabled = false; }
});
