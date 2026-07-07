/* ============================================================
   KARAOKE ONLINE — app.js
   Arsitektur:
     Browser ──WS:8000/ws/audio──► Proxy ──UDP:5004──► Server
     Browser ◄─WS:8001/ws/score──────────────────────── Server
   ============================================================ */

'use strict';

// ─────────────────────────────────────────────
//  KONSTANTA & KONFIGURASI
// ─────────────────────────────────────────────
const PROXY_WS_URL  = 'ws://localhost:8000/ws/audio';
const SERVER_WS_URL = 'ws://localhost:8001/ws/score';
const SERVER_HTTP   = 'http://localhost:8001';

// ─────────────────────────────────────────────
//  STATE APLIKASI
// ─────────────────────────────────────────────
const state = {
  songs:        [],
  currentSong:  null,
  sessionId:    null,
  isRecording:  false,
  lrcLines:     [],    // [{time, text}, ...]
  startTime:    null,  // Date.now() saat mulai
  timerInterval: null,
  lastScore:    0,
  lastMode:     'raw',
  lastFrames:   0,
  lastScored:   0,
};

// WebSocket & Audio
let wsAudio = null;   // Proxy connection (kirim mic)
let wsScore = null;   // Server connection (terima skor)
let audioCtx    = null;
let micStream   = null;
let processor   = null;

// ─────────────────────────────────────────────
//  DOM REFS
// ─────────────────────────────────────────────
const $ = id => document.getElementById(id);

const pages = {
  select:  $('page-select'),
  karaoke: $('page-karaoke'),
  result:  $('page-result'),
};

// Page 1
const songGrid      = $('songGrid');
const dotProxy      = $('dotProxy');
const dotServer     = $('dotServer');
const btnLeaderboard= $('btnLeaderboard');

// Page 2
const karaokeTitle  = $('karaokeTitle');
const karaokeArtist = $('karaokeArtist');
const timerDisplay  = $('timerDisplay');
const sepBadge      = $('sepBadge');
const scoreNum      = $('scoreNum');
const scoreRingFill = $('scoreRingFill');
const videoPlayer   = $('videoPlayer');
const audioPlayer   = $('audioPlayer');
const trackerCanvas = $('pitchTrackerCanvas');
const noteFlash     = $('noteFlash');
const lyricPrev     = $('lyricPrev');
const lyricCurrent  = $('lyricCurrent');
const lyricNext     = $('lyricNext');
const pitchMeter    = $('pitchMeterCanvas');
const pitchHzLabel  = $('pitchHzLabel');
const currentNote   = $('currentNote');
const accuracyLabel = $('accuracyLabel');
const volumeBarFill = $('volumeBarFill');
const btnMic        = $('btnMic');
const micLabel      = $('micLabel');
const btnStopSong   = $('btnStopSong');
const btnBackToSelect = $('btnBackToSelect');

// Page 3
const resultSongName  = $('resultSongName');
const resultGrade     = $('resultGrade');
const resultGradeLabel= $('resultGradeLabel');
const resultScore     = $('resultScore');
const statAccuracy    = $('statAccuracy');
const statFrames      = $('statFrames');
const statMode        = $('statMode');
const btnSingAgain    = $('btnSingAgain');
const btnChooseOther  = $('btnChooseOther');

// Leaderboard
const leaderboardOverlay = $('leaderboardOverlay');
const leaderboardList    = $('leaderboardList');
const lbClose            = $('lbClose');

// Toast
const toast = $('toast');

// ─────────────────────────────────────────────
//  PAGE NAVIGATION
// ─────────────────────────────────────────────
function showPage(name) {
  Object.entries(pages).forEach(([k, el]) => {
    el.classList.toggle('active', k === name);
  });
}

// ─────────────────────────────────────────────
//  TOAST NOTIFICATION
// ─────────────────────────────────────────────
let toastTimer = null;
function showToast(msg, dur = 2500) {
  toast.textContent = msg;
  toast.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.remove('show'), dur);
}

// ─────────────────────────────────────────────
//  BACKGROUND CANVAS — Starfield Particles
// ─────────────────────────────────────────────
(function initBgCanvas() {
  const canvas = $('bgCanvas');
  const ctx    = canvas.getContext('2d');
  const stars  = [];
  const N      = 180;

  function resize() {
    canvas.width  = window.innerWidth;
    canvas.height = window.innerHeight;
  }
  window.addEventListener('resize', resize);
  resize();

  for (let i = 0; i < N; i++) {
    stars.push({
      x:    Math.random(),
      y:    Math.random(),
      r:    Math.random() * 1.5 + 0.3,
      speed: Math.random() * 0.00015 + 0.00005,
      alpha: Math.random() * 0.6 + 0.1,
      hue:  Math.random() < 0.3 ? 270 : (Math.random() < 0.5 ? 210 : 0),
    });
  }

  let t = 0;
  function draw() {
    t++;
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    // Deep gradient
    const grad = ctx.createRadialGradient(
      canvas.width * 0.5, canvas.height * 0.3, 0,
      canvas.width * 0.5, canvas.height * 0.5, canvas.width * 0.8
    );
    grad.addColorStop(0,   'rgba(90,20,140,0.12)');
    grad.addColorStop(0.5, 'rgba(20,10,60,0.08)');
    grad.addColorStop(1,   'rgba(0,0,0,0)');
    ctx.fillStyle = grad;
    ctx.fillRect(0, 0, canvas.width, canvas.height);

    stars.forEach(s => {
      s.y -= s.speed;
      if (s.y < 0) { s.y = 1; s.x = Math.random(); }
      const tw = Math.sin(t * 0.02 + s.x * 10) * 0.3 + 0.7;
      ctx.beginPath();
      ctx.arc(s.x * canvas.width, s.y * canvas.height, s.r, 0, Math.PI * 2);
      ctx.fillStyle = `hsla(${s.hue}, 80%, 80%, ${s.alpha * tw})`;
      ctx.fill();
    });
    requestAnimationFrame(draw);
  }
  draw();
})();

