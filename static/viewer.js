'use strict';

(() => {
  const STORE_ID = 14;
  const STATIC_SITE = document.documentElement.dataset.staticSite === 'true';
  const REFRESH_MS = 60 * 1000;
  const state = { stores: [], store: null, kind: 'slot', rate: 'ALL', analysis: null, selectedUnit: null, analysisRequest: 0, lastAnalysisAt: 0, refreshingStore: false, collectionTimer: null, observationSort: 'net' };
  const $ = id => document.getElementById(id);
  const asArray = value => Array.isArray(value) ? value : [];
  const text = value => value === null || value === undefined ? '' : String(value);
  const number = value => value !== null && value !== undefined && value !== '' && Number.isFinite(Number(value)) ? Number(value) : null;
  const fmt = (value, digits = 0) => number(value) === null ? '—' : Number(value).toLocaleString('ja-JP', { maximumFractionDigits: digits });
  const el = (tag, className, value) => { const node = document.createElement(tag); if (className) node.className = className; if (value !== undefined) node.textContent = text(value); return node; };
  const safeUrl = value => { if (!text(value).trim()) return null; try { const url = new URL(text(value), window.location.origin); return ['https:', 'http:'].includes(url.protocol) && !url.username && !url.password ? url.href : null; } catch (_) { return null; } };
  const formatTime = value => { if (!value) return ''; const date = new Date(value); return Number.isNaN(date.valueOf()) ? text(value) : date.toLocaleString('ja-JP', { timeZone: 'Asia/Tokyo', month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' }); };

  async function fetchJson(url) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 25000);
    try {
      let requestUrl = url;
      if (STATIC_SITE) {
        const route = new URL(url, window.location.origin);
        const params = route.searchParams;
        const relative = route.pathname === '/api/stores' ? 'data/api/stores.json'
          : route.pathname === '/api/store/14' ? 'data/api/store-14.json'
          : route.pathname === '/api/collection' ? 'data/api/collection.json'
          : route.pathname === '/api/analysis' ? 'data/api/analysis-' + (params.get('kind') || 'slot') + '-' + (params.get('days') || '90') + '.json'
          : null;
        if (!relative) throw new Error('この静的ページでは未対応のデータです。');
        requestUrl = new URL(relative, document.baseURI).href;
      }
      const response = await fetch(requestUrl, { signal: controller.signal, headers: { Accept: 'application/json' }, cache: 'no-store' });
      const data = await response.json();
      if (!response.ok || data.error) throw new Error(typeof data.error === 'string' ? data.error : 'データを取得できませんでした。');
      return data;
    } catch (error) {
      if (error.name === 'AbortError') throw new Error('取得に時間がかかっています。しばらくしてから更新してください。');
      if (error instanceof SyntaxError) throw new Error('サーバーから正しいデータが返りませんでした。');
      throw error;
    } finally { clearTimeout(timer); }
  }

  function setStatus(node, message, mode = '', spinner = false) {
    node.className = 'status-box' + (mode ? ' ' + mode : '');
    node.replaceChildren();
    if (spinner) { const icon = el('span', 'spinner'); icon.setAttribute('aria-hidden', 'true'); node.append(icon); }
    node.append(el('span', '', message));
    node.hidden = false;
  }

  async function fetchStores() {
    try { const data = await fetchJson('/api/stores'); state.stores = asArray(data).filter(store => Number(store.id) === STORE_ID); }
    catch (_) { /* The fixed store can still be loaded when the store list is unavailable. */ }
  }

  async function loadStore() {
    if (state.refreshingStore) return;
    state.refreshingStore = true;
    if (!state.store) setStatus($('storeStatus'), '設置台データを読み込んでいます…', 'loading', true);
    try {
      const data = await fetchJson('/api/store/' + STORE_ID);
      if (!data.store || typeof data.store !== 'object') throw new Error('店舗情報がありません。');
      state.store = data.store;
      renderStore(data);
      renderRates();
      renderMachines();
      $('storeStatus').hidden = true;
      $('machineControls').hidden = false;
    } catch (error) {
      setStatus($('storeStatus'), '設置台データの取得に失敗しました。' + text(error.message), 'error');
      if (!state.store) $('storeAddress').textContent = '本厚木エリア・PIA厚木の情報のみ表示';
    } finally { state.refreshingStore = false; }
  }

  function totalUnits(kind) {
    return Object.values(state.store?.[kind] || {}).reduce((sum, list) => sum + asArray(list).reduce((count, machine) => count + (number(machine.count) || 0), 0), 0);
  }

  function renderStore(data) {
    const store = data.store;
    const brief = state.stores[0] || {};
    $('storeName').replaceChildren(document.createTextNode('PIA厚木 '), el('span', 'muted', '（本厚木）'));
    $('storeAddress').textContent = text(brief.address || store.address || '本厚木エリア');
    const badges = $('storeBadges'); badges.replaceChildren();
    asArray(store.slot_price).forEach(rate => badges.append(el('span', 'badge', text(rate) + '円スロット')));
    asArray(store.pachinko_price).forEach(rate => badges.append(el('span', 'badge blue', text(rate) + '円パチンコ')));
    $('slotTotalCount').textContent = fmt(totalUnits('slot')) + '台';
    $('pachinkoTotalCount').textContent = fmt(totalUnits('pachinko')) + '台';
    $('btnFloorMap').hidden = !asArray(store.store_floormap_image).length;
    $('btnFlyers').hidden = !asArray(store.store_news).concat(asArray(store.store_informations)).length;
    const mapUrl = safeUrl(store.google_map_url) || (number(brief.latitude) !== null && number(brief.longitude) !== null ? 'https://www.google.com/maps?q=' + encodeURIComponent(brief.latitude + ',' + brief.longitude) : null);
    $('btnGoogleMap').hidden = !mapUrl;
    if (mapUrl) $('btnGoogleMap').href = mapUrl;
    const tickers = asArray(data.tickers).map(item => typeof item === 'string' ? item : item.text || item.body || '').filter(Boolean);
    $('tickerText').textContent = tickers.join(' ｜ ');
    $('tickerBox').hidden = !tickers.length;
  }

  function renderRates() {
    const group = state.store?.[state.kind] || {};
    if (state.rate !== 'ALL' && !Object.hasOwn(group, state.rate)) state.rate = 'ALL';
    $('rateButtons').replaceChildren();
    ['ALL', ...Object.keys(group)].forEach(rate => {
      const button = el('button', 'rate-button' + (rate === state.rate ? ' active' : ''), rate === 'ALL' ? 'すべて' : rate + '円');
      button.type = 'button'; button.setAttribute('aria-pressed', String(rate === state.rate));
      button.addEventListener('click', () => { state.rate = rate; renderRates(); renderMachines(); });
      $('rateButtons').append(button);
    });
  }

  function machineNumbers(value) { return (Array.isArray(value) ? value : text(value).split(',')).map(item => text(item).trim()).filter(Boolean); }

  function renderMachines() {
    if (!state.store) return;
    const query = $('searchInput').value.trim().toLocaleLowerCase('ja-JP');
    const matched = [];
    Object.entries(state.store[state.kind] || {}).forEach(([rate, list]) => {
      if (state.rate !== 'ALL' && state.rate !== rate) return;
      asArray(list).forEach(item => {
        const units = machineNumbers(item.machines);
        if (!query || text(item.name).toLocaleLowerCase('ja-JP').includes(query) || units.some(unit => unit.includes(query))) matched.push({ ...item, rate, units });
      });
    });
    matched.sort((a, b) => (number(b.count) || 0) - (number(a.count) || 0));
    $('filteredCount').textContent = fmt(matched.length);
    $('filteredUnits').textContent = fmt(matched.reduce((sum, item) => sum + (number(item.count) || 0), 0));
    const grid = $('machineGrid'); grid.replaceChildren();
    if (!matched.length) { grid.append(el('p', 'grid-empty', '条件に一致する機種が見つかりませんでした。')); return; }
    const fragment = document.createDocumentFragment();
    matched.forEach(item => {
      const card = el('article', 'card machine-card');
      const imageBox = el('div', 'machine-img-box');
      imageBox.append(el('span', 'machine-badge-rate' + (state.kind === 'pachinko' ? ' blue' : ''), item.rate + '円'), el('span', 'machine-badge-count', fmt(item.count) + '台'));
      const imageUrl = safeUrl(item.machine_image);
      if (imageUrl) {
        const image = el('img'); image.src = imageUrl; image.alt = text(item.name); image.loading = 'lazy'; image.decoding = 'async';
        image.addEventListener('error', () => image.replaceWith(el('span', 'machine-placeholder', '画像なし')), { once: true }); imageBox.append(image);
      } else imageBox.append(el('span', 'machine-placeholder', '画像なし'));
      const body = el('div', 'machine-body'); const title = el('h3', 'machine-title', item.name || '機種名なし'); title.title = text(item.name);
      const unitList = el('div', 'unit-list'); const tags = el('div', 'unit-tags');
      item.units.forEach(unit => tags.append(el('span', 'unit-tag', unit)));
      if (!item.units.length) tags.append(el('span', 'muted small', '台番号の情報なし'));
      unitList.append(el('div', 'unit-label', '台番号'), tags); body.append(title, unitList); card.append(imageBox, body); fragment.append(card);
    });
    grid.append(fragment);
  }

  async function loadAnalysis() {
    clearTimeout(state.collectionTimer);
    state.collectionTimer = null;
    const requestId = ++state.analysisRequest;
    const days = $('analysisDays').value;
    const kind = state.kind;
    if (!state.analysis || state.analysis.kind !== kind || String(state.analysis.window?.days) !== days) $('analysisContent').hidden = true;
    setStatus($('analysisStatus'), '過去データを確認・集計しています…', 'loading', true);
    $('analysisPanel')?.setAttribute('aria-busy', 'true');
    try {
      const data = await fetchJson('/api/analysis?days=' + encodeURIComponent(days) + '&kind=' + encodeURIComponent(kind));
      if (requestId !== state.analysisRequest) return;
      state.analysis = data;
      state.lastAnalysisAt = Date.now();
      renderAnalysis(data);
      if (data.refresh?.running && !document.hidden) state.collectionTimer = setTimeout(loadAnalysis, 5000);
    } catch (error) {
      if (requestId !== state.analysisRequest) return;
      setStatus($('analysisStatus'), '過去データの取得に失敗しました。' + text(error.message), 'error');
      if (state.analysis?.kind === kind && String(state.analysis.window?.days) === days) {
        $('analysisContent').hidden = !(asArray(state.analysis.candidates).length || asArray(state.analysis.observations).length);
        $('analysisStatus').append(el('span', '', ' 表示中の集計は前回取得時のものです。'));
      } else { $('analysisContent').hidden = true; $('analysisCoverage').textContent = '今回の収録状況は確認できませんでした'; }
    } finally { if (requestId === state.analysisRequest) $('analysisPanel')?.removeAttribute('aria-busy'); }
  }

  function renderAnalysis(data) {
    const ranked = asArray(data.candidates).length > 0;
    const observations = asArray(data.observations).slice().sort((a, b) => {
      const units = () => text(a.machine_number).localeCompare(text(b.machine_number), 'ja', { numeric: true });
      if (state.observationSort === 'unit') return units();
      const aNet = number(a.evidence?.total_net), bNet = number(b.evidence?.total_net);
      if (aNet === null && bNet === null) return units();
      if (aNet === null) return 1;
      if (bNet === null) return -1;
      return bNet - aNet || units();
    });
    const candidates = ranked ? asArray(data.candidates).slice(0, 10) : observations.slice(0, 20).map(item => ({ ...item, isObservation: true }));
    const summary = data.summary || {};
    const source = data.source || {};
    $('sourceCaution').textContent = source.coverage === 'selected_report_units' || !source.coverage
      ? '公開取材の掲載日・掲載台のみが対象です。全台・全営業日の記録ではなく、掲載対象には偏りがあります。'
      : text(source.summary || '取得できた実績が対象です。収録されていない日・台は分析に含まれません。');
    $('sourceDetails').hidden = !source.message;
    $('sourceNotes').textContent = text(source.message);
    const running = Boolean(data.refresh?.running);
    const messages = asArray(data.reasons).map(text).filter(Boolean);
    if (data.status === 'ok' && ranked) {
      if (running) setStatus($('analysisStatus'), text(data.refresh.message || '新しいデータを収集中です。取得済みの実績で集計しています。'), 'collecting', true);
      else if (source.status === 'error' || source.status === 'blocked' || source.status === 'partial') setStatus($('analysisStatus'), text(source.message || '最新データの取得に制限があります。収録済みの実績を表示しています。'), 'collecting');
      else $('analysisStatus').hidden = true;
    } else {
      let message = messages.join(' ');
      if (running) message = text(data.refresh.message || '過去データを収集中です。取得が終わると自動更新時に結果を表示します。') + (message ? ' ' + message : '');
      else if (data.status === 'insufficient_data') message ||= '分析に必要な実績が不足しています。複数日の有効なデータが揃った台から表示します。';
      else if (data.status === 'error') message ||= '過去データを取得できませんでした。時間をおいて更新してください。';
      else message ||= 'この種別・期間の過去データがまだありません。取得できた実データのみで分析します。';
      setStatus($('analysisStatus'), message, data.status === 'error' ? 'error' : running ? 'collecting' : 'empty', running);
    }
    $('analysisContent').hidden = !candidates.length;
    const netLabel = state.kind === 'slot' ? '差枚' : '差玉';
    const observationCount = number(summary.observed_units) ?? observations.length;
    $('candidateHeading').textContent = ranked ? '過去実績の参考順位' : '過去の掲載実績（' + (state.observationSort === 'unit' ? '台番号順' : netLabel + '合計順') + '）';
    $('candidateLimit').textContent = ranked ? '上位10台' : '全' + fmt(observationCount) + '台中' + fmt(candidates.length) + '台を表示';
    $('observationControls').hidden = ranked;
    $('observationSort').options[0].textContent = '合計' + netLabel + '順';
    $('observationNote').textContent = '掲載された日の合計' + netLabel + 'です。掲載日数が異なるため将来の優劣は判断できません。スコア・推奨順位は付けていません。';
    $('analysisSummary').replaceChildren();
    const values = [ ['対象期間', fmt(data.window?.days || $('analysisDays').value), '日'], ['実績収録日', fmt(summary.observed_days), '日'], [ranked ? '参考候補' : '掲載実績を表示', fmt(ranked ? (summary.eligible_candidates ?? candidates.length) : candidates.length), ranked ? '台' : '台 / 全' + fmt(observationCount) + '台'] ];
    values.forEach(([label, value, unit]) => { const item = el('div', 'summary-item'); const amount = el('div', 'summary-value', value); amount.append(el('span', '', unit)); item.append(el('div', 'summary-label', label), amount); $('analysisSummary').append(item); });
    const selected = candidates.find(candidate => candidateKey(candidate) === state.selectedUnit) || candidates[0];
    state.selectedUnit = selected ? candidateKey(selected) : null;
    renderCandidates(candidates);
    if (selected) renderCandidateHistory(selected);
    const observed = number(summary.observed_days);
    const available = Array.isArray(source.available_days) ? source.available_days.length : number(source.available_days);
    const coverage = ['分析期間 ' + text(data.window?.start || '—') + '〜' + text(data.window?.end || '—')];
    if (observed !== null) coverage.push('実績 ' + fmt(observed) + '日・' + fmt(summary.usable_rows) + '件');
    if (available !== null) coverage.push('収録 ' + fmt(available) + '日');
    if (source.last_date) coverage.push('最新実績日 ' + text(source.last_date));
    $('analysisCoverage').textContent = coverage.join(' / ');
    const updated = formatTime(source.fetched_at || data.generated_at || data.window?.as_of);
    $('analysisUpdated').textContent = updated ? '最終確認 ' + updated + '（日本時間）' : '';
    const sourceUrl = safeUrl(source.url);
    $('analysisSource').hidden = !sourceUrl;
    if (sourceUrl) { $('analysisSource').href = sourceUrl; $('analysisSource').textContent = text(source.name || 'データ出典') + ' ↗'; }
    renderCollection(data.collection);
  }

  function renderCollection(collection) {
    if (!collection || typeof collection !== 'object') {
      $('collectionStatus').textContent = '未確認';
      $('collectionSchedule').textContent = 'サーバーの保存状況を取得できませんでした。';
      $('collectionFacts').hidden = true;
      $('collectionMessage').hidden = true;
      $('officialSnapshotNote').hidden = true;
      $('officialSnapshotDetails').hidden = true;
      return;
    }
    const failed = collection.status === 'error' || collection.last_archive_status === 'error';
    $('collectionPanel').classList.toggle('collection-error', failed);
    $('collectionStatus').textContent = failed ? '保存状況を確認してください' : collection.last_snapshot_at ? '保存済み' : '初回保存待ち';
    const saveTime = text(collection.daily_save_time || '23:00');
    $('collectionSchedule').textContent = STATIC_SITE
      ? 'GitHub Actionsが毎日' + saveTime + '（日本時間）に履歴を取得し、このページのデータを更新します。'
      : 'サーバー稼働中は、ページを閉じても毎日' + saveTime + '（日本時間）に取得可能なデータを保存します。';
    const nextSave = formatTime(collection.next_save_at);
    const previousSave = formatTime(collection.last_snapshot_at);
    const facts = [
      ['最終保存', previousSave ? previousSave + '（日本時間）' : 'まだありません'],
      ['次回保存予定', nextSave ? nextSave + '（日本時間）' : '確認中'],
      ['DB収録（全種別）', fmt(collection.stored_days) + '実績日 / ' + fmt(collection.total_rows) + '件'],
      ['収録実績日の範囲', collection.first_date && collection.last_date ? text(collection.first_date) + '〜' + text(collection.last_date) : '実績はまだありません']
    ];
    if (collection.last_archive_date) facts.push(['直近の保存日', text(collection.last_archive_date)]);
    const officialCapture = formatTime(collection.official_snapshot_at);
    const hasOfficialCapture = Boolean(officialCapture);
    if (hasOfficialCapture) facts.push(['公式ランキング取得時点記録', officialCapture + '（日本時間） / ' + fmt(collection.official_snapshot_units) + '台']);
    $('collectionFacts').replaceChildren();
    facts.forEach(([label, value]) => { const item = el('div'); item.append(el('dt', '', label), el('dd', '', value)); $('collectionFacts').append(item); });
    $('collectionFacts').hidden = false;
    $('collectionMessage').textContent = text(collection.message);
    $('collectionMessage').hidden = !collection.message;
    $('officialSnapshotNote').textContent = collection.official_data_date
      ? '公式ランキングの対象実績日：' + text(collection.official_data_date)
      : '対象日のない公式ランキングは取得時点データとして保存し、日別分析に使いません。';
    $('officialSnapshotNote').hidden = !hasOfficialCapture;
    $('officialSnapshotMessage').textContent = text(collection.official_message);
    $('officialSnapshotDetails').hidden = !hasOfficialCapture || !collection.official_message;
  }

  const candidateKey = candidate => text(candidate.kind) + ':' + text(candidate.rate) + ':' + text(candidate.machine_number);

  function renderCandidates(candidates) {
    $('candidateList').replaceChildren();
    candidates.forEach((candidate, index) => {
      const button = el('button', 'candidate' + (candidateKey(candidate) === state.selectedUnit ? ' selected' : ''));
      button.type = 'button'; button.setAttribute('aria-pressed', String(candidateKey(candidate) === state.selectedUnit));
      const identity = el('div'); identity.append(el('div', 'candidate-unit', text(candidate.machine_number) + '番台'), el('div', 'candidate-model', candidate.machine_name || '機種名なし'));
      const score = el('div', candidate.isObservation ? 'candidate-score observation-days' : 'candidate-score', candidate.isObservation ? signed(candidate.evidence?.total_net) : fmt(candidate.score, 1)); score.append(el('small', '', candidate.isObservation ? '掲載日合計' + (state.kind === 'slot' ? '差枚' : '差玉') : '相対スコア'));
      button.append(el('span', 'candidate-rank', candidate.isObservation ? '•' : candidate.rank || index + 1), identity, score);
      const reason = candidate.isObservation ? '掲載実績 ' + fmt(candidate.evidence?.sample_days) + '日。実績不足のためスコア・推奨順位は付けていません。' : asArray(candidate.reasons)[0]; if (reason) button.append(el('div', 'candidate-reason', reason));
      button.addEventListener('click', () => { state.selectedUnit = candidateKey(candidate); renderCandidates(candidates); renderCandidateHistory(candidate); });
      $('candidateList').append(button);
    });
  }

  function renderCandidateHistory(candidate) {
    $('historyHeading').textContent = text(candidate.machine_number) + '番台の日別実績';
    const history = asArray(candidate.history).slice().sort((a, b) => text(a.date).localeCompare(text(b.date)));
    const chart = $('historyChart'); chart.replaceChildren();
    chart.append(el('div', 'chart-title', candidate.machine_name || '機種名なし'));
    const netRows = history.filter(row => number(row.net) !== null);
    const metric = netRows.length ? 'net' : 'hits';
    const metricLabel = metric === 'net' ? (state.kind === 'slot' ? '差枚' : '差玉') : '大当り回数';
    chart.append(el('p', 'chart-subtitle', metricLabel + 'の推移。未取得の値は表示しません。'));
    drawChart(chart, history, metric, metricLabel);
    const evidence = candidate.evidence || {};
    const evidenceNode = $('candidateEvidence'); evidenceNode.replaceChildren();
    const stats = el('div', 'evidence-stats');
    const pairs = [ ['有効実績日', fmt(evidence.sample_days) + '日'], ['総回転数', fmt(evidence.total_spins)], [metric === 'net' ? '合計' + metricLabel : '合計大当り', metric === 'net' ? signed(evidence.total_net) : fmt(evidence.hits)] ];
    if (number(evidence.peer_units) !== null) pairs.push(['比較対象', fmt(evidence.peer_units) + '台']);
    pairs.forEach(([label, value]) => { const pair = el('div'); pair.append(el('div', 'evidence-label', label), el('div', 'evidence-value', value)); stats.append(pair); });
    evidenceNode.append(stats);
    const reasons = el('ul', 'evidence-reasons');
    const reasonItems = candidate.isObservation ? ['現在設置されている同じ台番号・機種の掲載実績です。', '実績が不足しているため、当たり台の推奨や順位付けはしていません。'] : asArray(candidate.reasons);
    reasonItems.forEach(reason => reasons.append(el('li', '', reason))); evidenceNode.append(reasons);
    const reportUrl = safeUrl(candidate.source_url);
    if (reportUrl) { const link = el('a', 'report-link', '元の掲載記事を見る ↗'); link.href = reportUrl; link.target = '_blank'; link.rel = 'noopener noreferrer'; evidenceNode.append(link); }
    if (history.length) {
      const details = el('details', 'history-table-wrap'); details.append(el('summary', '', '日別データを確認（' + history.length + '日）'));
      const scroll = el('div', 'table-scroll'); const table = el('table'); const head = el('thead'); const heading = el('tr');
      const hasSources = history.some(row => safeUrl(row.source_url));
      ['日付', '回転数', '大当り', state.kind === 'slot' ? '差枚' : '差玉', ...(hasSources ? ['出典'] : [])].forEach(value => { const cell = el('th', '', value); cell.scope = 'col'; heading.append(cell); }); head.append(heading);
      const body = el('tbody'); history.slice().reverse().forEach(row => { const line = el('tr'); [row.date, fmt(row.spins), fmt(row.hits), signed(row.net)].forEach(value => line.append(el('td', '', value))); if (hasSources) { const cell = el('td'); const url = safeUrl(row.source_url); if (url) { const link = el('a', '', '記事 ↗'); link.href = url; link.target = '_blank'; link.rel = 'noopener noreferrer'; cell.append(link); } else cell.textContent = '—'; line.append(cell); } body.append(line); });
      table.append(head, body); scroll.append(table); details.append(scroll); evidenceNode.append(details);
    }
  }

  function signed(value) { return number(value) === null ? '—' : (Number(value) > 0 ? '+' : '') + fmt(value); }

  function drawChart(container, history, metric, metricLabel) {
    const valid = history.filter(row => number(row[metric]) !== null);
    if (!valid.length) { container.append(el('div', 'chart-empty', 'グラフに表示できる日別実績がありません。')); return; }
    const NS = 'http://www.w3.org/2000/svg';
    const svgNode = (tag, attrs, value) => { const node = document.createElementNS(NS, tag); Object.entries(attrs).forEach(([key, val]) => node.setAttribute(key, text(val))); if (value !== undefined) node.textContent = text(value); return node; };
    const width = 480, height = 220, left = 52, right = 10, top = 14, bottom = 33;
    const chartWidth = width - left - right, chartHeight = height - top - bottom;
    let minimum = Math.min(0, ...valid.map(row => Number(row[metric]))), maximum = Math.max(0, ...valid.map(row => Number(row[metric])));
    if (minimum === maximum) { maximum += 1; minimum -= metric === 'net' ? 1 : 0; }
    const range = maximum - minimum; maximum += range * 0.08; if (minimum < 0) minimum -= range * 0.08;
    const y = value => top + (maximum - value) / (maximum - minimum) * chartHeight;
    const svg = svgNode('svg', { viewBox: '0 0 ' + width + ' ' + height, class: 'history-chart', role: 'img', 'aria-label': metricLabel + 'の日別グラフ。詳細な値は下の日別データから確認できます。' });
    [maximum, (maximum + minimum) / 2, minimum].forEach(value => { const py = y(value); svg.append(svgNode('line', { x1: left, y1: py, x2: width - right, y2: py, class: 'chart-grid' }), svgNode('text', { x: left - 7, y: py + 3, 'text-anchor': 'end', class: 'chart-axis' }, fmt(value))); });
    svg.append(svgNode('line', { x1: left, y1: y(0), x2: width - right, y2: y(0), class: 'chart-zero' }));
    const step = chartWidth / Math.max(history.length, 1), barWidth = Math.min(28, step * 0.67);
    const labelIndices = new Set(Array.from({ length: Math.min(5, history.length) }, (_, index) => Math.round(index * (history.length - 1) / Math.max(Math.min(5, history.length) - 1, 1))));
    history.forEach((row, index) => {
      const value = number(row[metric]); const x = left + step * index + step / 2;
      if (value !== null) {
        const bar = svgNode('rect', { x: x - barWidth / 2, y: Math.min(y(value), y(0)), width: barWidth, height: Math.max(Math.abs(y(value) - y(0)), 1), rx: 2, fill: value >= 0 ? '#67bea4' : '#d97d88' });
        bar.append(svgNode('title', {}, text(row.date) + ' ' + metricLabel + ' ' + signed(value))); svg.append(bar);
      }
      if (labelIndices.has(index)) svg.append(svgNode('text', { x, y: height - 10, 'text-anchor': 'middle', class: 'chart-axis' }, text(row.date).slice(5).replace('-', '/')));
    });
    container.append(svg);
    const legend = el('div', 'chart-legend');
    (metric === 'net' ? [['#67bea4', 'プラス'], ['#d97d88', 'マイナス']] : [['#67bea4', '大当り回数']]).forEach(([color, label]) => { const item = el('span'); const dot = el('span', 'legend-dot'); dot.style.backgroundColor = color; item.append(dot, document.createTextNode(label)); legend.append(item); });
    container.append(legend);
  }

  function openMedia(type) {
    if (!state.store) return;
    const isMap = type === 'map';
    const items = isMap ? asArray(state.store.store_floormap_image) : asArray(state.store.store_news).concat(asArray(state.store.store_informations));
    $('dialogTitle').textContent = isMap ? 'フロアマップ' : 'チラシ・お知らせ';
    const body = $('dialogBody'); body.replaceChildren();
    let displayed = 0;
    items.forEach(item => {
      const data = typeof item === 'string' ? { image: item } : item;
      if (!data || typeof data !== 'object') return;
      const url = safeUrl(data.original || data.image);
      if (url) { const image = el('img'); image.src = url; image.alt = text(data.title || (isMap ? 'PIA厚木 フロアマップ' : 'PIA厚木 お知らせ')); image.loading = 'lazy'; body.append(image); displayed += 1; }
      if (!isMap && (data.title || data.text || data.body)) { const news = el('article', 'news-item'); if (data.title) news.append(el('h3', '', data.title)); if (data.text || data.body) news.append(el('p', '', data.text || data.body)); body.append(news); displayed += 1; }
    });
    if (!displayed) body.append(el('p', 'muted', '表示できる情報はありません。'));
    $('mediaDialog').showModal();
  }

  async function refreshAll() {
    $('refreshButton').disabled = true;
    try { await Promise.allSettled([loadStore(), loadAnalysis()]); } finally { $('refreshButton').disabled = false; }
  }

  document.addEventListener('DOMContentLoaded', async () => {
    document.querySelectorAll('[data-kind]').forEach(button => button.addEventListener('click', () => {
      const kind = button.dataset.kind; if (kind === state.kind) return;
      state.kind = kind; state.rate = 'ALL'; state.selectedUnit = null;
      document.querySelectorAll('[data-kind]').forEach(tab => { const active = tab.dataset.kind === kind; tab.classList.toggle('active', active); tab.setAttribute('aria-pressed', String(active)); });
      renderRates(); renderMachines(); loadAnalysis();
    }));
    $('analysisDays').addEventListener('change', () => { state.selectedUnit = null; loadAnalysis(); });
    $('observationSort').addEventListener('change', () => { state.observationSort = $('observationSort').value; state.selectedUnit = null; if (state.analysis) renderAnalysis(state.analysis); });
    $('refreshButton').addEventListener('click', refreshAll);
    $('searchInput').addEventListener('input', renderMachines);
    $('clearSearch').addEventListener('click', () => { $('searchInput').value = ''; renderMachines(); $('searchInput').focus(); });
    $('btnFloorMap').addEventListener('click', () => openMedia('map'));
    $('btnFlyers').addEventListener('click', () => openMedia('news'));
    $('closeDialog').addEventListener('click', () => $('mediaDialog').close());
    $('mediaDialog').addEventListener('click', event => { if (event.target === $('mediaDialog')) { const box = $('mediaDialog').getBoundingClientRect(); if (event.clientX < box.left || event.clientX > box.right || event.clientY < box.top || event.clientY > box.bottom) $('mediaDialog').close(); } });
    await fetchStores();
    loadStore(); loadAnalysis();
    setInterval(() => { if (!document.hidden) { loadStore(); loadAnalysis(); } }, REFRESH_MS);
    document.addEventListener('visibilitychange', () => {
      if (document.hidden) { clearTimeout(state.collectionTimer); state.collectionTimer = null; }
      else if (state.analysis?.refresh?.running || Date.now() - state.lastAnalysisAt >= REFRESH_MS) loadAnalysis();
    });
  });
})();
