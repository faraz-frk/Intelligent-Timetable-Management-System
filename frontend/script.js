/* =====================================================
   Smart Timetable Scheduler - script.js
   ===================================================== */

// ---- Constants ----
const API = '/api';

// ---- Current user (set after auth check) ----
let currentUser = null;

// ---- Auth Guard + Role-based Init ----
(async function initApp() {
  try {
    const res = await fetch(`${API}/auth/me`, { credentials: 'include' });
    const data = await res.json();
    if (!data.authenticated) {
      window.location.href = '/login.html';
      return;
    }
    currentUser = data.user;

    // Show user info in header
    document.getElementById('headerUser').style.display = 'flex';
    document.getElementById('headerUsername').textContent   = currentUser.username;
    document.getElementById('headerSchoolName').textContent = currentUser.school_name || currentUser.username;

    // Apply role-based UI
    applyRoleUI(currentUser.role);
  } catch {
    window.location.href = '/login.html';
  }
})();

function applyRoleUI(role) {
  const uploadSection = document.getElementById('section-upload');

  if (role === 'admin') {
    // Admin: full access — upload + timetable + chatbot
    uploadSection.style.display = '';
    showSection('upload');

  } else if (role === 'teacher') {
    // Teacher: hide upload, show their personal schedule
    uploadSection.style.display = 'none';
    showSection('teacher');
    loadTeacherSchedule();
    loadTimetablesForChat();

  } else {
    // Student: hide upload, auto-load all timetables
    uploadSection.style.display = 'none';
    loadAndShowTimetables();
  }
}

async function loadTimetablesForChat() {
  try {
    const res = await fetch(`${API}/timetables`, { credentials: 'include' });
    if (!res.ok) {
      console.error('Unable to load timetables for chat:', await res.text());
      return;
    }

    allTimetables = await res.json();
    timetablesLoaded = Object.keys(allTimetables).length > 0;
  } catch (error) {
    console.error('Unable to load timetables for chat:', error);
  }
}

async function loadAndShowTimetables() {
  try {
    const res = await fetch(`${API}/timetables`, { credentials: 'include' });
    if (!res.ok) {
      // Timetables not ready yet — show a friendly message on the upload section
      const uploadSection = document.getElementById('section-upload');
      uploadSection.style.display = '';
      showSection('upload');
      document.querySelector('#section-upload .section-desc').textContent =
        'Timetables are not available yet. Please ask your admin to upload the teacher data.';
      document.getElementById('dropZone').style.display = 'none';
      return;
    }
    allTimetables = await res.json();
    timetablesLoaded = true;

    if (currentUser.role === 'student') {
      // Student: pre-select their class
      selectedClass   = currentUser.class_num;
      selectedSection = currentUser.section;
      showSection('timetable');
      _renderCurrentTimetable();
      // Update timetable section header to show their class
      const sectionTitle = document.querySelector('#section-timetable .section-title');
      if (sectionTitle) {
        sectionTitle.innerHTML = `<i class="fas fa-table"></i> Your Timetable — Class ${selectedClass}${selectedSection}`;
      }
    } else {
      selectedClass   = 1;
      selectedSection = 'A';
      showSection('timetable');
      _renderCurrentTimetable();
    }
  } catch {
    showSection('upload');
  }
}

async function loadTeacherSchedule() {
  const grid = document.getElementById('teacherScheduleGrid');
  try {
    const res = await fetch(`${API}/teacher/my-schedule`, { credentials: 'include' });
    const data = await res.json();
    if (!res.ok) {
      grid.innerHTML = `<div class="teacher-error"><i class="fas fa-exclamation-triangle"></i> ${data.error || 'Schedule not found.'}</div>`;
      return;
    }
    document.getElementById('teacherScheduleSubtitle').textContent =
      `Welcome, ${data.teacher_name}! Here is your weekly teaching schedule.`;
    grid.innerHTML = renderTeacherScheduleTable(data.schedule, data.teacher_name);
  } catch (e) {
    grid.innerHTML = `<div class="teacher-error"><i class="fas fa-exclamation-triangle"></i> Failed to load schedule. Please try again.</div>`;
  }
}