// ─────────────────────────────────────────────
//  VISUALIZER CANVAS (karaoke stage)
// ─────────────────────────────────────────────
const vizCanvas = $('visualizerCanvas');
const vizCtx    = vizCanvas.getContext('2d');
let vizAudioData = new Float32Array(256).fill(0);
let vizAnimId    = null;
let vizAnalyser  = null;

function startVisualizer(analyser) {
  vizAnalyser = analyser;
  drawVisualizer();
}
function stopVisualizer() {
  vizAnalyser = null;
  cancelAnimationFrame(vizAnimId);
}
function drawVisualizer() {
  vizAnimId = requestAnimationFrame(drawVisualizer);
  const w = vizCanvas.width, h = vizCanvas.height;
  vizCtx.clearRect(0, 0, w, h);

  // Gradient background
  const bg = vizCtx.createLinearGradient(0, 0, 0, h);
  bg.addColorStop(0, '#04040f');
  bg.addColorStop(1, '#0a0020');
  vizCtx.fillStyle = bg;
  vizCtx.fillRect(0, 0, w, h);

  // Get frequency data
  if (vizAnalyser) {
    const buf = new Uint8Array(vizAnalyser.frequencyBinCount);
    vizAnalyser.getByteFrequencyData(buf);
    for (let i = 0; i < vizAudioData.length; i++) {
      vizAudioData[i] = buf[i] / 255;
    }
  } else {
    // Idle animation (slow sine waves)
    const t = Date.now() / 1000;
    for (let i = 0; i < vizAudioData.length; i++) {
      vizAudioData[i] = (Math.sin(t * 0.5 + i * 0.08) * 0.5 + 0.5) * 0.12;
    }
  }

  // Draw bars
  const bars = Math.min(vizAudioData.length, 128);
  const barW = w / bars;
  for (let i = 0; i < bars; i++) {
    const v   = vizAudioData[i];
    const bh  = v * h * 0.7;
    const hue = 250 + i * 0.7;
    const x   = i * barW;

    const g = vizCtx.createLinearGradient(0, h - bh, 0, h);
    g.addColorStop(0, `hsla(${hue}, 90%, 70%, 0.9)`);
    g.addColorStop(1, `hsla(${hue}, 70%, 40%, 0.2)`);
    vizCtx.fillStyle = g;
    vizCtx.fillRect(x, h - bh, barW - 1, bh);
  }

  // Reflection (mirrored, faint)
  vizCtx.save();
  vizCtx.scale(1, -0.25);
  vizCtx.translate(0, -h * 4.2);
  vizCtx.globalAlpha = 0.15;
  for (let i = 0; i < bars; i++) {
    const v = vizAudioData[i];
    const bh = v * h * 0.7;
    const hue = 250 + i * 0.7;
    vizCtx.fillStyle = `hsla(${hue}, 80%, 60%, 0.5)`;
    vizCtx.fillRect(i * barW, h - bh, barW - 1, bh);
  }
  vizCtx.restore();
  vizCtx.globalAlpha = 1;
}

function resizeCanvases() {
  vizCanvas.width  = vizCanvas.offsetWidth;
  vizCanvas.height = vizCanvas.offsetHeight;
  pitchMeter.width = pitchMeter.offsetWidth;
  if (trackerCanvas) {
    trackerCanvas.width  = trackerCanvas.offsetWidth;
    trackerCanvas.height = trackerCanvas.offsetHeight;
  }
}
window.addEventListener('resize', resizeCanvases);

// ─────────────────────────────────────────────
//  PITCH TRACKER (scrolling segmented bar)
// ─────────────────────────────────────────────

// Setiap entry: { hz, rms, label, ts }
const TRACKER_HISTORY_SEC = 6;   // Berapa detik sejarah yang tampil
const TRACKER_SAMPLE_MS   = 100; // Frekuensi sampling (ms)

let trackerBuffer = []; // [{hz, rms, label, ts}, ...]
let trackerAnimId = null;

function pushTrackerSample(hz, rms, label) {
  const now = Date.now();
  trackerBuffer.push({ hz, rms, label, ts: now });
  // Buang data lebih dari N detik yang lalu
  const cutoff = now - TRACKER_HISTORY_SEC * 1000;
  while (trackerBuffer.length > 0 && trackerBuffer[0].ts < cutoff) {
    trackerBuffer.shift();
  }
}

