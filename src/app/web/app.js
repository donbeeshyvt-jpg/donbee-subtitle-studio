// 字幕工作室前端：分頁 + 下載(進度) + wavesurfer 多區段(可單獨刪除) + 轉錄。
// 本機 vendor（離線可用，不依賴 CDN）
import WaveSurfer from '/static/vendor/wavesurfer.esm.js'
import RegionsPlugin from '/static/vendor/plugins/regions.esm.js'
import TimelinePlugin from '/static/vendor/plugins/timeline.esm.js'

const $ = (s) => document.querySelector(s)
let ws = null
let regionsPlugin = null
let currentId = null

// 秒 → 時間碼：m:ss.s（未滿 1 小時）或 h:mm:ss.s（滿 1 小時），保留 1 位小數
function fmtTime(sec) {
  sec = Math.max(0, sec || 0)
  const h = Math.floor(sec / 3600)
  const m = Math.floor((sec % 3600) / 60)
  const s = (sec % 60).toFixed(1).padStart(4, '0')
  return h > 0 ? `${h}:${String(m).padStart(2, '0')}:${s}` : `${m}:${s}`
}

// ---------------- 分頁切換 ----------------
function showTab(name) {
  document.querySelectorAll('.tab').forEach((t) => {
    const on = t.dataset.tab === name
    t.classList.toggle('active', on)
    t.setAttribute('aria-selected', on ? 'true' : 'false')
  })
  $('#tab-download').hidden = name !== 'download'
  $('#tab-edit').hidden = name !== 'edit'
}
document.querySelectorAll('.tab').forEach((t) => { t.onclick = () => showTab(t.dataset.tab) })

// ---------------- 下載（背景 + 進度輪詢） ----------------
function setDlStatus(t) { $('#dlStatus').textContent = t }

async function startDownload() {
  const url = $('#url').value.trim()
  if (!url) { setDlStatus('請貼上 YouTube 網址'); return }
  const downloadOnly = $('#downloadOnly').checked
  $('#dlBtn').disabled = true
  $('#dlProgressWrap').hidden = false
  setDlStatus('開始下載…')
  try {
    const r = await fetch('/download', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url, download_only: downloadOnly }),
    })
    if (!r.ok) { setDlStatus('下載啟動失敗：' + (await r.text())); return }
    const { job } = await r.json()
    pollDownload(job, downloadOnly)
  } catch (e) {
    setDlStatus('下載錯誤：' + e); $('#dlBtn').disabled = false
  }
}

async function pollDownload(job, downloadOnly) {
  try {
    const r = await fetch('/download_status/' + job)
    const j = await r.json()
    const pct = j.percent || 0
    $('#dlProgress').style.width = pct + '%'
    if (j.status === 'downloading') {
      const mb = j.speed ? (j.speed / 1048576).toFixed(1) + ' MB/s' : ''
      setDlStatus(`下載中… ${pct.toFixed(1)}%  ${mb}`)
    } else if (j.status === 'processing') {
      setDlStatus('合併與最佳化中…（faststart）')
    } else if (j.status === 'done') {
      $('#dlProgress').style.width = '100%'
      setDlStatus('✓ 下載完成：' + j.name)
      $('#dlBtn').disabled = false
      if (!downloadOnly) {
        loadInfo({ id: j.id, name: j.name, duration: j.duration })
        showTab('edit')
      }
      return
    } else if (j.status === 'error') {
      setDlStatus('下載失敗：' + j.error); $('#dlBtn').disabled = false; return
    }
    setTimeout(() => pollDownload(job, downloadOnly), 500)
  } catch (e) {
    setDlStatus('查詢進度失敗：' + e); $('#dlBtn').disabled = false
  }
}

// ---------------- 編輯：載入媒體 ----------------
function setStatus(t) { $('#status').textContent = t }

function loadInfo(info) {
  currentId = info.id
  const video = $('#video')
  video.src = '/media/' + info.id
  $('#player').hidden = false
  initWave(video)
  const dur = info.duration ? fmtTime(info.duration) : '?'
  setStatus(`已載入 ${info.name}（${dur}）。在波形上拖曳建立區段；拖區段中間可移動、拖邊緣可縮放；不選＝整檔。`)
}

