// ============ STATE ============
let currentTab = 'dashboard';
let usersState = { data: [], page: 1, perPage: 25, sort: 'created_at', order: 'desc', q: '' };
let routesState = { data: [], page: 1, perPage: 25, sort: 'created_at', order: 'desc', q: '' };
let scoredState = { data: [], page: 1, perPage: 25, sort: 'scored_at', order: 'desc', q: '' };
let searchTimers = {};

// ============ AUTH CHECK ============
async function checkAdminAuth() {
  try {
    const resp = await fetch('/api/auth/me');
    const data = await resp.json();
    if (!data.authenticated || !data.user.is_admin) {
      window.location.href = '/app.html';
      return;
    }
    document.getElementById('adminUserName').textContent = data.user.name || data.user.email || '';
    loadDashboard();
  } catch (e) {
    window.location.href = '/app.html';
  }
}

// ============ TABS ============
function switchTab(tab) {
  currentTab = tab;
  document.querySelectorAll('.admin-tab').forEach(t => t.classList.toggle('active', t.dataset.tab === tab));
  document.querySelectorAll('.admin-section').forEach(s => s.classList.remove('visible'));

  if (tab === 'dashboard') { document.getElementById('dashboardSection').classList.add('visible'); loadDashboard(); }
  else if (tab === 'users') { document.getElementById('usersSection').classList.add('visible'); fetchUsers(); }
  else if (tab === 'routes') { document.getElementById('routesSection').classList.add('visible'); fetchRoutes(); }
  else if (tab === 'scored') { document.getElementById('scoredSection').classList.add('visible'); fetchScored(); }
}

// ============ DASHBOARD ============
async function loadDashboard() {
  try {
    const resp = await fetch('/api/admin/stats');
    const data = await resp.json();
    document.getElementById('statUsers').textContent = data.total_users;
    document.getElementById('statNew7d').textContent = data.new_users_7d;
    document.getElementById('statNew30d').textContent = data.new_users_30d;
    document.getElementById('statRoutes').textContent = data.total_routes;
    document.getElementById('statScored').textContent = data.total_scored_routes;
    document.getElementById('statWaitlist').textContent = data.total_waitlist;

    const panel = document.getElementById('rescorePanel');
    const stale = data.stale_routes ?? 0;
    if (stale > 0) {
      document.getElementById('rescoreHeading').textContent = `${stale} route${stale === 1 ? '' : 's'} on stale scoring algorithm`;
      document.getElementById('rescoreSubtext').textContent = `Current version: v${data.current_algo_version}`;
      panel.style.display = 'flex';
    } else {
      panel.style.display = 'none';
    }
    document.getElementById('rescoreResult').textContent = '';
  } catch (e) { console.error('Failed to load stats:', e); }
}

async function rescoreRoutes() {
  const btn = document.getElementById('rescoreBtn');
  btn.textContent = 'Rescoring…';
  btn.disabled = true;
  document.getElementById('rescoreResult').textContent = '';
  try {
    const resp = await fetch('/api/admin/rescore', { method: 'POST' });
    const data = await resp.json();
    if (resp.ok) {
      const msg = `Done — ${data.rescored} rescored, ${data.errors} error${data.errors === 1 ? '' : 's'}.`;
      document.getElementById('rescoreResult').textContent = msg;
      loadDashboard();  // Refresh stale count
    } else {
      document.getElementById('rescoreResult').textContent = `Error: ${data.error}`;
    }
  } catch (e) {
    document.getElementById('rescoreResult').textContent = 'Request failed.';
  } finally {
    btn.textContent = 'Rescore stale routes';
    btn.disabled = false;
  }
}

// ============ USERS ============
async function fetchUsers() {
  const s = usersState;
  const params = new URLSearchParams({ q: s.q, sort: s.sort, order: s.order, page: s.page, per_page: s.perPage });
  try {
    const resp = await fetch(`/api/admin/users?${params}`);
    const data = await resp.json();
    renderUsersTable(data.users);
    renderPagination('usersPagination', data, (p) => { usersState.page = p; fetchUsers(); });
  } catch (e) { console.error('Failed to fetch users:', e); }
}