function startTrackerLoop() {
  if (trackerAnimId) cancelAnimationFrame(trackerAnimId);
  function loop() {
    drawPitchTracker();
    trackerAnimId = requestAnimationFrame(loop);
  }
  loop();
}

function stopTrackerLoop() {
  if (trackerAnimId) { cancelAnimationFrame(trackerAnimId); trackerAnimId = null; }
  trackerBuffer = [];
  // Clear canvas
  if (trackerCanvas) {
    const ctx = trackerCanvas.getContext('2d');
    ctx.clearRect(0, 0, trackerCanvas.width, trackerCanvas.height);
  }
}

function drawPitchTracker() {
  if (!trackerCanvas) return;
  const ctx = trackerCanvas.getContext('2d');
  const W = trackerCanvas.width, H = trackerCanvas.height;
  if (!W || !H) return;

  // Background
  ctx.fillStyle = 'rgba(8,12,28,0.95)';
  ctx.fillRect(0, 0, W, H);

  // Grid lines horizontal (pitch rows)
  const rowCount = 8;
  for (let i = 0; i <= rowCount; i++) {
    const y = (i / rowCount) * H;
    ctx.strokeStyle = 'rgba(255,255,255,0.04)';
    ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(W, y); ctx.stroke();
  }

  // Playhead position: 22% dari kiri
  const PLAYHEAD_X = Math.round(W * 0.22);

  // Pitch range (Hz log scale mapping ke Y)
  const HZ_MIN = 80, HZ_MAX = 1000;
  const hzToY = hz => {
    if (hz <= 0) return H * 0.5;
    const logMin = Math.log2(HZ_MIN), logMax = Math.log2(HZ_MAX);
    const t = (Math.log2(Math.max(HZ_MIN, Math.min(HZ_MAX, hz))) - logMin) / (logMax - logMin);
    return H * (1 - t);
  };

  const now = Date.now();
  const windowMs = TRACKER_HISTORY_SEC * 1000;

  // Draw segments
  // Kelompokkan sample berurutan menjadi segmen (jika hz aktif)
  const SEG_GAP_MS = 250;  // gap > 250ms = segmen baru
  const BAR_H = Math.max(6, H * 0.13);

  if (trackerBuffer.length > 0) {
    let segStart = null, segEnd = null, segHz = 0, segLabel = '';
    let segCount = 0;

    const flushSeg = () => {
      if (segStart === null || segHz <= 0) return;
      const xEnd   = PLAYHEAD_X + ((segEnd - now) / windowMs) * (W - PLAYHEAD_X);
      const xStart = PLAYHEAD_X + ((segStart - now) / windowMs) * (W - PLAYHEAD_X);
      if (xEnd < 0) return; // sudah terlalu lama

      const x0 = Math.max(0, xStart);
      const x1 = Math.min(W, xEnd + 2);
      if (x1 <= x0) return;

      const y0  = hzToY(segHz) - BAR_H / 2;
      const grad = ctx.createLinearGradient(x0, 0, x1, 0);

      // Warna berdasarkan akurasi
      if (segLabel === 'PERFECT') {
        grad.addColorStop(0, 'rgba(16,185,129,0.7)');
        grad.addColorStop(1, 'rgba(16,185,129,1)');
      } else if (segLabel === 'GOOD') {
        grad.addColorStop(0, 'rgba(56,189,248,0.7)');
        grad.addColorStop(1, 'rgba(96,165,250,1)');
      } else if (segLabel === 'OK') {
        grad.addColorStop(0, 'rgba(245,158,11,0.7)');
        grad.addColorStop(1, 'rgba(251,191,36,1)');
      } else {
        // Default cyan (no label yet)
        grad.addColorStop(0, 'rgba(56,189,248,0.65)');
        grad.addColorStop(1, 'rgba(56,189,248,0.9)');
      }

      ctx.fillStyle = grad;
      ctx.shadowBlur = 6;
      ctx.shadowColor = segLabel === 'PERFECT' ? 'rgba(16,185,129,0.5)' : 'rgba(56,189,248,0.4)';
      ctx.beginPath();
      ctx.roundRect(x0, y0, x1 - x0, BAR_H, 3);
      ctx.fill();
      ctx.shadowBlur = 0;
    };

    for (let i = 0; i < trackerBuffer.length; i++) {
      const s = trackerBuffer[i];
      if (s.hz > 0) {
        if (segStart === null) {
          segStart = s.ts; segHz = s.hz; segLabel = s.label;
        } else if (s.ts - segEnd > SEG_GAP_MS) {
          flushSeg();
          segStart = s.ts; segHz = s.hz; segLabel = s.label;
        } else {
          // Update running average hz
          segHz = (segHz * segCount + s.hz) / (segCount + 1);
          if (s.label && s.label !== '') segLabel = s.label;
        }
        segEnd = s.ts;
        segCount++;
      } else {
        flushSeg();
        segStart = null; segEnd = null; segHz = 0; segLabel = ''; segCount = 0;
      }
    }
    flushSeg();
  }

  // Draw playhead line
  ctx.strokeStyle = 'rgba(255,255,255,0.9)';
  ctx.lineWidth = 2;
  ctx.beginPath(); ctx.moveTo(PLAYHEAD_X, 0); ctx.lineTo(PLAYHEAD_X, H); ctx.stroke();

  // Draw playhead arrow (triangle)
  const arrowY = H / 2;
  ctx.fillStyle = 'rgba(255,255,255,0.9)';
  ctx.beginPath();
  ctx.moveTo(PLAYHEAD_X - 10, arrowY - 7);
  ctx.lineTo(PLAYHEAD_X - 10, arrowY + 7);
  ctx.lineTo(PLAYHEAD_X + 2,  arrowY);
  ctx.closePath();
  ctx.fill();

  // Current pitch dot on playhead
  const lastSample = trackerBuffer[trackerBuffer.length - 1];
  if (lastSample && lastSample.hz > 0 && (now - lastSample.ts) < 300) {
    const dotY = hzToY(lastSample.hz);
    ctx.beginPath();
    ctx.arc(PLAYHEAD_X, dotY, 6, 0, Math.PI * 2);
    ctx.fillStyle = 'rgba(232,121,249,1)';
    ctx.shadowBlur = 14;
    ctx.shadowColor = '#e879f9';
    ctx.fill();
    ctx.shadowBlur = 0;
  }

  // Future area tint (right of playhead)
  ctx.fillStyle = 'rgba(255,255,255,0.015)';
  ctx.fillRect(PLAYHEAD_X, 0, W - PLAYHEAD_X, H);
}