async function openLocal() {
  const path = $('#path').value.trim()
  if (!path) { setStatus('請輸入本機影片路徑'); return }
  setStatus('載入中…')
  const r = await fetch('/open', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ path }),
  })
  if (!r.ok) { setStatus('開檔失敗：' + (await r.text())); return }
  loadInfo(await r.json())
}

// ---------------- 波形 + 區段 ----------------
const BADGE_BG = 'rgba(15,20,32,0.78)'
const BADGE_BG_HOVER = '#ff5a6e'

function makeRemoveBadge(region) {
  // 區段「正中央」的 ✕ 按鈕。wavesurfer 區段在 shadow DOM，外部 CSS 進不去，
  // 故全部用 inline 樣式（含 !important 防 wavesurfer 覆蓋），hover 用 JS。
  const el = document.createElement('div')
  el.className = 'region-x'        // 便於定位/測試（樣式仍走 inline，因在 shadow DOM）
  el.textContent = '✕'
  el.title = '刪除此區段'
  Object.assign(el.style, {
    display: 'flex', alignItems: 'center', justifyContent: 'center',
    width: '24px', height: '24px', borderRadius: '50%',
    background: BADGE_BG, color: '#fff', fontSize: '13px', lineHeight: '1',
    cursor: 'pointer', userSelect: 'none', zIndex: '5',
    transition: 'background .12s ease, transform .12s ease, box-shadow .12s ease',
  })
  el.style.setProperty('position', 'absolute', 'important')
  el.style.setProperty('top', '50%', 'important')
  el.style.setProperty('left', '50%', 'important')
  el.style.setProperty('transform', 'translate(-50%, -50%)', 'important')
  el.onmouseenter = () => {
    el.style.background = BADGE_BG_HOVER
    el.style.setProperty('transform', 'translate(-50%, -50%) scale(1.18)', 'important')
    el.style.boxShadow = '0 0 0 3px rgba(255,90,110,0.35)'
  }
  el.onmouseleave = () => {
    el.style.background = BADGE_BG
    el.style.setProperty('transform', 'translate(-50%, -50%)', 'important')
    el.style.boxShadow = 'none'
  }
  el.onclick = (e) => { e.stopPropagation(); region.remove() }
  return el
}

function addBadge(region) {
  try { region.setContent(makeRemoveBadge(region)) } catch (e) { /* 忽略 */ }
}

function initWave(video) {
  if (ws) ws.destroy()
  regionsPlugin = RegionsPlugin.create()
  ws = WaveSurfer.create({
    container: '#waveform', media: video, height: 96,
    waveColor: '#5b8cff', progressColor: '#2b5fd6', cursorColor: '#eef2fb',
    plugins: [regionsPlugin, TimelinePlugin.create({ container: '#timeline' })],
  })
  ws.on('ready', () => { window.__wsReady = true })
  // 可拖曳建立區段（區段預設可拖移、可縮放）
  regionsPlugin.enableDragSelection({ color: 'rgba(91,140,255,0.25)' })
  regionsPlugin.on('region-created', (r) => { addBadge(r); renderRegions() })
  regionsPlugin.on('region-updated', renderRegions)
  regionsPlugin.on('region-removed', renderRegions)
}

function getRegionObjs() {
  if (!regionsPlugin) return []
  const rs = regionsPlugin.getRegions()
  return (Array.isArray(rs) ? rs : Object.values(rs)).sort((a, b) => a.start - b.start)
}
function getRegions() { return getRegionObjs().map((r) => [r.start, r.end]) }

function renderRegions() {
  const ul = $('#regionList')
  ul.innerHTML = ''
  getRegionObjs().forEach((r, i) => {
    const li = document.createElement('li')
    li.title = '雙擊可編輯起訖秒數'
    const label = document.createElement('span')
    label.textContent = `區段 ${i + 1}: ${fmtTime(r.start)}–${fmtTime(r.end)}`
    const del = document.createElement('button')
    del.className = 'btn-x'; del.textContent = '✕'; del.title = '刪除此區段'
    del.setAttribute('aria-label', `刪除區段 ${i + 1}`)
    del.onclick = (e) => { e.stopPropagation(); r.remove() }
    li.append(label, del)
    li.ondblclick = () => editRegion(r, li)          // 雙擊改數值
    ul.appendChild(li)
  })
}