function renderTeacherScheduleTable(schedule, teacherName) {
  const days = ['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday'];
  // Find max periods in any day
  let maxP = 0;
  days.forEach(d => { if (schedule[d] && schedule[d].length > maxP) maxP = schedule[d].length; });
  if (maxP === 0) return `<div class="teacher-error">No teaching slots found for ${teacherName}.</div>`;

  let html = '<div class="teacher-table-wrap"><table class="teacher-table"><thead><tr><th>Day</th><th>Period</th><th>Time</th><th>Subject</th><th>Class</th></tr></thead><tbody>';
  days.forEach(day => {
    const slots = schedule[day] || [];
    if (slots.length === 0) {
      html += `<tr class="teacher-row-empty"><td>${day}</td><td colspan="4" class="no-class">Free Day</td></tr>`;
    } else {
      slots.forEach((slot, i) => {
        html += `<tr class="${i === 0 ? 'teacher-row-first' : ''}">`;
        if (i === 0) html += `<td class="day-cell" rowspan="${slots.length}">${day}</td>`;
        html += `
          <td class="period-num">Period ${slot.period}</td>
          <td class="slot-time">${slot.time}</td>
          <td class="slot-subj"><span class="subj-badge" style="background:${getSubjectColor(slot.subject)}">${slot.subject}</span></td>
          <td class="slot-class">Class ${slot.class_num}${slot.section}</td>
        </tr>`;
      });
    }
  });
  html += '</tbody></table></div>';
  return html;
}

function getSubjectColor(subject) {
  const colors = {
    'Telugu':'#ffe0e0','Hindi':'#e0f0ff','English':'#e0ffe0','Maths':'#f0e0ff',
    'Social':'#fff0e0','Physics':'#fffde0','Biology':'#eaffe0','Chemistry':'#fde0ff',
    'Environmental Science':'#e0feff','Computer':'#ffe0f0','Sports':'#fdf8e0',
    'History':'#fef0e0','Geography':'#e0f8ff','Science':'#e0feef',
  };
  return colors[subject] || '#f0f0f0';
}

async function handleLogout() {
  await fetch(`${API}/auth/logout`, { method: 'POST', credentials: 'include' });
  window.location.href = '/login.html';
}


const DAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];

const PERIODS = [
  { type: 'period', num: 1,  time: '09:00 - 09:45' },
  { type: 'period', num: 2,  time: '09:45 - 10:30' },
  { type: 'break',  label: 'Coffee Break',     time: '10:30 - 10:40' },
  { type: 'period', num: 3,  time: '10:40 - 11:25' },
  { type: 'period', num: 4,  time: '11:25 - 12:10' },
  { type: 'lunch',  label: 'Lunch Break',      time: '12:10 - 13:00' },
  { type: 'period', num: 5,  time: '13:00 - 13:45' },
  { type: 'period', num: 6,  time: '13:45 - 14:30' },
  { type: 'period', num: 7,  time: '14:30 - 15:15' },
  { type: 'period', num: 8,  time: '15:15 - 16:00' },
];

const SUBJECT_COLORS = {
  'Telugu':                 { bg: '#fde8e8', border: '#e74c3c' },
  'Hindi':                  { bg: '#e8f0fe', border: '#3b82f6' },
  'English':                { bg: '#e8fde8', border: '#27ae60' },
  'Social':                 { bg: '#fef3e8', border: '#f39c12' },
  'Maths':                  { bg: '#f0e8fe', border: '#8b5cf6' },
  'Environmental Science':  { bg: '#e8fefd', border: '#06b6d4' },
  'Computer':               { bg: '#fee8f0', border: '#ec4899' },
  'Physics':                { bg: '#fefde8', border: '#eab308' },
  'Biology':                { bg: '#eafee8', border: '#16a34a' },
  'Chemistry':              { bg: '#fde8fe', border: '#a855f7' },
  'History':                { bg: '#fef0e8', border: '#d97706' },
  'Geography':              { bg: '#e8f8fe', border: '#0891b2' },
  'Science':                { bg: '#e8feef', border: '#059669' },
  'Sports':                 { bg: '#fdf8e8', border: '#ca8a04' },
  'Art':                    { bg: '#ffe8f5', border: '#db2777' },
  'Music':                  { bg: '#ede8fe', border: '#7c3aed' },
  'PE':                     { bg: '#fdf8e8', border: '#ca8a04' },
  'Moral':                  { bg: '#f0fde8', border: '#65a30d' },
  'TBD':                    { bg: '#f5f5f5', border: '#9e9e9e' },
};