// ─────────────────────────────────────────────
//  PITCH METER CANVAS
// ─────────────────────────────────────────────
let pitchMeterState = { userHz: 0, refHz: 0, rms: 0 };

function drawPitchMeter() {
  const { userHz, refHz, rms } = pitchMeterState;
  const w = pitchMeter.width, h = pitchMeter.height;
  const ctx = pitchMeter.getContext('2d');
  if (!w || !h) return;

  ctx.clearRect(0, 0, w, h);

  // Background
  const bg = ctx.createLinearGradient(0, 0, w, 0);
  bg.addColorStop(0,   'rgba(20,10,50,0.9)');
  bg.addColorStop(0.5, 'rgba(10,15,40,0.9)');
  bg.addColorStop(1,   'rgba(20,10,50,0.9)');
  ctx.fillStyle = bg;
  ctx.fillRect(0, 0, w, h);

  // Log frequency scale: 80–1100 Hz
  const LOG_MIN = Math.log2(80);
  const LOG_MAX = Math.log2(1100);
  const freqToX = hz => {
    if (hz <= 0) return -10;
    return ((Math.log2(hz) - LOG_MIN) / (LOG_MAX - LOG_MIN)) * w;
  };

  // Colored zones
  const zones = [
    { f: 80,  t: 200,  c: 'rgba(59,130,246,0.18)' },
    { f: 200, t: 500,  c: 'rgba(16,185,129,0.18)' },
    { f: 500, t: 1100, c: 'rgba(239,68,68,0.18)'  },
  ];
  zones.forEach(z => {
    ctx.fillStyle = z.c;
    ctx.fillRect(freqToX(z.f), 0, freqToX(z.t) - freqToX(z.f), h);
  });

  // Note reference lines (faint)
  const refs = [82.4, 130.8, 196, 261.6, 329.6, 392, 523.3, 659.3, 783.9];
  refs.forEach(f => {
    const x = freqToX(f);
    ctx.strokeStyle = 'rgba(255,255,255,0.07)';
    ctx.lineWidth = 1;
    ctx.setLineDash([2, 4]);
    ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, h); ctx.stroke();
    ctx.setLineDash([]);
  });

  // Reference pitch (purple dashed)
  if (refHz > 0) {
    const rx = freqToX(refHz);
    ctx.strokeStyle = 'rgba(168,85,247,0.7)';
    ctx.lineWidth   = 2;
    ctx.setLineDash([5, 5]);
    ctx.beginPath(); ctx.moveTo(rx, 0); ctx.lineTo(rx, h); ctx.stroke();
    ctx.setLineDash([]);
  }

  // User pitch bar
  if (userHz > 0) {
    const ux   = freqToX(userHz);
    const barH = Math.max(6, Math.min(h - 8, h * rms * 6));
    const by   = (h - barH) / 2;

    // Glow
    ctx.shadowBlur  = 16;
    ctx.shadowColor = '#c084fc';
    const pg = ctx.createLinearGradient(ux - 10, 0, ux + 10, 0);
    pg.addColorStop(0,   'rgba(168,85,247,0.6)');
    pg.addColorStop(0.5, 'rgba(232,121,249,1)');
    pg.addColorStop(1,   'rgba(168,85,247,0.6)');
    ctx.fillStyle = pg;
    ctx.beginPath();
    ctx.roundRect(ux - 8, by, 16, barH, 5);
    ctx.fill();
    ctx.shadowBlur = 0;
  }
}

// ─────────────────────────────────────────────
//  SCORE RING UPDATE
// ─────────────────────────────────────────────
const RING_CIRC = 163.4;
function updateScoreRing(score) {
  const offset = RING_CIRC * (1 - Math.min(score, 100) / 100);
  scoreRingFill.style.strokeDashoffset = offset;
  scoreNum.textContent = Math.round(score);
}

