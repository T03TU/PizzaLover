/* Local, buildless chat client. Reviews and model text are rendered as text only. */
'use strict';
const $ = id => document.getElementById(id);
const metrics = {faithfulness:'Faithfulness',answer_relevancy:'Answer Relevancy',context_precision:'Context Precision',context_utilization:'Context Utilization'};
let activeController = null;
let turns = [];
let selected = null;

function node(tag, text, className) {
  const el = document.createElement(tag);
  if (text !== undefined) el.textContent = text;
  if (className) el.className = className;
  return el;
}
async function api(path, options = {}) {
  const response = await fetch(path, options);
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
  if (activeController || !question.trim()) return;
  $('connection-error').hidden = true;
  const turn = createTurn(question.trim());
  const controller = new AbortController(); activeController = controller;
  $('send').hidden = true; $('stop').hidden = false; $('question').value = '';
  $('chat-status').textContent = 'Retrieving local evidence…';
  let receivedDone = false;
  try {
    const filters = {franchise:$('franchise').value || null,location:$('location').value || null,min_rating:Number($('rating').value) || null};
    const response = await api('/api/chat/stream', {method:'POST',headers:{'Content-Type':'application/json'},signal:controller.signal,body:JSON.stringify({question:question.trim(),filters,eval_enabled:$('eval').checked})});
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
    activeController = null; $('send').hidden = false; $('stop').hidden = true;
    $('chat-status').textContent = turn.error ? 'Turn ended. You can try another question.' : 'Ready for your next question.';
    if (selected === turn) selectTurn(turn);
    refreshSummary().catch(error => showError(error.message));
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
  if (activeController) { showError('Stop the current turn before starting a new chat.'); return; }
  turns.forEach(turn => { turn.assistant.previousSibling?.remove(); turn.assistant.remove(); });
  turns = []; selected = null; $('welcome').hidden = false;
  $('reviews').replaceChildren(); $('evidence-empty').hidden = false; $('review-count').textContent = '0 reviews';
  renderScores(null); $('chat-status').textContent = 'New chat. Server evaluation history is retained.'; $('question').focus();
});
$('save-history').addEventListener('click', async () => {
  $('save-history').disabled = true; $('save-status').textContent = 'Saving…';
  try { const result = await (await api('/api/evaluation/save',{method:'POST'})).json(); $('save-status').textContent = `${result.count} records saved to ${result.path}`; }
  catch (error) { $('save-status').textContent = error.message; }
  finally { $('save-history').disabled = false; }
});
$('upload').addEventListener('click', async () => {
  const file = $('csv-file').files[0];
  if (!file) { $('upload-status').textContent = 'Choose a CSV file first.'; return; }
  if (file.size > 2 * 1024 * 1024 - 1024) { $('upload-status').textContent = 'Please choose a file smaller than 2 MiB.'; return; }
  const form = new FormData(); form.append('file',file);
  $('upload').disabled = true; $('upload-status').textContent = 'Embedding and indexing locally…';
  try { const result = await (await api('/api/ingest',{method:'POST',body:form})).json(); $('upload-status').textContent = `${result.added} reviews indexed.`; await loadMetadata(); }
  catch (error) { $('upload-status').textContent = error.message; }
  finally { $('upload').disabled = false; }
});
renderScores(null);
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