const LOADING_MESSAGES = [
  'Parsing teacher data...',
  'Applying CSP constraints...',
  'Assigning teachers to slots...',
  'Resolving scheduling conflicts...',
  'Building RAG knowledge base...',
  'Finalizing timetables...',
];

// ---- State ----
let allTimetables = {};
let currentView  = 'upload';
let selectedClass   = null;
let selectedSection = null;
let chatOpen     = false;
let timetablesLoaded = false;

// ============================================================
// SECTION NAVIGATION
// ============================================================
function showSection(name) {
  document.querySelectorAll('.app-section').forEach(s => s.classList.remove('active'));
  const target = document.getElementById('section-' + name);
  if (target) target.classList.add('active');
  currentView = name;
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

// ============================================================
// LOADING OVERLAY
// ============================================================
let loadingInterval = null;
let loadingStep = 0;

function showLoading() {
  document.getElementById('loadingOverlay').classList.remove('hidden');
  loadingStep = 0;
  updateLoadingMsg();
  const bar = document.getElementById('progressBar');
  bar.style.width = '0%';
  loadingInterval = setInterval(() => {
    loadingStep++;
    if (loadingStep < LOADING_MESSAGES.length) {
      updateLoadingMsg();
      const pct = Math.round((loadingStep / LOADING_MESSAGES.length) * 90);
      bar.style.width = pct + '%';
    }
  }, 1500);
}

function updateLoadingMsg() {
  const el = document.getElementById('loadingMsg');
  if (el) {
    el.style.opacity = '0';
    setTimeout(() => {
      el.textContent = LOADING_MESSAGES[Math.min(loadingStep, LOADING_MESSAGES.length - 1)];
      el.style.opacity = '1';
    }, 200);
  }
}

function hideLoading() {
  if (loadingInterval) { clearInterval(loadingInterval); loadingInterval = null; }
  document.getElementById('progressBar').style.width = '100%';
  setTimeout(() => {
    document.getElementById('loadingOverlay').classList.add('hidden');
  }, 500);
}

// ============================================================
// TOAST NOTIFICATIONS
// ============================================================
function showToast(message, type = 'info') {
  const icons = { success: 'fas fa-check-circle', error: 'fas fa-times-circle', info: 'fas fa-info-circle' };
  const container = document.getElementById('toastContainer');
  const toast = document.createElement('div');
  toast.className = 'toast ' + type;
  toast.innerHTML = '<i class="toast-icon ' + icons[type] + '"></i><span>' + escapeHtml(message) + '</span>';
  container.appendChild(toast);
  setTimeout(() => toast.remove(), 3100);
}

// ============================================================
// FILE HANDLING
// ============================================================
let selectedFile = null;

function setupDropZone() {
  const zone = document.getElementById('dropZone');
  const input = document.getElementById('fileInput');

  ['dragenter','dragover'].forEach(evt => {
    zone.addEventListener(evt, e => {
      e.preventDefault();
      zone.classList.add('drag-over');
    });
  });

  ['dragleave','dragend'].forEach(evt => {
    zone.addEventListener(evt, () => zone.classList.remove('drag-over'));
  });

  zone.addEventListener('drop', e => {
    e.preventDefault();
    zone.classList.remove('drag-over');
    const file = e.dataTransfer.files[0];
    if (file) setFile(file);
  });

  input.addEventListener('change', () => {
    if (input.files[0]) setFile(input.files[0]);
  });
}

function setFile(file) {
  const allowed = ['.csv', '.xlsx', '.xls'];
  if (!allowed.some(ext => file.name.toLowerCase().endsWith(ext))) {
    showToast('Please select a CSV or Excel (.xlsx / .xls) file.', 'error');
    return;
  }
  selectedFile = file;
  document.getElementById('selectedFileName').textContent = file.name + ' (' + formatBytes(file.size) + ')';
  document.getElementById('fileSelectedInfo').classList.remove('hidden');
  document.getElementById('dropZone').classList.add('file-chosen');
  document.getElementById('generateBtn').disabled = false;
  showToast('File selected: ' + file.name, 'success');
}

function clearFile() {
  selectedFile = null;
  document.getElementById('selectedFileName').textContent = 'No file chosen';
  document.getElementById('fileSelectedInfo').classList.add('hidden');
  document.getElementById('dropZone').classList.remove('file-chosen');
  document.getElementById('generateBtn').disabled = true;
  document.getElementById('fileInput').value = '';
}

function formatBytes(bytes) {
  if (bytes < 1024) return bytes + ' B';
  if (bytes < 1048576) return (bytes / 1024).toFixed(1) + ' KB';
  return (bytes / 1048576).toFixed(1) + ' MB';
}

// ============================================================
// UPLOAD & GENERATE
// ============================================================
async function handleUpload() {
  if (!selectedFile) { showToast('Please select a CSV file first.', 'error'); return; }

  showLoading();
  const formData = new FormData();
  formData.append('file', selectedFile);

  try {
    const res = await fetch(API + '/upload', {
      method: 'POST',
      body: formData,
      credentials: 'include',
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || data.message || 'Upload failed');

    allTimetables = data.timetables || data;
    timetablesLoaded = true;
    hideLoading();
    showToast('20 timetables generated successfully!', 'success');

    // Go straight to timetable view — default Class 1, Section A
    selectedClass   = 1;
    selectedSection = 'A';
    showSection('timetable');
    _renderCurrentTimetable();
  } catch (err) {
    hideLoading();
    showToast('Error: ' + err.message, 'error');
    console.error('Upload error:', err);
  }
}



// ============================================================
// DASHBOARD
// ============================================================
function renderDashboard() {
  const grid = document.getElementById('classGrid');
  grid.innerHTML = '';

  let totalPeriods = 0, totalAssigned = 0, totalTBD = 0;
  const keys = Object.keys(allTimetables);

  keys.forEach(key => {
    const tt = allTimetables[key];
    // API returns keys like "Class_1A" or "Class_10B"
    // Also handle plain "1A" format just in case
    const match = key.match(/^(?:Class_)?(\d+)([AB])$/);
    if (!match) return;
    const classNum = parseInt(match[1]);
    const section  = match[2];

    const { assigned, tbd, total, sportsDay } = countSlots(tt);
    totalPeriods  += total;
    totalAssigned += assigned;
    totalTBD      += tbd;

    const isJunior = classNum <= 5;
    const pct = total > 0 ? Math.round((assigned / total) * 100) : 0;

    const card = document.createElement('div');
    card.className = 'class-card ' + (isJunior ? 'junior' : 'senior');
    card.onclick = () => showTimetable(classNum, section);
    card.innerHTML = `
      <div class="card-badges">
        <span class="badge-class">Class ${classNum}</span>
        <span class="badge-section section-${section.toLowerCase()}">${section}</span>
      </div>
      <div class="card-class-name">Class ${classNum} &mdash; Section ${section}</div>
      <div class="card-sports"><i class="fas fa-running"></i> Sports: ${sportsDay || 'N/A'}</div>
      <div class="card-progress-wrap">
        <div class="card-progress-bar-bg">
          <div class="card-progress-bar-fill" style="width:${pct}%"></div>
        </div>
        <span class="card-progress-label">${assigned}/${total}</span>
      </div>
      <i class="fas fa-chevron-right card-arrow"></i>
    `;
    grid.appendChild(card);
  });

  // Stats
  animateCounter('statClasses', keys.length);
  animateCounter('statPeriods', totalPeriods);
  animateCounter('statAssigned', totalAssigned);
  animateCounter('statTBD', totalTBD);
}

function countSlots(tt) {
  let assigned = 0, tbd = 0, total = 0;
  let sportsDay = null;
  if (!tt || !tt.schedule) return { assigned, tbd, total, sportsDay };

  DAYS.forEach(day => {
    // schedule[day] is an array of slot objects: [{period, subject, teacher, time}, ...]
    const daySlots = tt.schedule[day];
    if (!Array.isArray(daySlots)) return;

    daySlots.forEach(slot => {
      const subj = slot.subject || '';
      total++;
      if (!subj || subj === 'TBD' || subj === '—') {
        tbd++;
      } else {
        assigned++;
        if (subj === 'Sports') sportsDay = day;
      }
    });
  });

  if (!sportsDay && tt.sports_day) sportsDay = tt.sports_day;
  return { assigned, tbd, total, sportsDay };
}

function animateCounter(id, target) {
  const el = document.getElementById(id);
  if (!el) return;
  let current = 0;
  const step = Math.ceil(target / 30);
  const timer = setInterval(() => {
    current = Math.min(current + step, target);
    el.textContent = current;
    if (current >= target) clearInterval(timer);
  }, 40);
}

// ============================================================
// TIMETABLE VIEW — Filter + Matrix Render
// ============================================================

// Called when a class card on the dashboard is clicked
function showTimetable(classNum, section) {
  selectedClass   = classNum;
  selectedSection = section;

  // Sync the dropdown and section buttons
  const dropdown = document.getElementById('classDropdown');
  if (dropdown) dropdown.value = String(classNum);
  _updateSectionButtons(section);

  showSection('timetable');
  _renderCurrentTimetable();
}

// Called when the class dropdown changes
function onFilterChange() {
  const dropdown = document.getElementById('classDropdown');
  selectedClass = parseInt(dropdown.value);
  _renderCurrentTimetable();
}

// Called when A or B section button is clicked
function selectSection(sec) {
  selectedSection = sec;
  _updateSectionButtons(sec);
  _renderCurrentTimetable();
}

// Highlight the active section button
function _updateSectionButtons(sec) {
  document.getElementById('btnSectionA').classList.toggle('active', sec === 'A');
  document.getElementById('btnSectionB').classList.toggle('active', sec === 'B');
}

// Render timetable for currently selected class + section
function _renderCurrentTimetable() {
  const classNum = selectedClass  || 1;
  const section  = selectedSection || 'A';
  const key = 'Class_' + classNum + section;
  const tt  = allTimetables[key];

  // Update display label
  document.getElementById('filterDisplay').textContent =
    'Class ' + classNum + ' \u2014 Section ' + section;

  if (!tt) {
    document.getElementById('timetableGrid').innerHTML =
      '<tr><td style="padding:2rem;color:#999">No timetable data. Please upload a CSV first.</td></tr>';
    document.getElementById('ttSportsDayText').textContent = '—';
    return;
  }

  // Sports day
  const sportsDay = tt.sports_day || '—';
  document.getElementById('ttSportsDayText').textContent = sportsDay;

  renderTimetable(classNum, section, tt, sportsDay);
}

// Build the matrix table:  ROWS = Days,  COLUMNS = Periods/Timings
function renderTimetable(classNum, section, tt, sportsDay) {
  const table  = document.getElementById('timetableGrid');
  const legend = document.getElementById('subjectLegend');
  const usedSubjects = new Set();
  table.innerHTML = '';

  // Sports period pair (e.g. [5, 6])
  const sportsPair = tt.sports_periods || [];
  const sp1 = sportsPair[0];
  const sp2 = sportsPair[1];

  // ── THEAD: Period headers ────────────────────────────────
  const thead   = document.createElement('thead');
  const headRow = document.createElement('tr');

  // First header cell: Day label
  const thDay = document.createElement('th');
  thDay.textContent = 'Day';
  thDay.className = 'day-col-header';
  headRow.appendChild(thDay);

  // Period headers
  PERIODS.forEach(slot => {
    const th = document.createElement('th');
    if (slot.type === 'break') {
      th.className = 'break-col-header';
      th.innerHTML = '<span class="col-label">Break</span><span class="col-time">' + slot.time + '</span>';
    } else if (slot.type === 'lunch') {
      th.className = 'lunch-col-header';
      th.innerHTML = '<span class="col-label">Lunch</span><span class="col-time">' + slot.time + '</span>';
    } else {
      th.className = 'period-col-header';
      th.innerHTML = '<span class="col-label">P' + slot.num + '</span><span class="col-time">' + slot.time + '</span>';
    }
    headRow.appendChild(th);
  });

  thead.appendChild(headRow);
  table.appendChild(thead);

  // ── TBODY: One row per day ──────────────────────────────
  const tbody = document.createElement('tbody');

  DAYS.forEach(day => {
    const tr = document.createElement('tr');
    if (day === sportsDay) tr.classList.add('sports-day-row');

    // Day name cell
    const tdDay = document.createElement('td');
    tdDay.className = 'day-name-cell';
    tdDay.innerHTML = '<span class="day-name">' + day.substring(0, 3) + '</span>' +
                      '<span class="day-full">' + day + '</span>';
    if (day === sportsDay) {
      tdDay.innerHTML += '<span class="sports-tag"><i class="fas fa-running"></i></span>';
    }
    tr.appendChild(tdDay);

    // Period cells for this day
    const daySlots = (tt.schedule || {})[day] || [];

    let skipPeriod = -1;  // period number to skip (merged into previous colspan cell)

    PERIODS.forEach((slot, idx) => {
      // Skip the second sports period (already merged into first)
      if (slot.type === 'period' && slot.num === skipPeriod) return;

      const td = document.createElement('td');

      if (slot.type === 'break') {
        td.className = 'break-cell';
        td.textContent = '☕';
        tr.appendChild(td);
        return;
      }

      if (slot.type === 'lunch') {
        td.className = 'lunch-cell';
        td.textContent = '🍽';
        tr.appendChild(td);
        return;
      }

      // Check: is this the first sports period on the sports day?
      const isSportsMerge = (day === sportsDay) &&
                            (slot.num === sp1) &&
                            (sp2 === sp1 + 1 || PERIODS[idx + 1]?.num === sp2);

      if (isSportsMerge) {
        td.colSpan = 2;
        skipPeriod = sp2;  // skip the next sports period cell
      }

      // Find matching slot data
      const slotData = Array.isArray(daySlots)
        ? daySlots.find(s => s.period === slot.num)
        : null;

      const subject = (slotData && slotData.subject) || 'TBD';
      const rawTeacher = (slotData && slotData.teacher) ? slotData.teacher : '';
      const isUnassigned = rawTeacher === 'Not Assigned' || rawTeacher === 'TBD';
      const isSportsTeacher = rawTeacher === 'Sports Teacher';

      td.className = 'period-cell';

      if (!subject || subject === 'TBD' || subject === '—') {
        td.classList.add('tbd-cell');
        td.innerHTML = '<span class="subject-name tbd-label">TBD</span>';
      } else {
        const cls = getSubjectClass(subject);
        td.classList.add(cls);
        if (subject === 'Sports') td.classList.add('sports-cell');
        let teacherHtml = '';
        if (rawTeacher) {
          const tClass = isUnassigned ? 'teacher-name unassigned' : isSportsTeacher ? 'teacher-name sports-teacher' : 'teacher-name';
          teacherHtml = '<span class="' + tClass + '"><i class="fas fa-user-tie"></i>' + escapeHtml(rawTeacher) + '</span>';
        }
        td.innerHTML = '<span class="subject-name">' + escapeHtml(subject) + '</span>' + teacherHtml;
        usedSubjects.add(subject);
      }

      tr.appendChild(td);
    });

    tbody.appendChild(tr);
  });

  table.appendChild(tbody);
  renderLegend(legend, usedSubjects);
}


function getSubjectClass(subject) {
  if (!subject) return 'subject-TBD';
  const clean = subject.replace(/\s+/g, '-').replace(/[^a-zA-Z0-9\-]/g, '');
  return 'subject-' + clean;
}

function renderLegend(container, subjects) {
  container.innerHTML = '';
  const title = document.createElement('div');
  title.className = 'legend-title';
  title.innerHTML = '<i class="fas fa-palette"></i> Subject Legend';
  container.appendChild(title);

  const items = document.createElement('div');
  items.className = 'legend-items';

  subjects.forEach(subj => {
    const colors = SUBJECT_COLORS[subj] || { bg: '#f0f0f0', border: '#999' };
    const item = document.createElement('div');
    item.className = 'legend-item';
    item.innerHTML = '<div class="legend-swatch" style="background:' + colors.bg + ';border-color:' + colors.border + '"></div><span>' + escapeHtml(subj) + '</span>';
    items.appendChild(item);
  });

  container.appendChild(items);
}

// ============================================================
// PDF EXPORT
// ============================================================
function exportCurrentPDF() {
  if (!selectedClass || !selectedSection) { showToast('No timetable selected.', 'error'); return; }
  window.open(API + '/export/pdf/' + selectedClass + '/' + selectedSection, '_blank');
}

function exportAllPDF() {
  window.open(API + '/export/pdf/all', '_blank');
}

// ============================================================
// CHAT (RAG)
// ============================================================
function toggleChat() {
  chatOpen = !chatOpen;
  const panel = document.getElementById('chatPanel');
  if (chatOpen) {
    panel.classList.remove('hidden');
    document.getElementById('chatInput').focus();
  } else {
    panel.classList.add('hidden');
  }
}

async function sendChatMessage() {
  const input = document.getElementById('chatInput');
  const text  = input.value.trim();
  if (!text) return;
  input.value = '';

  if (!timetablesLoaded) {
    appendMessage('Please generate timetables first before asking questions.', false);
    return;
  }

  appendMessage(text, true);
  hideSuggestions();
  const typingId = showTyping();

  try {
    const res = await fetch(API + '/ask', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question: text }),
      credentials: 'include',
    });
    const data = await res.json();
    removeTyping(typingId);
    const answer = data.answer || data.response || data.message || 'Sorry, I could not find an answer.';
    appendMessage(answer, false);
  } catch (err) {
    removeTyping(typingId);
    appendMessage('Error connecting to backend. Please make sure the Flask server is running.', false);
  }
}