// ─────────────────────────────────────────────
//  LRC PARSER
// ─────────────────────────────────────────────
function parseLRC(text) {
  const lines = [];
  const re    = /\[(\d{2}):(\d{2})[.:](\d{2,3})\](.*)/g;
  let m;
  while ((m = re.exec(text)) !== null) {
    const min = parseInt(m[1]);
    const sec = parseInt(m[2]);
    const ms  = m[3].length === 2 ? parseInt(m[3]) * 10 : parseInt(m[3]);
    const t   = min * 60 + sec + ms / 1000;
    const txt = m[4].trim();
    if (txt) lines.push({ time: t, text: txt });
  }
  lines.sort((a, b) => a.time - b.time);
  return lines;
}

function updateLyrics(elapsed) {
  const lines = state.lrcLines;
  if (!lines.length) return;

  let idx = -1;
  for (let i = 0; i < lines.length; i++) {
    if (lines[i].time <= elapsed) idx = i;
    else break;
  }

  lyricPrev.textContent    = idx > 0          ? lines[idx - 1].text : '';
  lyricCurrent.textContent = idx >= 0         ? lines[idx].text     : 'Bersiap…';
  lyricNext.textContent    = idx < lines.length - 1 ? lines[idx + 1].text : '';

  if (idx >= 0) lyricCurrent.style.animation = 'none';
  requestAnimationFrame(() => {
    lyricCurrent.style.animation = '';
  });
}

// ─────────────────────────────────────────────
//  TIMER
// ─────────────────────────────────────────────
function startTimer() {
  state.startTime = Date.now();
  state.timerInterval = setInterval(() => {
    let elapsed = 0;
    if (state.currentSong && state.currentSong.has_audio && !audioPlayer.paused) {
      elapsed = audioPlayer.currentTime;
    } else if (state.currentSong && state.currentSong.has_video && !videoPlayer.paused) {
      elapsed = videoPlayer.currentTime;
    } else {
      elapsed = (Date.now() - state.startTime) / 1000;
    }
    const m = Math.floor(elapsed / 60);
    const s = Math.floor(elapsed % 60).toString().padStart(2, '0');
    timerDisplay.textContent = `${m}:${s}`;
    updateLyrics(elapsed);
  }, 200);
}
function stopTimer() {
  clearInterval(state.timerInterval);
  state.timerInterval = null;
}

// ─────────────────────────────────────────────
//  WEBSOCKET — Score (Server → Browser)
// ─────────────────────────────────────────────
function connectScoreWS() {
  if (wsScore) { try { wsScore.close(); } catch(_) {} }
  wsScore = new WebSocket(SERVER_WS_URL);

  wsScore.onopen = () => {
    console.log('[WS/score] Terhubung ke server');
    dotServer.classList.add('connected');
    // Keep-alive ping setiap 15s
    wsScore._pingInterval = setInterval(() => {
      if (wsScore.readyState === WebSocket.OPEN)
        wsScore.send(JSON.stringify({ type: 'ping' }));
    }, 15000);
  };

  wsScore.onmessage = (event) => {
    try {
      const d = JSON.parse(event.data);
      if (d.type === 'connected' || d.type === 'pong') return;
      handleScoreData(d);
    } catch (_) {}
  };

  wsScore.onclose = () => {
    dotServer.classList.remove('connected');
    clearInterval(wsScore._pingInterval);
    // Reconnect setelah 3 detik
    setTimeout(() => {
      if (pages.karaoke.classList.contains('active')) connectScoreWS();
    }, 3000);
  };

  wsScore.onerror = () => {
    console.warn('[WS/score] Koneksi gagal — server aktif?');
  };
}

function handleScoreData(d) {
  if (!state.isRecording) return;

  // Score
  state.lastScore  = d.session_score || 0;
  state.lastMode   = d.sep_mode || 'raw';

  updateScoreRing(state.lastScore);

  // Sep badge
  sepBadge.textContent = (d.sep_mode || 'raw').toUpperCase();
  sepBadge.className   = `sep-badge ${d.sep_mode || 'raw'}`;

  // Note display
  const pitchHz = d.pitch_hz || 0;
  const note    = d.note    || '--';
  const rms     = d.rms     || 0;

  // Push ke pitch tracker
  pushTrackerSample(d.is_singing ? pitchHz : 0, rms, d.label || '');

  currentNote.textContent  = note;
  pitchHzLabel.textContent = pitchHz > 0 ? `${pitchHz.toFixed(1)} Hz` : '— Hz';
  noteFlash.textContent    = d.note_short || (pitchHz > 0 ? note : '');
  noteFlash.classList.toggle('singing', d.is_singing);

  // Volume bar
  const vol = Math.min(100, rms * 800);
  volumeBarFill.style.width = `${vol}%`;

  // Accuracy label
  const label = d.label || '';
  accuracyLabel.textContent = label;
  accuracyLabel.className   = 'accuracy-label ' + (
    label === 'PERFECT' ? 'acc-perfect' :
    label === 'GOOD'    ? 'acc-good'    :
    label === 'OK'      ? 'acc-ok'      :
    label === 'MISS'    ? 'acc-miss'    : ''
  );

  // Pitch meter
  pitchMeterState = {
    userHz: pitchHz,
    refHz:  d.ref_hz || 0,
    rms:    rms,
  };
  drawPitchMeter();

  // Frames stats
  state.lastFrames = d.total_frames || state.lastFrames;
  state.lastScored = d.scored_frames || state.lastScored;
}