function renderUsersTable(users) {
  const tbody = document.getElementById('usersTableBody');
  if (!users.length) { tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;padding:32px;color:#9CA3AF;">No users found</td></tr>'; return; }
  tbody.innerHTML = users.map(u => `
    <tr>
      <td>${esc(u.email)}</td>
      <td>${esc(u.name || `${u.first_name || ''} ${u.last_name || ''}`.trim())}</td>
      <td><span class="badge ${u.auth_method === 'magic_link' ? 'badge-magic' : 'badge-strava'}">${u.auth_method || 'unknown'}</span></td>
      <td>${u.is_admin ? '<span class="badge badge-admin">Admin</span>' : ''}</td>
      <td>${formatDate(u.created_at)}</td>
      <td>${formatDate(u.last_login)}</td>
      <td><button class="btn-sm" onclick="openEditUser('${u.doc_id}')">Edit</button></td>
    </tr>
  `).join('');
}

function sortUsers(field) {
  if (usersState.sort === field) usersState.order = usersState.order === 'desc' ? 'asc' : 'desc';
  else { usersState.sort = field; usersState.order = 'asc'; }
  usersState.page = 1;
  updateSortArrows('users', usersState);
  fetchUsers();
}

function debounceUserSearch() {
  clearTimeout(searchTimers.users);
  searchTimers.users = setTimeout(() => {
    usersState.q = document.getElementById('userSearch').value;
    usersState.page = 1;
    fetchUsers();
  }, 300);
}

// ============ ROUTES ============
async function fetchRoutes() {
  const s = routesState;
  const params = new URLSearchParams({ q: s.q, sort: s.sort, order: s.order, page: s.page, per_page: s.perPage });
  try {
    const resp = await fetch(`/api/admin/routes?${params}`);
    const data = await resp.json();
    renderRoutesTable(data.routes);
    renderPagination('routesPagination', data, (p) => { routesState.page = p; fetchRoutes(); });
  } catch (e) { console.error('Failed to fetch routes:', e); }
}

function renderRoutesTable(routes) {
  const tbody = document.getElementById('routesTableBody');
  if (!routes.length) { tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;padding:32px;color:#9CA3AF;">No routes found</td></tr>'; return; }
  tbody.innerHTML = routes.map(r => {
    const distMi = r.totalDist ? (r.totalDist * 0.621371).toFixed(1) : '?';
    const gainFt = r.totalGain ? Math.round(r.totalGain * 3.28084) : '?';
    const scoreClass = getScoreClass(r.composite);
    return `
      <tr>
        <td>${esc(r.name || 'Unnamed')}</td>
        <td><strong class="${scoreClass}">${r.composite || '?'}</strong></td>
        <td>${esc(r.descriptor || '')}</td>
        <td>${distMi} mi</td>
        <td>${gainFt} ft</td>
        <td>${formatDateStr(r.created_at)}</td>
        <td><a href="/api/admin/download/${r.doc_id}?collection=routes" style="color:#2563EB;text-decoration:none;font-size:0.8rem;">Download</a></td>
      </tr>
    `;
  }).join('');
}

function sortRoutes(field) {
  if (routesState.sort === field) routesState.order = routesState.order === 'desc' ? 'asc' : 'desc';
  else { routesState.sort = field; routesState.order = 'asc'; }
  routesState.page = 1;
  updateSortArrows('routes', routesState);
  fetchRoutes();
}

function debounceRouteSearch() {
  clearTimeout(searchTimers.routes);
  searchTimers.routes = setTimeout(() => {
    routesState.q = document.getElementById('routeSearch').value;
    routesState.page = 1;
    fetchRoutes();
  }, 300);
}

// ============ SCORED ROUTES ============
async function fetchScored() {
  const s = scoredState;
  const params = new URLSearchParams({ q: s.q, sort: s.sort, order: s.order, page: s.page, per_page: s.perPage });
  try {
    const resp = await fetch(`/api/admin/scored-routes?${params}`);
    const data = await resp.json();
    renderScoredTable(data.routes);
    renderPagination('scoredPagination', data, (p) => { scoredState.page = p; fetchScored(); });
  } catch (e) { console.error('Failed to fetch scored routes:', e); }
}

function renderScoredTable(routes) {
  const tbody = document.getElementById('scoredTableBody');
  if (!routes.length) { tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;padding:32px;color:#9CA3AF;">No scored routes found</td></tr>'; return; }
  tbody.innerHTML = routes.map(r => {
    const distKm = r.totalDist || r.total_dist_km;
    const distMi = distKm ? (distKm * 0.621371).toFixed(1) : '?';
    const gain = r.totalGain || r.total_gain;
    const gainFt = gain ? Math.round(gain * 3.28084) : '?';
    const scoreClass = getScoreClass(r.composite);
    return `
      <tr>
        <td>${esc(r.name || 'Unnamed')}</td>
        <td><strong class="${scoreClass}">${r.composite || '?'}</strong></td>
        <td>${esc(r.descriptor || '')}</td>
        <td>${distMi} mi</td>
        <td>${gainFt} ft</td>
        <td>${formatDateStr(r.scored_at)}</td>
        <td><a href="/api/admin/download/${r.doc_id}?collection=scored_routes" style="color:#2563EB;text-decoration:none;font-size:0.8rem;">Download</a></td>
      </tr>
    `;
  }).join('');
}

function sortScored(field) {
  if (scoredState.sort === field) scoredState.order = scoredState.order === 'desc' ? 'asc' : 'desc';
  else { scoredState.sort = field; scoredState.order = 'asc'; }
  scoredState.page = 1;
  updateSortArrows('scored', scoredState);
  fetchScored();
}

function debounceScoredSearch() {
  clearTimeout(searchTimers.scored);
  searchTimers.scored = setTimeout(() => {
    scoredState.q = document.getElementById('scoredSearch').value;
    scoredState.page = 1;
    fetchScored();
  }, 300);
}

// ============ EDIT USER MODAL ============
async function openEditUser(docId) {
  try {
    const resp = await fetch(`/api/admin/users/${docId}`);
    const data = await resp.json();
    document.getElementById('editUserId').value = docId;
    document.getElementById('editFirstName').value = data.first_name || '';
    document.getElementById('editLastName').value = data.last_name || '';
    document.getElementById('editEmail').value = data.email || '';
    document.getElementById('editIsAdmin').checked = data.is_admin || false;
    document.getElementById('editUserModal').classList.add('visible');
  } catch (e) { alert('Failed to load user details'); }
}

function closeEditModal() {
  document.getElementById('editUserModal').classList.remove('visible');
}

async function saveUser() {
  const docId = document.getElementById('editUserId').value;
  const body = {
    first_name: document.getElementById('editFirstName').value,
    last_name: document.getElementById('editLastName').value,
    email: document.getElementById('editEmail').value,
    is_admin: document.getElementById('editIsAdmin').checked,
  };
  try {
    const resp = await fetch(`/api/admin/users/${docId}`, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    });
    const data = await resp.json();
    if (resp.ok) { closeEditModal(); fetchUsers(); }
    else alert(data.error || 'Failed to save');
  } catch (e) { alert('Failed to save user'); }
}

async function deleteUser() {
  const docId = document.getElementById('editUserId').value;
  if (!confirm('Delete this user and all their saved route links? This cannot be undone.')) return;
  try {
    const resp = await fetch(`/api/admin/users/${docId}`, { method: 'DELETE' });
    const data = await resp.json();
    if (resp.ok) { closeEditModal(); fetchUsers(); }
    else alert(data.error || 'Failed to delete');
  } catch (e) { alert('Failed to delete user'); }
}

// ============ SHARED HELPERS ============
function renderPagination(containerId, data, onPageChange) {
  const el = document.getElementById(containerId);
  el.innerHTML = `
    <span>Showing ${data[Object.keys(data).find(k => Array.isArray(data[k]))].length} of ${data.total}</span>
    <div class="page-btns">
      <button ${data.page <= 1 ? 'disabled' : ''} onclick="void(0)" id="${containerId}-prev">Prev</button>
      <span>Page ${data.page} of ${data.total_pages}</span>
      <button ${data.page >= data.total_pages ? 'disabled' : ''} onclick="void(0)" id="${containerId}-next">Next</button>
    </div>
  `;
  const prev = document.getElementById(`${containerId}-prev`);
  const next = document.getElementById(`${containerId}-next`);
  if (prev) prev.onclick = () => onPageChange(data.page - 1);
  if (next) next.onclick = () => onPageChange(data.page + 1);
}

function updateSortArrows(prefix, state) {
  document.querySelectorAll(`[id^="sort-${prefix}-"]`).forEach(el => el.textContent = '');
  const arrow = document.getElementById(`sort-${prefix}-${state.sort}`);
  if (arrow) arrow.textContent = state.order === 'asc' ? '\u25B2' : '\u25BC';
}

function formatDate(ts) {
  if (!ts) return '-';
  const d = new Date(ts * 1000);
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
}

function formatDateStr(str) {
  if (!str) return '-';
  const d = new Date(str);
  if (isNaN(d)) return str;
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
}

function getScoreClass(score) {
  if (score <= 15) return 'score-flat';
  if (score <= 35) return 'score-gently';
  if (score <= 55) return 'score-rolling';
  if (score <= 70) return 'score-hilly';
  if (score <= 85) return 'score-very';
  return 'score-mountain';
}

function esc(str) {
  const d = document.createElement('div');
  d.textContent = str || '';
  return d.innerHTML;
}

// ============ INIT ============
checkAdminAuth();
