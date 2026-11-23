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

function can(permission) { return !!currentProfile?.permissions.includes(permission); }
function applyPermissions() {
  document.querySelectorAll('[data-permission]').forEach(el => { el.hidden = !can(el.dataset.permission); });
  $('add-profile').hidden = !can('users:create');
  $('reset-knowledge').hidden = currentProfile?.role !== 'ADMIN' || !can('pipeline:manage');
  $('account-summary').textContent = currentProfile ? `${currentProfile.name} · ${currentProfile.role}` : '';
  $('question').disabled = !can('chat:use');
  $('eval').disabled = !can('scorecards:view');
  if (!can('scorecards:view')) { $('eval').checked = false; renderScores(null); }
  turns.forEach(renderFeedback);
  updateNavigation();
}
function updateNavigation() {
  const busy = historyBusy || !!activeController;
  $('profile').disabled = true;
  $('add-profile').disabled = busy || !can('users:create');
  for (const id of ['new-chat', 'send']) $(id).disabled = busy || !can('chat:use');
  $('settings').disabled = !currentProfile;
  $('sign-out').disabled = busy || !currentProfile;
  document.querySelectorAll('.chips button, .history-open, .history-delete').forEach(button => { button.disabled = busy || !can('chat:use'); });
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
  if (!can('chat:use')) { $('history-status').textContent = 'Chat access is not enabled for your role.'; return []; }
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
      metrics:saved.metrics, error:saved.error, complete:true, id:saved.id, status:saved.status, feedback:saved.feedback});
    renderAnswer(turn); renderTurnFooter(turn); renderFeedback(turn); selectTurn(turn);
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
  applyPermissions();
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
  if (currentProfile && !path.startsWith('/api/auth/')) headers.set('X-Profile-ID', currentProfile.id);
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
function renderFeedback(turn) {
  if (!can('feedback:submit') || turn.status !== 'complete' || !turn.id) {
    turn.feedbackElement?.remove(); turn.feedbackElement = null; return;
  }
  if (turn.feedbackElement) return;
  const box = node('div', undefined, 'feedback-controls'); turn.feedbackElement = box;
  box.addEventListener('click', event => event.stopPropagation());
  const form = node('form'); form.hidden = true;
  const comment = node('textarea'); comment.maxLength = 4000; comment.rows = 2;
  comment.placeholder = 'Optional comment'; comment.setAttribute('aria-label', 'Optional response feedback comment');
  comment.value = turn.feedback?.comment || '';
  const submit = node('button', 'Submit', 'secondary'); submit.type = 'submit';
  const status = node('p', turn.feedback ? 'Feedback saved. Select a rating to update it.' : ''); status.setAttribute('role', 'status');
  let rating = turn.feedback?.rating;
  const buttons = [];
  for (const [value, label] of [['thumbs_up', '👍 Thumbs Up'], ['thumbs_down', '👎 Thumbs Down']]) {
    const button = node('button', label, 'secondary'); button.type = 'button'; button.dataset.rating = value;
    button.setAttribute('aria-pressed', String(rating === value));
    button.addEventListener('click', () => {
      rating = value; form.hidden = false;
      buttons.forEach(b => b.setAttribute('aria-pressed', String(b.dataset.rating === rating)));
    }); buttons.push(button); box.append(button);
  }
  const cancel = node('button', 'Cancel', 'secondary'); cancel.type = 'button';
  cancel.addEventListener('click', () => {
    rating = turn.feedback?.rating;
    comment.value = turn.feedback?.comment || '';
    buttons.forEach(button => button.setAttribute('aria-pressed', String(button.dataset.rating === rating)));
    form.hidden = true;
    status.textContent = turn.feedback ? 'Feedback saved. Select a rating to update it.' : '';
  });
  form.append(comment, submit, cancel);
  form.addEventListener('submit', async event => {
    event.preventDefault(); submit.disabled = true; cancel.disabled = true;
    buttons.forEach(button => { button.disabled = true; });
    try {
      turn.feedback = await (await api('/api/feedback', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({message_id:turn.id, rating, comment:comment.value})})).json();
      status.textContent = 'Feedback saved. Thank you.'; form.hidden = true;
    } catch (error) { status.textContent = error.message; }
    finally { submit.disabled = false; cancel.disabled = false; buttons.forEach(button => { button.disabled = false; }); }
  });
  box.append(form, status); turn.assistant.append(box);
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
  if (activeController || historyBusy || !can('chat:use') || !question.trim()) return;
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
    activeChatId = response.headers.get('X-Chat-ID'); turn.id = response.headers.get('X-Turn-ID'); rememberChat();
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
    turn.status = receivedDone && !turn.error ? 'complete' : 'interrupted';
    renderFeedback(turn);
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
  if (!can('scorecards:view')) return;
  const data = await (await api('/api/evaluation/summary')).json();
  summaryRows($('session-summary'), data.session);
  const count = Math.max(0, ...Object.values(data.session).map(stat => stat.count));
  $('session-count').textContent = `${count} turns`;
  if (Object.keys(data.golden).length) summaryRows($('golden-summary'), data.golden);
}
async function loadMetadata() {
  if (!can('chat:use')) return;
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
$('add-profile').addEventListener('click', async () => {
  $('profile-form').reset(); $('profile-error').textContent = ''; $('profile-role').replaceChildren(new Option('USER', 'USER'));
  try {
    if (can('roles:manage')) {
      const {roles} = await (await api('/api/roles')).json();
      $('profile-role').replaceChildren(...roles.map(role => new Option(role.name, role.name)));
      $('profile-role').value = 'USER';
    }
    $('profile-dialog').showModal();
  } catch (error) { showError(error.message); }
});
$('profile-cancel').addEventListener('click', () => $('profile-dialog').close());
$('profile-form').addEventListener('submit', async event => {
  event.preventDefault();
  const name = $('profile-name').value.trim();
  if (!name || historyBusy || activeController) return;
  historyBusy = true; updateNavigation(); $('profile-create').disabled = true;
  try {
    await api('/api/profiles', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({name, password:$('profile-password').value, role:$('profile-role').value, email:$('profile-email').value, phone:$('profile-phone').value, country:$('profile-country').value})});
    $('profile-dialog').close();
    if ($('users-dialog').open) await loadUsers();
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
applyPermissions();

async function startAccount() {
  await loadProfiles();
  try {
    await restoreProfileChat();
    const results = await Promise.allSettled([loadMetadata(), refreshSummary()]);
    results.forEach(result => { if (result.status === 'rejected') showError(result.reason.message); });
  } catch (error) { showError(error.message); }
  finally { historyBusy = false; updateNavigation(); }
}
let setupRequired = false;
(async () => {
  try {
    const status = await (await api('/api/auth/status')).json();
    setupRequired = status.setup_required;
    await loadRecoveryOptions();
    $('auth-method-fields').hidden = setupRequired;
    $('forgot-password').hidden = setupRequired;
    if (!setupRequired) {
      const response = await fetch('/api/profiles');
      if (response.ok) { await startAccount(); return; }
    }
    $('setup-fields').hidden = !setupRequired;
    $('setup-token').required = setupRequired;
    $('auth-password').minLength = setupRequired ? 12 : 1;
    $('auth-title').textContent = setupRequired ? 'Set up administrator' : 'Sign in';
    $('auth-help').textContent = setupRequired ? 'Use the setup token printed in the server console and choose a password of at least 12 characters. Existing installations keep the oldest profile as administrator.' : 'Use your account name, email or phone number and password.';
    $('auth-dialog').showModal();
  } catch (error) { showError(error.message); }
})();
$('auth-dialog').addEventListener('cancel', event => event.preventDefault());
$('auth-form').addEventListener('submit', async event => {
  event.preventDefault();
  const button = event.submitter; button.disabled = true;
  try {
    const body = {name:$('auth-name').value.trim(), password:$('auth-password').value, country:$('auth-country').value};
    if (setupRequired) body.token = $('setup-token').value.trim();
    await api(setupRequired ? '/api/auth/setup' : '/api/auth/login', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
    $('auth-password').value = ''; $('setup-token').value = '';
    $('auth-dialog').close(); await startAccount();
  } catch (error) { $('auth-error').textContent = error.message; }
  finally { button.disabled = false; }
});
$('sign-out').addEventListener('click', async () => {
  try { await api('/api/auth/logout', {method:'POST'}); location.reload(); }
  catch (error) { showError(error.message); }
});
$('settings').addEventListener('click', () => $('settings-dialog').showModal());
document.querySelectorAll('[data-close]').forEach(button => button.addEventListener('click', () => $(button.dataset.close).close()));

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

function renderPipeline(result) {
  const table = node('table'); const head = node('tr');
  result.columns.forEach(column => head.append(node('th', column)));
  const thead = node('thead'); thead.append(head); table.append(thead);
  const body = node('tbody');
  result.rows.forEach(row => { const tr = node('tr'); row.forEach(cell => tr.append(node('td', cell === null ? 'NULL' : String(cell)))); body.append(tr); });
  table.append(body); $('pipeline-results').replaceChildren(table);
  $('pipeline-status').textContent = `${result.affected_rows} rows changed. ${result.row_count} result rows${result.truncated ? ' (preview limited to 500)' : ''}.`;
}
let pipelineBusy = false;
async function pipelineAction(action) {
  if (pipelineBusy) return;
  pipelineBusy = true;
  for (const id of ['run-sql', 'stage-upload', 'embed-staged', 'reset-knowledge']) $(id).disabled = true;
  $('pipeline-status').textContent = 'Working…';
  try { await action(); }
  catch (error) { $('pipeline-status').textContent = error.message; }
  finally { pipelineBusy = false; for (const id of ['run-sql', 'stage-upload', 'embed-staged', 'reset-knowledge']) $(id).disabled = false; }
}
$('open-pipeline').addEventListener('click', () => {
  $('pipeline-dialog').showModal();
  pipelineAction(async () => renderPipeline(await (await api('/api/pipeline')).json()));
});
$('sql-form').addEventListener('submit', event => {
  event.preventDefault();
  pipelineAction(async () => {
    renderPipeline(await (await api('/api/pipeline/query', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({sql:$('pipeline-sql').value})})).json());
  });
});
$('stage-upload').addEventListener('click', () => pipelineAction(async () => {
  const file = $('stage-file').files[0]; if (!file) throw new Error('Choose a CSV or XLSX file first.');
  const data = new FormData(); data.append('file', file);
  const preview = await (await api('/api/ingest/preview', {method:'POST', body:data})).json();
  if (preview.requires_mapping) {
    const mapping = await chooseColumnMapping(preview);
    if (!mapping) { $('pipeline-status').textContent = 'Upload cancelled.'; return; }
    data.append('mapping', JSON.stringify(mapping));
  }
  const result = await (await api('/api/pipeline/stage', {method:'POST', body:data})).json();
  renderPipeline(await (await api('/api/pipeline')).json());
  $('pipeline-status').textContent = `${result.staged} reviews staged; ${result.skipped_blank_comments} blank comments skipped. Edit the data, then generate embeddings.`;
}));
$('embed-staged').addEventListener('click', () => pipelineAction(async () => {
  const result = await (await api('/api/pipeline/embed', {method:'POST'})).json();
  renderPipeline(await (await api('/api/pipeline')).json());
  $('pipeline-status').textContent = `${result.added} updated reviews embedded and removed from staging.`;
  await loadMetadata();
}));

let roleData = [];
let permissionNames = [];
function fillRoleForm() {
  const role = roleData.find(r => r.name === $('role-select').value);
  $('role-name').value = role?.name || ''; $('role-name').readOnly = !!role;
  $('permission-fields').replaceChildren();
  for (const permission of permissionNames) {
    const label = node('label'); const checkbox = node('input'); checkbox.type = 'checkbox';
    checkbox.value = permission; checkbox.checked = role?.permissions.includes(permission) || false;
    label.append(checkbox, document.createTextNode(permission)); $('permission-fields').append(label);
  }
  $('role-delete').hidden = !role || ['ADMIN', 'USER'].includes(role.name);
}
async function loadRoles(selectedName = '') {
  const data = await (await api('/api/roles')).json(); roleData = data.roles; permissionNames = data.permissions;
  $('role-select').replaceChildren(new Option('Create new role…', ''), ...roleData.map(role => new Option(role.name, role.name)));
  $('role-select').value = selectedName; fillRoleForm();
}
$('open-roles').addEventListener('click', async () => {
  $('role-status').textContent = ''; $('roles-dialog').showModal();
  try { await loadRoles(); } catch (error) { $('role-status').textContent = error.message; }
});
$('role-select').addEventListener('change', fillRoleForm);
$('role-form').addEventListener('submit', async event => {
  event.preventDefault(); $('role-save').disabled = true;
  try {
    const existing = $('role-select').value; const name = $('role-name').value;
    const permissions = [...$('permission-fields').querySelectorAll('input:checked')].map(input => input.value);
    await api(existing ? `/api/roles/${encodeURIComponent(existing)}` : '/api/roles', {method:existing ? 'PUT' : 'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({name, permissions})});
    await loadProfiles();
    if (can('roles:manage')) await loadRoles(name); else $('roles-dialog').close();
    $('role-status').textContent = 'Role saved.';
  } catch (error) { $('role-status').textContent = error.message; }
  finally { $('role-save').disabled = false; }
});
$('role-delete').addEventListener('click', async () => {
  $('role-delete').disabled = true;
  try { await api(`/api/roles/${encodeURIComponent($('role-select').value)}`, {method:'DELETE'}); await loadRoles(); $('role-status').textContent = 'Role deleted.'; }
  catch (error) { $('role-status').textContent = error.message; }
  finally { $('role-delete').disabled = false; }
});
async function loadUsers() {
  const {users} = await (await api('/api/users')).json();
  const roles = can('roles:manage') ? (await (await api('/api/roles')).json()).roles : [{name:'USER'}];
  $('users-list').replaceChildren();
  for (const user of users) {
    const form = node('form', undefined, 'user-editor');
    form.append(node('h3', `${user.name} · ${user.role}${user.active ? '' : ' · inactive'}`));
    if (can('roles:manage')) {
      const select = node('select'); select.setAttribute('aria-label', `Role for ${user.name}`);
      roles.forEach(role => select.add(new Option(role.name, role.name))); select.value = user.role;
      const active = node('input'); active.type = 'checkbox'; active.checked = !!user.active;
      const label = node('label'); label.append(active, document.createTextNode(' Active account'));
      const password = node('input'); password.type = 'password'; password.minLength = 12; password.maxLength = 256;
      password.autocomplete = 'new-password'; password.placeholder = 'New password (optional, at least 12 characters)';
      password.setAttribute('aria-label', `New password for ${user.name}`);
      const email = node('input'); email.type = 'email'; email.maxLength = 254; email.value = user.email || '';
      email.setAttribute('aria-label', `Recovery email for ${user.name}`); email.placeholder = 'Recovery email';
      const phone = node('input'); phone.type = 'tel'; phone.maxLength = 80; phone.value = user.phone || '';
      phone.setAttribute('aria-label', `Recovery phone for ${user.name}`); phone.placeholder = 'Recovery phone';
      const countryGroup = node('div'); const country = makeCountrySelector(countryGroup, `country-${user.id}`);
      const save = node('button', 'Save user', 'secondary'); save.type = 'submit';
      form.append(select, label, email, countryGroup, phone, password, save);
      form.addEventListener('submit', async event => {
        event.preventDefault(); save.disabled = true;
        try {
          const body = {role:select.value, active:active.checked, email:email.value, phone:phone.value, country:country.value}; if (password.value) body.password = password.value;
          await api(`/api/users/${user.id}`, {method:'PATCH', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
          if (user.id === currentProfile.id && password.value) { location.reload(); return; }
          await loadProfiles();
          if (can('users:create')) await loadUsers(); else $('users-dialog').close();
          $('users-status').textContent = 'User saved.';
        } catch (error) { $('users-status').textContent = error.message; }
        finally { save.disabled = false; }
      });
    }
    const lowerPrivilege = currentProfile.role === 'ADMIN' && user.role !== 'ADMIN' && user.id !== currentProfile.id &&
      user.permissions.length < currentProfile.permissions.length && user.permissions.every(permission => can(permission));
    if (lowerPrivilege) {
      const remove = node('button', 'Remove user', 'secondary'); remove.type = 'button';
      remove.setAttribute('aria-label', `Remove user ${user.name}`);
      remove.addEventListener('click', async () => {
        if (!confirm(`Remove ${user.name}? Their account and saved chats will be permanently deleted.`)) return;
        remove.disabled = true;
        try {
          await api(`/api/users/${user.id}`, {method:'DELETE'});
          await loadUsers(); $('users-status').textContent = 'User removed.';
        } catch (error) { $('users-status').textContent = error.message; }
        finally { remove.disabled = false; }
      });
      form.append(remove);
    }
    $('users-list').append(form);
  }
}
$('open-users').addEventListener('click', async () => {
  $('users-status').textContent = ''; $('users-dialog').showModal();
  try { await loadUsers(); } catch (error) { $('users-status').textContent = error.message; }
});

$('reset-knowledge').addEventListener('click', () => {
  if (pipelineBusy) return;
  $('reset-knowledge-form').reset(); $('reset-confirm').disabled = true;
  $('reset-dialog-status').textContent = ''; $('reset-knowledge-dialog').showModal();
});
$('reset-confirmation').addEventListener('input', () => {
  $('reset-confirm').disabled = $('reset-confirmation').value !== 'CONFIRM' || pipelineBusy;
});
$('reset-cancel').addEventListener('click', () => $('reset-knowledge-dialog').close());
$('reset-knowledge-dialog').addEventListener('cancel', event => { if (pipelineBusy) event.preventDefault(); });
$('reset-knowledge-form').addEventListener('submit', async event => {
  event.preventDefault();
  const confirmation = $('reset-confirmation').value;
  if (pipelineBusy || confirmation !== 'CONFIRM') return;
  pipelineBusy = true;
  for (const id of ['run-sql', 'stage-upload', 'embed-staged', 'reset-knowledge', 'reset-confirm', 'reset-cancel', 'reset-confirmation']) $(id).disabled = true;
  $('reset-dialog-status').textContent = 'Removing all embedded reviews…';
  try {
    const result = await (await api('/api/pipeline/reset', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({confirmation})})).json();
    $('reset-status').textContent = `${result.removed} embedded reviews removed. Knowledge bank is empty.`;
    clearConversation(); rememberChat();
    $('chat-status').textContent = 'Knowledge bank is empty. Stage and embed new reviews to chat.';
    await Promise.all([loadMetadata(), refreshHistory()]);
    $('reset-knowledge-dialog').close();
  } catch (error) { $('reset-dialog-status').textContent = error.message; }
  finally {
    pipelineBusy = false;
    for (const id of ['run-sql', 'stage-upload', 'embed-staged', 'reset-knowledge', 'reset-cancel', 'reset-confirmation']) $(id).disabled = false;
    $('reset-confirm').disabled = $('reset-confirmation').value !== 'CONFIRM';
  }
});

let recoveryOptions = null;
function makeCountrySelector(container, id) {
  const searchLabel = node('label', 'Search country or dial code'); searchLabel.htmlFor = `${id}-search`;
  const search = node('input'); search.id = `${id}-search`; search.type = 'search'; search.placeholder = 'e.g. New Zealand, NZ, +64';
  const label = node('label', 'Country / calling code'); label.htmlFor = id;
  const select = node('select'); select.id = id;
  const names = new Intl.DisplayNames(['en'], {type:'region'});
  const entries = (recoveryOptions?.countries || []).map(country => ({...country, text:`${names.of(country.iso)} (${country.iso}) ${country.dial_code}`})).sort((a, b) => a.text.localeCompare(b.text));
  let chosen = 'NZ';
  function populate() {
    const query = search.value.trim().toLowerCase();
    select.replaceChildren(...entries.filter(entry => entry.text.toLowerCase().includes(query)).map(entry => new Option(entry.text, entry.iso, entry.iso === chosen, entry.iso === chosen)));
    if ([...select.options].some(option => option.value === chosen)) select.value = chosen;
    else if (select.options.length) select.selectedIndex = 0;
  }
  select.addEventListener('change', () => { chosen = select.value; });
  search.addEventListener('input', populate); populate();
  container.replaceChildren(searchLabel, search, label, select);
  return select;
}
async function loadRecoveryOptions() {
  recoveryOptions = await (await api('/api/auth/recovery/options')).json();
  makeCountrySelector($('auth-country-fields'), 'auth-country');
  makeCountrySelector($('recovery-country-fields'), 'recovery-country');
  makeCountrySelector($('profile-country-fields'), 'profile-country');
  updateRecoveryChannel();
}
$('auth-method').addEventListener('change', () => {
  const phone = $('auth-method').value === 'phone';
  $('auth-country-fields').hidden = !phone;
  $('auth-name-label').textContent = phone ? 'Phone number' : 'Account name or email';
  $('auth-name').type = phone ? 'tel' : 'text';
});
let recoveryRequestId = null;
let recoveryToken = null;
function recoveryStep(step) {
  for (const name of ['request', 'verify', 'password']) $(`recovery-${name}-form`).hidden = name !== step;
  $('recovery-error').textContent = '';
}
function updateRecoveryChannel() {
  const phone = $('recovery-channel').value === 'phone';
  $('recovery-country-fields').hidden = !phone;
  $('recovery-identifier-label').textContent = phone ? 'Phone number' : 'Email address';
  $('recovery-identifier').type = phone ? 'tel' : 'email';
  $('recovery-identifier').autocomplete = phone ? 'tel-national' : 'email';
  $('recovery-dev').hidden = recoveryOptions?.delivery[$('recovery-channel').value] !== 'console';
}
$('recovery-channel').addEventListener('change', () => { $('recovery-identifier').value = ''; updateRecoveryChannel(); });
$('forgot-password').addEventListener('click', () => {
  recoveryRequestId = null; recoveryToken = null;
  for (const name of ['request', 'verify', 'password']) $(`recovery-${name}-form`).reset();
  recoveryStep('request'); updateRecoveryChannel(); $('recovery-dialog').showModal();
});
function clearRecovery() {
  recoveryToken = null; recoveryRequestId = null;
  $('recovery-code').value = ''; $('recovery-password').value = ''; $('recovery-confirm-password').value = '';
}
$('recovery-dialog').addEventListener('close', clearRecovery);
$('recovery-cancel').addEventListener('click', () => $('recovery-dialog').close());
$('recovery-resend').addEventListener('click', () => { clearRecovery(); recoveryStep('request'); });
async function recoveryAction(event, action) {
  event.preventDefault(); const button = event.submitter; button.disabled = true;
  $('recovery-error').textContent = '';
  try { await action(); }
  catch (error) { $('recovery-error').textContent = error.message; }
  finally { button.disabled = false; }
}
$('recovery-request-form').addEventListener('submit', event => recoveryAction(event, async () => {
  const result = await (await api('/api/auth/recovery/request', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({channel:$('recovery-channel').value, identifier:$('recovery-identifier').value.trim(), country:$('recovery-country').value})})).json();
  recoveryRequestId = result.request_id; recoveryStep('verify'); $('recovery-confirmation').textContent = result.message;
}));
$('recovery-verify-form').addEventListener('submit', event => recoveryAction(event, async () => {
  const result = await (await api('/api/auth/recovery/verify', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({request_id:recoveryRequestId, code:$('recovery-code').value})})).json();
  recoveryToken = result.reset_token; $('recovery-code').value = ''; recoveryStep('password');
}));
$('recovery-password-form').addEventListener('submit', event => recoveryAction(event, async () => {
  if ($('recovery-password').value !== $('recovery-confirm-password').value) throw new Error('Passwords do not match.');
  const result = await (await api('/api/auth/recovery/complete', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({reset_token:recoveryToken, password:$('recovery-password').value, confirm_password:$('recovery-confirm-password').value})})).json();
  $('recovery-dialog').close(); $('auth-password').value = ''; $('auth-error').textContent = '';
  $('auth-notice').textContent = result.message; $('auth-password').focus();
}));