// ─────────────────────────────────────────────
//  MIC — AudioContext + WebSocket Audio Proxy
// ─────────────────────────────────────────────
async function startMic() {
  try {
    micStream = await navigator.mediaDevices.getUserMedia({ audio: true, video: false });
    audioCtx  = new AudioContext({ sampleRate: 48000 });
    const source   = audioCtx.createMediaStreamSource(micStream);
    const analyser = audioCtx.createAnalyser();
    analyser.fftSize = 512;

    // Connect to Proxy via WebSocket
    wsAudio = new WebSocket(PROXY_WS_URL);
    wsAudio.binaryType = 'arraybuffer';

    wsAudio.onopen = () => {
      console.log('[WS/audio] Terhubung ke proxy');
      dotProxy.classList.add('connected');

      processor = audioCtx.createScriptProcessor(4096, 1, 1);

      processor.onaudioprocess = (e) => {
        const raw = e.inputBuffer.getChannelData(0);

        // Convert float32 → int16 PCM untuk dikirim ke proxy
        const i16 = new Int16Array(raw.length);
        for (let i = 0; i < raw.length; i++) {
          const s = Math.max(-1, Math.min(1, raw[i]));
          i16[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
        }

        if (wsAudio.readyState === WebSocket.OPEN)
          wsAudio.send(i16.buffer);
      };

      source.connect(analyser);
      source.connect(processor);
      processor.connect(audioCtx.destination);

      startVisualizer(analyser);
      startTrackerLoop();

      state.isRecording = true;
      btnMic.classList.add('recording');
      micLabel.textContent = 'Sedang merekam…';
      showToast('🎤 Mikrofon aktif — mulai bernyanyi!');
    };

    wsAudio.onerror = () => {
      showToast('⚠️ Proxy tidak aktif (port 8000)', 4000);
      stopMic();
    };

    wsAudio.onclose = () => {
      dotProxy.classList.remove('connected');
      console.log('[WS/audio] Terputus dari proxy');
    };

  } catch (err) {
    console.error('Gagal akses mikrofon:', err);
    showToast('❌ Izin mikrofon diperlukan', 3500);
  }
}

function stopMic() {
  if (processor) { processor.disconnect(); processor = null; }
  if (audioCtx)  { audioCtx.close();       audioCtx  = null; }
  if (micStream)  { micStream.getTracks().forEach(t => t.stop()); micStream = null; }
  if (wsAudio)   { wsAudio.close();         wsAudio   = null; }

  stopVisualizer();
  stopTrackerLoop();
  dotProxy.classList.remove('connected');
  state.isRecording = false;
  btnMic.classList.remove('recording');
  micLabel.textContent = 'Tap untuk mulai';
  volumeBarFill.style.width = '0%';
  pitchMeterState = { userHz: 0, refHz: 0, rms: 0 };
  drawPitchMeter();
}

// ─────────────────────────────────────────────
//  SONG SELECTION
// ─────────────────────────────────────────────
function genreBadge(genre) {
  const g = (genre || '').toLowerCase();
  const cls = g.includes('pop')       ? 'badge-pop'
            : g.includes('folk')      ? 'badge-folk'
            : g.includes('classical') ? 'badge-classical'
            : 'badge-default';
  const icon = g.includes('pop') ? '🎵' : g.includes('classical') ? '🎻' : '🎶';
  return `<div class="card-badge ${cls}">${icon} ${genre || 'music'}</div>`;
}

function fmtDur(sec) {
  if (!sec) return '—';
  const m = Math.floor(sec / 60);
  const s = (sec % 60).toString().padStart(2, '0');
  return `${m}:${s}`;
}

function renderSongs(songs) {
  songGrid.innerHTML = '';
  if (!songs.length) {
    songGrid.innerHTML = `<div style="color:var(--text-dim);grid-column:1/-1;text-align:center;padding:40px;font-size:0.9rem">
      Tidak ada lagu. Jalankan <code>python server/init_db.py</code> terlebih dahulu.
    </div>`;
    return;
  }

  songs.forEach(song => {
    const tags = [];
    if (song.has_video)  tags.push('<span class="tag">📹 Video</span>');
    if (song.has_lyrics) tags.push('<span class="tag">📄 Lirik</span>');
    tags.push(`<span class="tag">🎵 x${song.play_count}</span>`);

    const card = document.createElement('div');
    card.className = 'song-card';
    card.innerHTML = `
      ${genreBadge(song.genre)}
      <div class="song-title">${song.title}</div>
      <div class="song-artist">${song.artist}</div>
      <div class="song-meta">
        <div class="song-tags">${tags.join('')}</div>
        <div class="song-duration">${fmtDur(song.duration_sec)}</div>
        <button class="play-btn" title="Pilih lagu">▶</button>
      </div>`;
    card.addEventListener('click', () => selectSong(song));
    songGrid.appendChild(card);
  });
}

async function fetchSongs() {
  try {
    const res  = await fetch(`${SERVER_HTTP}/api/songs`);
    const data = await res.json();
    state.songs = data.data || [];
    renderSongs(state.songs);
  } catch (e) {
    console.warn('Server tidak aktif:', e);
    songGrid.innerHTML = `<div style="color:var(--text-dim);grid-column:1/-1;text-align:center;padding:40px;font-size:0.9rem">
      ⚠️ Server tidak aktif. Jalankan <code>uvicorn main:app --port 8001</code> di folder <code>server/</code>
    </div>`;
  }
}

// ─────────────────────────────────────────────
//  SELECT & START SONG
// ─────────────────────────────────────────────
async function selectSong(song) {
  state.currentSong = song;

  // Update header
  karaokeTitle.textContent  = song.title;
  karaokeArtist.textContent = song.artist;
  resultSongName.textContent = `${song.title} — ${song.artist}`;

  // Reset display
  updateScoreRing(0);
  lyricCurrent.textContent = 'Bersiap…';
  lyricPrev.textContent    = '';
  lyricNext.textContent    = '';
  noteFlash.textContent    = '';
  timerDisplay.textContent = '0:00';
  state.lrcLines           = [];
  state.lastScore          = 0;

  showPage('karaoke');

  // Resize canvases after page is visible
  setTimeout(resizeCanvases, 50);

  // 1. Load lyrics (if any)
  if (song.has_lyrics) {
    try {
      const r = await fetch(`${SERVER_HTTP}/api/lyrics/${song.id}`);
      const d = await r.json();
      if (d.lyrics) {
        state.lrcLines = parseLRC(d.lyrics);
        lyricCurrent.textContent = 'Tekan 🎤 untuk mulai bernyanyi';
        showToast(`📄 ${state.lrcLines.length} baris lirik dimuat`);
      }
    } catch (_) {}
  }

  // 2. Setup video (if any) and audio (if any)
  let useVideoSound = false;

  if (song.has_video && song.video_file) {
    const videoUrl = `${SERVER_HTTP}/media/${encodeURIComponent(song.video_file.split('/').pop())}`;
    videoPlayer.src = videoUrl;
    videoPlayer.classList.remove('hidden');
    
    if (song.has_audio && song.audio_file) {
      // Skenario 1: Ada audio MP3 terpisah -> video dibisukan & diloop
      videoPlayer.muted = true;
      videoPlayer.loop = true;
    } else {
      // Skenario 2: Hanya ada video MP4 -> video bersuara & tidak diloop otomatis
      videoPlayer.muted = false;
      videoPlayer.loop = false;
      useVideoSound = true;
    }
  } else {
    videoPlayer.src = '';
    videoPlayer.classList.add('hidden');
  }

  if (song.has_audio && song.audio_file) {
    const audioUrl = `${SERVER_HTTP}/media/${encodeURIComponent(song.audio_file.split('/').pop())}`;
    audioPlayer.src = audioUrl;
  } else {
    audioPlayer.src = '';
  }

  // 3. Start score WebSocket
  connectScoreWS();

  // 4. Start session on server
  try {
    const r = await fetch(`${SERVER_HTTP}/api/session/start`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ song_id: song.id, user_id: 1 }),
    });
    const d = await r.json();
    state.sessionId = d.session_id;
    console.log('[Session] Dimulai, ID:', state.sessionId);
  } catch (e) {
    console.warn('[Session] Gagal start:', e);
    state.sessionId = null;
  }

  // 5. Start timer (and lyric sync)
  startTimer();

  // 6. Auto-start media if available
  if (song.has_audio && song.audio_file) {
    audioPlayer.play().catch(() => {});
  }
  if (song.has_video && song.video_file) {
    videoPlayer.play().catch(() => {});
  }
}

