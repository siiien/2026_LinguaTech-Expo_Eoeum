'use strict';
const $ = id => document.getElementById(id);
const methodLabels = {B0: 'B0 · 원문만', B1: 'B1 · 문맥 포함', P1: 'P1 · 화용 보존 지침'};
let cases = [], direction = 'en-ko', selected = null, busy = false, configured = false, exportRecord = null;

function resetResult() {
  exportRecord = null;
  $('download').disabled = true;
  $('error').hidden = true;
  $('output-badge').className = 'small-tag';
  $('output-badge').textContent = '준비';
  $('results').replaceChildren();
  const empty = document.createElement('div');
  empty.className = 'empty-state';
  const title = document.createElement('h3');
  title.textContent = '문맥과 함께 번역을 살펴보세요.';
  const text = document.createElement('p');
  text.textContent = '저장된 사례를 고르거나 문장을 직접 입력할 수 있어요.';
  empty.append(title, text);
  $('results').append(empty);
}

function showError(message) { $('error').textContent = message; $('error').hidden = false; }
function contextText(context) { return context.map(turn => `${turn.speaker === 'speaker' ? '나' : '상대'}: ${turn.text}`).join('\n'); }
function selectCase(id) {
  selected = cases.find(c => c.id === id) || null;
  $('sample').value = selected ? selected.id : '';
  $('source').value = selected ? selected.source_text : '';
  $('context').value = selected ? contextText(selected.context) : '';
  $('relationship').value = selected ? selected.relationship : '';
  resetResult();
  refreshButtons();
}

function setDirection(value) {
  direction = value;
  for (const id of ['en-ko', 'ko-en']) {
    $(id).classList.toggle('selected', id === value);
    $(id).setAttribute('aria-pressed', String(id === value));
  }
  $('source-language').textContent = value === 'en-ko' ? 'ENGLISH' : '한국어';
  $('sample').replaceChildren(new Option('직접 입력', ''));
  const subset = cases.filter(c => `${c.source_language}-${c.target_language}` === value);
  for (const item of subset) {
    const variant = item.id.slice(-1);
    const description = variant === 'c' ? '정보 부족 대조' : `문맥 ${variant.toUpperCase()}`;
    $('sample').add(new Option(`${item.source_text} · ${description}`, item.id));
  }
  selectCase(subset[0]?.id || '');
}

function unchanged() {
  return Boolean(selected && $('source').value === selected.source_text &&
    $('context').value === contextText(selected.context) && $('relationship').value === selected.relationship);
}

function refreshButtons() {
  $('preview').disabled = busy || !unchanged();
  $('translate').disabled = busy || !configured || !$('source').value.trim();
  $('translate').textContent = busy ? '번역 중…' : ($('compare').checked ? '모델로 세 조건 비교' : '모델로 번역');
  $('preview').textContent = busy ? '처리 중…' : '저장된 번역 예시 보기';
  for (const id of ['en-ko', 'ko-en', 'sample', 'custom', 'source', 'context', 'relationship', 'compare']) $(id).disabled = busy;
  $('mode-help').textContent = !unchanged() && selected
    ? '입력을 수정했습니다. 저장된 예시는 다시 불러온 뒤 볼 수 있어요.'
    : ($('compare').checked ? '예시 보기는 1개 초안만 표시합니다. 모델 비교는 최대 3회 호출합니다.'
      : '예시 보기는 저장된 초안을 표시하며 API를 호출하지 않습니다.');
}

function currentCase() {
  const [source, target] = direction.split('-');
  const context = $('context').value.split('\n').filter(line => line.trim()).map(line => {
    const match = line.match(/^(나|상대|speaker|listener)\s*[:：]\s*(.+)$/);
    if (!match) throw new Error('앞선 대화의 각 줄을 “나:” 또는 “상대:”로 시작해 주세요.');
    return {speaker: ['나', 'speaker'].includes(match[1]) ? 'speaker' : 'listener', text: match[2].trim()};
  });
  return {source_language: source, target_language: target, source_text: $('source').value.trim(), context,
    relationship: $('relationship').value.trim() || '관계와 말높임 합의 정보 없음.',
    target_audience: target === 'ko' ? '한국어 업무 메신저 대화 상대' : '국제 업무에서 영어를 사용하는 메신저 대화 상대'};
}