function appendMessage(text, isUser) {
  const container = document.getElementById('chatMessages');
  const msg = document.createElement('div');
  msg.className = 'chat-msg ' + (isUser ? 'user' : 'bot');
  msg.innerHTML = '<div class="chat-bubble">' + escapeHtml(text) + '</div>';
  container.appendChild(msg);
  container.scrollTop = container.scrollHeight;
}

function showTyping() {
  const container = document.getElementById('chatMessages');
  const id = 'typing-' + Date.now();
  const el = document.createElement('div');
  el.id = id;
  el.className = 'chat-msg bot';
  el.innerHTML = '<div class="typing-indicator"><div class="typing-dot"></div><div class="typing-dot"></div><div class="typing-dot"></div></div>';
  container.appendChild(el);
  container.scrollTop = container.scrollHeight;
  return id;
}

function removeTyping(id) {
  const el = document.getElementById(id);
  if (el) el.remove();
}

function useSuggestion(text) {
  document.getElementById('chatInput').value = text;
  sendChatMessage();
}

function hideSuggestions() {
  const sug = document.getElementById('chatSuggestions');
  if (sug) sug.style.display = 'none';
}


// ============================================================
// UTILITIES
// ============================================================
function escapeHtml(str) {
  if (typeof str !== 'string') str = String(str || '');
  return str.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

// ============================================================
// INIT / EVENT LISTENERS
// ============================================================
document.addEventListener('DOMContentLoaded', () => {
  // Setup drag & drop
  setupDropZone();

  // Chat input Enter key
  document.getElementById('chatInput').addEventListener('keydown', e => {
    if (e.key === 'Enter') sendChatMessage();
  });

  // Show upload section by default
  showSection('upload');
});