// ─────────────────────────────────────────────
//  STOP / END SESSION
// ─────────────────────────────────────────────
async function endKaraokeSession() {
  stopMic();
  stopTimer();

  if (audioPlayer) { audioPlayer.pause(); audioPlayer.src = ''; }
  if (videoPlayer) { videoPlayer.pause(); videoPlayer.src = ''; }
  if (wsScore)     { wsScore.close(); wsScore = null; }

  // Get final score from server
  let finalData = {
    final_score: state.lastScore,
    grade: computeClientGrade(state.lastScore),
    label: '',
    total_frames: state.lastFrames,
    scored_frames: state.lastScored,
  };

  if (state.sessionId) {
    try {
      const r = await fetch(`${SERVER_HTTP}/api/session/end`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          session_id: state.sessionId,
          song_id: state.currentSong?.id,
        }),
      });
      const d = await r.json();
      if (d.status === 'ok') finalData = d;
    } catch (_) {}
  }

  showResults(finalData);
}

function computeClientGrade(score) {
  if (score >= 95) return 'S';
  if (score >= 85) return 'A';
  if (score >= 75) return 'B';
  if (score >= 60) return 'C';
  if (score >= 40) return 'D';
  return 'F';
}

// ─────────────────────────────────────────────
//  SHOW RESULTS
// ─────────────────────────────────────────────
function showResults(data) {
  const score = Math.round(data.final_score || 0);
  const grade = data.grade || computeClientGrade(score);
  const label = data.label || ['', 'COBA LAGI', 'PERLU LATIHAN', 'CUKUP BAIK',
                               'BAGUS!', 'LUAR BIASA!', 'SEMPURNA!'][Math.ceil(score / 17)] || '';

  resultGrade.textContent      = grade;
  resultGradeLabel.textContent = label;
  resultScore.textContent      = score;

  const accuracy = data.total_frames
    ? Math.round((data.scored_frames / data.total_frames) * 100) + '%'
    : '—';
  statAccuracy.textContent = accuracy;
  statFrames.textContent   = data.scored_frames || 0;
  statMode.textContent     = (state.lastMode || 'raw').toUpperCase();

  showPage('result');

  // Confetti for good scores
  if (score >= 60) spawnConfetti(score >= 85 ? 80 : 40);
}