async function post(path, body) {
  const response = await fetch(path, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
  const data = await response.json();
  return {ok: response.ok, data};
}

function addCard(label, translation, note, metadata) {
  const card = document.createElement('article'); card.className = 'result-card';
  const heading = document.createElement('p'); heading.className = 'result-label'; heading.textContent = label;
  const body = document.createElement('p'); body.className = 'translation'; body.textContent = translation;
  card.append(heading, body);
  if (note) { const p = document.createElement('p'); p.className = 'result-note'; p.textContent = note; card.append(p); }
  if (metadata) { const p = document.createElement('p'); p.className = 'metadata'; p.textContent = metadata; card.append(p); }
  $('results').append(card);
}

async function run(mode) {
  if (busy) return;
  let input;
  try { input = currentCase(); } catch (error) { showError(error.message); return; }
  resetResult(); busy = true; refreshButtons();
  $('output-badge').textContent = mode === 'example' ? '예시 불러오는 중' : '모델 응답 대기';
  try {
    const body = mode === 'example' ? {case_id: selected?.id, case: input}
      : {case: input, methods: $('compare').checked ? ['B0', 'B1', 'P1'] : ['P1']};
    const {ok, data} = await post(mode === 'example' ? '/api/preview' : '/api/translate', body);
    $('results').replaceChildren();
    if (mode === 'example' && ok) {
      $('output-badge').className = 'small-tag example'; $('output-badge').textContent = '저장된 예시';
      addCard('AI 작성 · 사람 검증 전', data.translation, data.note, 'API 호출 0회 · 모델 성능 결과가 아닙니다.');
      const notice = document.createElement('p'); notice.className = 'example-notice';
      notice.textContent = '이 번역은 해당 사례에 저장된 참고 초안입니다. 입력을 새로 번역하거나 세 방법을 비교한 결과가 아닙니다.';
      $('results').append(notice);
    } else if (mode === 'live' && data.results?.length) {
      $('output-badge').className = 'small-tag live'; $('output-badge').textContent = ok ? '모델 결과' : '일부 완료';
      for (const result of data.results) addCard(methodLabels[result.method], result.translation, '',
        `${result.model_returned || result.model_requested} · ${(result.elapsed_ms / 1000).toFixed(1)}초 · 프롬프트 ${result.prompt_version}`);
    }
    if (!ok) { showError(data.error || '요청에 실패했습니다.'); if (!data.results?.length) $('output-badge').textContent = '실패'; }
    if (ok || data.results?.length) {
      exportRecord = {created_at: new Date().toISOString(), mode, direction, input, output: data,
        human_validated: false, original_case_id: selected?.id || null, input_modified: !unchanged()};
      $('download').disabled = false;
    }
  } catch (error) { showError('서버 연결을 확인해 주세요. ' + error.message); $('output-badge').textContent = '연결 오류'; }
  finally { busy = false; refreshButtons(); }
}

$('sample').addEventListener('change', event => selectCase(event.target.value));
for (const id of ['en-ko', 'ko-en']) $(id).addEventListener('click', () => setDirection(id));
$('custom').addEventListener('click', () => {selectCase(''); $('source').focus();});
for (const id of ['source', 'context', 'relationship']) $(id).addEventListener('input', () => {resetResult(); refreshButtons();});
$('compare').addEventListener('change', refreshButtons);
$('preview').addEventListener('click', () => run('example'));
$('translate').addEventListener('click', () => run('live'));
$('download').addEventListener('click', () => {
  if (!exportRecord) return;
  const url = URL.createObjectURL(new Blob([JSON.stringify(exportRecord, null, 2)], {type: 'application/json'}));
  const anchor = document.createElement('a'); anchor.href = url; anchor.download = `sai-${direction}-${exportRecord.mode}-${Date.now()}.json`;
  anchor.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
});

async function init() {
  try {
    const responses = await Promise.all([fetch('/api/cases'), fetch('/api/status')]);
    if (responses.some(response => !response.ok)) throw new Error('서버 응답 오류');
    const [data, status] = await Promise.all(responses.map(response => response.json()));
    cases = data.cases; configured = status.configured;
    $('connection').textContent = configured ? '모델 설정 감지 · 연결 미확인' : '예시 사용 가능 · 모델 설정 필요';
    setDirection('en-ko');
  } catch (error) { $('connection').textContent = '서버 연결 오류'; showError(error.message); }
}
init();