function setRegionRange(region, a, b) {
  // wavesurfer v7 用 setOptions 更新起訖；舊版退回移除後重建
  if (typeof region.setOptions === 'function') region.setOptions({ start: a, end: b })
  else { region.remove(); regionsPlugin.addRegion({ start: a, end: b }) }
}

function editRegion(region, li) {
  li.innerHTML = ''
  const mk = (val) => {
    const inp = document.createElement('input')
    inp.type = 'number'; inp.step = '0.1'; inp.min = '0'; inp.className = 'num'
    inp.value = val.toFixed(1)
    return inp
  }
  const s = mk(region.start)
  const e = mk(region.end)
  const ok = document.createElement('button')
  ok.className = 'btn-x'; ok.textContent = '✓'; ok.title = '套用'; ok.setAttribute('aria-label', '套用')
  const commit = () => {
    const a = parseFloat(s.value)
    const b = parseFloat(e.value)
    if (!isNaN(a) && !isNaN(b) && b > a) setRegionRange(region, a, b)
    renderRegions()
  }
  ok.onclick = commit
  const onKey = (ev) => { if (ev.key === 'Enter') commit(); if (ev.key === 'Escape') renderRegions() }
  s.onkeydown = onKey; e.onkeydown = onKey
  li.append(s, document.createTextNode('–'), e, ok)
  s.focus(); s.select()
}

// ---------------- 控制項 ----------------
$('#dlBtn').onclick = startDownload
$('#openBtn').onclick = openLocal
$('#pickBtn').onclick = async () => {
  // pywebview 視窗：用原生檔案對話框拿「真實路徑」→ 直接 /open，零複製、秒載入
  if (window.pywebview && window.pywebview.api && window.pywebview.api.pick_file) {
    try {
      const path = await window.pywebview.api.pick_file()
      if (path) { $('#path').value = path; openLocal() }
    } catch (e) { setStatus('選檔失敗：' + e) }
  } else {
    $('#fileInput').click()       // 瀏覽器模式無法取得路徑 → 退回上傳
  }
}
$('#fileInput').onchange = async () => {
  const f = $('#fileInput').files[0]
  if (!f) return
  setStatus(`上傳中… ${f.name}（影片較大可能需一會兒）`)
  const fd = new FormData()
  fd.append('file', f)
  try {
    const r = await fetch('/upload', { method: 'POST', body: fd })
    if (!r.ok) { setStatus('上傳失敗：' + (await r.text())); return }
    loadInfo(await r.json())
  } catch (e) { setStatus('上傳錯誤：' + e) }
}
$('#engine').onchange = () => { $('#modelWrap').style.display = $('#engine').value === 'whisperx' ? '' : 'none' }
$('#addRegion').onclick = () => {
  if (!regionsPlugin) return
  const v = $('#video')
  let a = parseFloat($('#regStart').value)
  let b = parseFloat($('#regEnd').value)
  if (isNaN(a)) a = v.currentTime || 0                       // 沒填開始 → 用播放頭
  if (isNaN(b) || b <= a) b = Math.min(a + 10, v.duration || a + 10)  // 沒填結束 → +10s
  regionsPlugin.addRegion({ start: a, end: b, drag: true, resize: true })
  $('#regStart').value = ''; $('#regEnd').value = ''         // 清空供下次
}
$('#clearRegions').onclick = () => { if (regionsPlugin) { regionsPlugin.clearRegions(); renderRegions() } }
$('#genBtn').onclick = async () => {
  if (!currentId) { setStatus('請先載入媒體'); return }
  setStatus('產生字幕中…（依長度與引擎可能數十秒～數分鐘）')
  const r = await fetch('/transcribe', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ id: currentId, regions: getRegions(),
                           show_lang: $('#showLang').checked,
                           engine: $('#engine').value, model: $('#model').value,
                           srt_mode: $('#srtMode').value }),
  })
  if (!r.ok) { setStatus('失敗：' + (await r.text())); return }
  const j = await r.json()
  setStatus('完成 ✓  SRT：' + j.srt)
}

// 供 headless 測試
window.__app = {
  getRegions,
  addRegion: (a, b) => regionsPlugin && regionsPlugin.addRegion({ start: a, end: b }),
  showTab,
}