// ─────────────────────────────────────────────
//  CONFETTI
// ─────────────────────────────────────────────
function spawnConfetti(count = 60) {
  const wrap   = $('confettiWrap');
  wrap.innerHTML = '';
  const colors = ['#a855f7','#3b82f6','#ec4899','#10b981','#f59e0b','#e879f9','#60a5fa'];
  for (let i = 0; i < count; i++) {
    const el = document.createElement('div');
    el.className = 'confetti-piece';
    const c = colors[Math.floor(Math.random() * colors.length)];
    el.style.cssText = `
      left: ${Math.random() * 100}%;
      width: ${Math.random() * 8 + 5}px;
      height: ${Math.random() * 14 + 8}px;
      background: ${c};
      animation-duration: ${Math.random() * 2 + 2}s;
      animation-delay: ${Math.random() * 1.5}s;
      transform: rotate(${Math.random() * 360}deg);
    `;
    wrap.appendChild(el);
  }
}

// ─────────────────────────────────────────────
//  LEADERBOARD
// ─────────────────────────────────────────────
async function loadLeaderboard() {
  leaderboardList.innerHTML = '<div style="color:var(--text-dim);text-align:center;padding:20px">Memuat…</div>';
  leaderboardOverlay.classList.add('open');
  try {
    const r = await fetch(`${SERVER_HTTP}/api/sessions`);
    const d = await r.json();
    const rows = d.data || [];
    if (!rows.length) {
      leaderboardList.innerHTML = '<div style="color:var(--text-dim);text-align:center;padding:20px;font-size:0.85rem">Belum ada riwayat sesi.</div>';
      return;
    }
    leaderboardList.innerHTML = rows.map((row, i) => `
      <div class="lb-row">
        <span class="lb-rank">${i + 1}</span>
        <span class="lb-name">${row.song_title}<br>
          <span style="font-size:0.7rem;color:var(--text-dim)">${row.user_name}</span>
        </span>
        <span class="lb-grade">${row.grade}</span>
        <span class="lb-score">${Math.round(row.final_score)}</span>
      </div>
    `).join('');
  } catch (_) {
    leaderboardList.innerHTML = '<div style="color:var(--text-dim);text-align:center;padding:20px;font-size:0.85rem">Server tidak aktif.</div>';
  }
}

// ─────────────────────────────────────────────
//  SERVER STATUS CHECK
// ─────────────────────────────────────────────
async function checkServerStatus() {
  try {
    const r = await fetch(`${SERVER_HTTP}/api/status`);
    if (r.ok) dotServer.classList.add('connected');
    else      dotServer.classList.remove('connected');
  } catch (_) {
    dotServer.classList.remove('connected');
  }
}

// ─────────────────────────────────────────────
//  EVENT LISTENERS
// ─────────────────────────────────────────────

// Mic toggle
btnMic.addEventListener('click', async () => {
  if (state.isRecording) {
    stopMic();
  } else {
    await startMic();
  }
});

// Stop song → show results
btnStopSong.addEventListener('click', () => {
  endKaraokeSession();
});

// Back to select (mid-song)
btnBackToSelect.addEventListener('click', () => {
  stopMic();
  stopTimer();
  if (audioPlayer) { audioPlayer.pause(); audioPlayer.src = ''; }
  if (videoPlayer) { videoPlayer.pause(); videoPlayer.src = ''; }
  if (wsScore) { wsScore.close(); wsScore = null; }
  showPage('select');
  fetchSongs();  // Refresh to update play counts
});

// Result buttons
btnSingAgain.addEventListener('click', () => {
  if (state.currentSong) selectSong(state.currentSong);
});
btnChooseOther.addEventListener('click', () => {
  showPage('select');
  fetchSongs();
});

// Leaderboard
btnLeaderboard.addEventListener('click', loadLeaderboard);
lbClose.addEventListener('click', () => leaderboardOverlay.classList.remove('open'));
leaderboardOverlay.addEventListener('click', e => {
  if (e.target === leaderboardOverlay)
    leaderboardOverlay.classList.remove('open');
});

// ─────────────────────────────────────────────
//  INIT
// ─────────────────────────────────────────────
(async function init() {
  // Initial canvas size
  setTimeout(resizeCanvases, 100);

  // Draw empty pitch meter
  drawPitchMeter();

  // Start the visualizer for idle animation
  drawVisualizer();

  // Check server + fetch songs
  await checkServerStatus();
  fetchSongs();

  // Periodic status check (every 10s)
  setInterval(checkServerStatus, 10000);
})();