// ============ HTML ESCAPING ============
function esc(str) {
  const d = document.createElement('div');
  d.textContent = str;
  return d.innerHTML;
}

// ============ AUTH STATE ============
let currentUser = null;
const FREE_SCORE_LIMIT = 3;

function getScoreCount() {
  return parseInt(localStorage.getItem('verthurt_score_count') || '0');
}

function incrementScoreCount() {
  const count = getScoreCount() + 1;
  localStorage.setItem('verthurt_score_count', String(count));
  return count;
}

function isAuthenticated() {
  return currentUser !== null;
}

function shouldGate(action) {
  if (isAuthenticated()) return false;
  if (action === 'score') return getScoreCount() >= FREE_SCORE_LIMIT;
  // compare, save, share always gated for anon
  return true;
}

function showGateModal(pendingAction) {
  // Save pending action + route data for post-auth resumption
  // With magic link, user clicks email link which opens /app.html in same or new tab
  // so we use localStorage (persists across tabs) to stash the pending action
  if (pendingAction) {
    if (!pendingAction.pendingGpxFiles && loadedRoutes.length > 0) {
      pendingAction.routes = loadedRoutes.map(r => {
        const slim = {...r};
        delete slim.profile;
        delete slim.bandColors;
        return slim;
      });
      pendingAction.gpxRaw = rawGpxStore;
    }
    try {
      localStorage.setItem('verthurt_pending_action', JSON.stringify(pendingAction));
    } catch (e) {
      console.error('Failed to stash pending action:', e);
    }
  }
  // Reset gate modal form state
  const gateEmail = document.getElementById('gateEmail');
  const gateStatus = document.getElementById('gateMagicStatus');
  const gateBtn = document.getElementById('gateSubmitBtn');
  if (gateEmail) gateEmail.value = '';
  if (gateStatus) gateStatus.innerHTML = '';
  if (gateBtn) { gateBtn.disabled = false; gateBtn.textContent = 'Send login link'; }

  document.getElementById('gateModal').classList.add('visible');
  posthog.capture('gate_modal_shown', { trigger: pendingAction?.type || 'unknown', score_count: getScoreCount() });
}

function hideGateModal() {
  document.getElementById('gateModal').classList.remove('visible');
}

const loadingMessages = [
  'Analyzing elevation profile',
  'Measuring the climb',
  'Counting the switchbacks',
  'Crunching the vert',
  'Grading the gradients',
];
let loadingMsgInterval;

let loadingStartTime = 0;
const MIN_LOADING_MS = 1500;

function showLoading() {
  loadingStartTime = Date.now();
  const overlay = document.getElementById('loadingOverlay');
  const subtext = document.getElementById('loadingSubtext');
  let msgIdx = 0;
  subtext.textContent = loadingMessages[0];
  overlay.classList.add('visible');
  loadingMsgInterval = setInterval(() => {
    msgIdx = (msgIdx + 1) % loadingMessages.length;
    subtext.textContent = loadingMessages[msgIdx];
  }, 2000);
}

function hideLoading() {
  const elapsed = Date.now() - loadingStartTime;
  const remaining = Math.max(0, MIN_LOADING_MS - elapsed);
  setTimeout(() => {
    document.getElementById('loadingOverlay').classList.remove('visible');
    clearInterval(loadingMsgInterval);
  }, remaining);
}

function updateNavAuth() {
  const nav = document.getElementById('navAuth');
  if (currentUser) {
    const initials = (currentUser.name || currentUser.email || '?').charAt(0).toUpperCase();
    nav.innerHTML = `
      ${currentUser.is_admin ? '<a href="/admin.html" style="color:#2563EB;font-size:0.875rem;margin-right:12px;text-decoration:none;font-weight:500;">Admin</a>' : ''}
      <div class="user-info" onclick="showEditProfileModal()" style="cursor:pointer;" title="Edit profile">
        ${currentUser.avatar ? `<img src="${currentUser.avatar}" class="user-avatar" alt="">` :
          `<div class="user-avatar" style="background:#2563EB;color:white;display:flex;align-items:center;justify-content:center;font-weight:700;font-size:14px;border-radius:50%;width:32px;height:32px;">${initials}</div>`}
        <span class="user-name">${currentUser.name || currentUser.email || 'User'}</span>
      </div>
      <button class="logout-btn" onclick="handleLogout()">Log out</button>
    `;
  } else {
    nav.innerHTML = `<button class="signin-btn" onclick="showSignInModal()">Sign in</button>`;
  }
}

function showSignInModal() {
  // Stash current routes so they survive the magic link redirect
  _stashCurrentRoutes();
  document.getElementById('signInEmail').value = '';
  document.getElementById('signInMagicStatus').innerHTML = '';
  document.getElementById('signInSubmitBtn').disabled = false;
  document.getElementById('signInSubmitBtn').textContent = 'Send login link';
  document.getElementById('signInModal').classList.add('visible');
}

function _stashCurrentRoutes() {
  // Save any scored routes + raw GPX so they survive auth redirect
  if (loadedRoutes.length > 0) {
    const pending = {
      type: 'restore',
      routes: loadedRoutes.map(r => {
        const slim = {...r};
        delete slim.profile;
        delete slim.bandColors;
        return slim;
      }),
      gpxRaw: rawGpxStore,
    };
    try {
      localStorage.setItem('verthurt_pending_action', JSON.stringify(pending));
    } catch (e) {
      console.error('Failed to stash routes:', e);
    }
  }
}

async function handleSignInMagicLink(e) {
  e.preventDefault();
  const email = document.getElementById('signInEmail').value.trim();
  const btn = document.getElementById('signInSubmitBtn');
  const status = document.getElementById('signInMagicStatus');
  await _sendMagicLink(email, btn, status);
}

async function handleGateMagicLink(e) {
  e.preventDefault();
  const email = document.getElementById('gateEmail').value.trim();
  const btn = document.getElementById('gateSubmitBtn');
  const status = document.getElementById('gateMagicStatus');
  await _sendMagicLink(email, btn, status);
}

async function _sendMagicLink(email, btn, statusEl) {
  btn.disabled = true;
  btn.textContent = 'Sending...';
  statusEl.innerHTML = '';

  try {
    const resp = await fetch('/api/auth/magic-link', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email }),
    });
    const data = await resp.json();
    if (resp.ok) {
      statusEl.innerHTML = '<div class="magic-link-sent">Check your email for a login link!</div>';
      btn.textContent = 'Sent!';
      posthog.capture('magic_link_sent', { email });
    } else {
      statusEl.innerHTML = `<div style="color:#EF4444;font-size:0.875rem;padding:8px 0;">${esc(data.error || 'Something went wrong.')}</div>`;
      btn.disabled = false;
      btn.textContent = 'Send login link';
    }
  } catch (err) {
    statusEl.innerHTML = '<div style="color:#EF4444;font-size:0.875rem;padding:8px 0;">Network error. Please try again.</div>';
    btn.disabled = false;
    btn.textContent = 'Send login link';
  }
}

async function checkAuth() {
  try {
    const resp = await fetch('/api/auth/me');
    if (resp.ok) {
      const data = await resp.json();
      if (data.authenticated) {
        currentUser = data.user;
        // Clear score count for authenticated users
        localStorage.removeItem('verthurt_score_count');
        updateNavAuth();
        updateTabs();

        // Identify user in PostHog with email as distinct ID
        if (currentUser.email) {
          posthog.identify(currentUser.email, {
            $email: currentUser.email,
            $name: (currentUser.first_name || '') + ' ' + (currentUser.last_name || ''),
            strava_id: currentUser.strava_id,
            first_name: currentUser.first_name,
            last_name: currentUser.last_name,
          });
        }
        posthog.capture('auth_session_restored', { strava_id: currentUser.strava_id });

        // Show profile completion modal on first login
        if (!currentUser.profile_complete) {
          showProfileModal(currentUser);
        }

        // Collapse explainer for authenticated users
        const explainerDetailsEl = document.getElementById('explainerDetails');
        if (explainerDetailsEl) explainerDetailsEl.open = false;

        // Load sidebar routes for compare feature
        loadSidebarRoutes();

        // Check for pending action
        resumePendingAction();
      }
    }
  } catch (e) {
    // Not authenticated, that's fine
  }
}

function showProfileModal(user) {
  document.getElementById('profileFirstName').value = user.first_name || '';
  document.getElementById('profileLastName').value = user.last_name || '';
  document.getElementById('profileEmail').value = user.email || '';
  document.getElementById('profileModal').classList.add('visible');
}

document.getElementById('profileForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const btn = document.getElementById('profileSubmitBtn');
  btn.disabled = true;
  btn.textContent = 'Saving...';

  try {
    const resp = await fetch('/api/auth/profile', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        first_name: document.getElementById('profileFirstName').value.trim(),
        last_name: document.getElementById('profileLastName').value.trim(),
        email: document.getElementById('profileEmail').value.trim(),
      }),
    });

    const data = await resp.json();
    if (resp.ok) {
      // Fetch full user data (includes first_name, last_name, email)
      const meResp = await fetch('/api/auth/me');
      if (meResp.ok) {
        const meData = await meResp.json();
        if (meData.authenticated) currentUser = meData.user;
      } else {
        currentUser = data.user;
      }
      updateNavAuth();
      document.getElementById('profileModal').classList.remove('visible');

      // Identify user in PostHog now that we have email
      if (currentUser.email) {
        posthog.identify(currentUser.email, {
          $email: currentUser.email,
          $name: (currentUser.first_name || '') + ' ' + (currentUser.last_name || ''),
          strava_id: currentUser.strava_id,
          first_name: currentUser.first_name,
          last_name: currentUser.last_name,
        });
      }
      posthog.capture('profile_completed', { email_provided: true });
    } else {
      btn.textContent = data.error || 'Error';
      btn.disabled = false;
      setTimeout(() => { btn.textContent = "Let's go"; }, 2000);
    }
  } catch (err) {
    btn.textContent = 'Error';
    btn.disabled = false;
    setTimeout(() => { btn.textContent = "Let's go"; }, 2000);
  }
});

function showEditProfileModal() {
  document.getElementById('editFirstName').value = currentUser.first_name || '';
  document.getElementById('editLastName').value = currentUser.last_name || '';
  document.getElementById('editEmail').value = currentUser.email || '';
  document.getElementById('editProfileSubmitBtn').textContent = 'Save Changes';
  document.getElementById('editProfileSubmitBtn').disabled = false;
  document.getElementById('editProfileModal').classList.add('visible');
}

document.getElementById('editProfileForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const btn = document.getElementById('editProfileSubmitBtn');
  btn.disabled = true;
  btn.textContent = 'Saving...';

  try {
    const resp = await fetch('/api/auth/profile', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        first_name: document.getElementById('editFirstName').value.trim(),
        last_name: document.getElementById('editLastName').value.trim(),
        email: document.getElementById('editEmail').value.trim(),
      }),
    });

    const data = await resp.json();
    if (resp.ok) {
      currentUser = data.user;
      // Fetch full user data (first/last/email) for future edits
      const meResp = await fetch('/api/auth/me');
      if (meResp.ok) {
        const meData = await meResp.json();
        if (meData.authenticated) currentUser = meData.user;
      }
      updateNavAuth();
      document.getElementById('editProfileModal').classList.remove('visible');

      // Re-identify in case email changed
      if (currentUser.email) {
        posthog.identify(currentUser.email, {
          $email: currentUser.email,
          $name: (currentUser.first_name || '') + ' ' + (currentUser.last_name || ''),
          strava_id: currentUser.strava_id,
          first_name: currentUser.first_name,
          last_name: currentUser.last_name,
        });
      }
      posthog.capture('profile_updated');
    } else {
      btn.textContent = data.error || 'Error';
      btn.disabled = false;
      setTimeout(() => { btn.textContent = 'Save Changes'; }, 2000);
    }
  } catch (err) {
    btn.textContent = 'Error';
    btn.disabled = false;
    setTimeout(() => { btn.textContent = 'Save Changes'; }, 2000);
  }
});

document.getElementById('editProfileModal').addEventListener('click', (e) => {
  if (e.target === e.currentTarget) document.getElementById('editProfileModal').classList.remove('visible');
});

async function deleteAccount() {
  if (!confirm('Are you sure? This will permanently delete your account and all saved routes. This cannot be undone.')) return;

  try {
    const resp = await fetch('/api/auth/account', { method: 'DELETE' });
    if (resp.ok) {
      posthog.capture('account_deleted');
      posthog.reset();
      window.location.reload();
    } else {
      alert('Failed to delete account. Please try again.');
    }
  } catch (err) {
    alert('Failed to delete account. Please try again.');
  }
}

async function handleLogout() {
  await fetch('/api/auth/logout', { method: 'POST' });
  posthog.capture('user_logged_out');
  posthog.reset();
  window.location.reload();
}

async function resumePendingAction() {
  const pending = localStorage.getItem('verthurt_pending_action');
  if (!pending) return;
  localStorage.removeItem('verthurt_pending_action');
  try {
    const action = JSON.parse(pending);

    // Show loading animation for all resume types that re-score
    const hasGpxToScore = action.pendingGpxFiles || action.gpxRaw;
    if (hasGpxToScore) showLoading();

    // Helper: re-score a raw GPX string and return the result
    async function reScoreGpx(gpxText, fileName) {
      const formData = new FormData();
      formData.append('file', new Blob([gpxText], { type: 'application/gpx+xml' }), fileName);
      const resp = await fetch('/api/score', { method: 'POST', body: formData });
      if (resp.ok) return await resp.json();
      return null;
    }

    // Resume compare: re-score all stashed GPX files and display them
    if (action.type === 'compare' && action.pendingGpxFiles) {
      const gpxEntries = Object.entries(action.pendingGpxFiles);
      const fileNames = action.fileNames || gpxEntries.map((_, i) => `route_${i}.gpx`);
      loadedRoutes = [];
      rawGpxStore = {};

      for (let i = 0; i < gpxEntries.length; i++) {
        const gpxText = gpxEntries[i][1];
        const result = await reScoreGpx(gpxText, fileNames[i] || `route_${i}.gpx`);
        if (result) {
          const idx = loadedRoutes.length;
          loadedRoutes.push(result);
          rawGpxStore[idx] = gpxText;
        }
      }

      hideLoading();
      if (loadedRoutes.length > 0) {
        updateUI();
        posthog.capture('compare_resumed', { routes: loadedRoutes.length, source: 'post_auth_resume' });
      }
    }

    // Resume save: re-score and auto-save
    if (action.type === 'save' && action.routes && action.routes.length > 0) {
      const saveIdx = action.idx || 0;
      const route = action.routes[saveIdx];
      const gpxRaw = action.gpxRaw ? (action.gpxRaw[saveIdx] || '') : '';

      if (gpxRaw) {
        try {
          const fullRoute = await reScoreGpx(gpxRaw, (route.name || 'route') + '.gpx');
          hideLoading();
          if (fullRoute) {
            loadedRoutes = [fullRoute];
            rawGpxStore = { 0: gpxRaw };
            updateUI();

            const saveResp = await fetch('/api/routes', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ score_data: fullRoute, gpx_raw: gpxRaw }),
            });
            if (saveResp.ok) {
              posthog.capture('route_saved', { route_name: fullRoute.name, score: fullRoute.composite, source: 'post_auth_resume' });
              const btn = document.querySelector('.btn-save[data-idx="0"]');
              if (btn) { btn.textContent = 'Saved'; btn.classList.add('saved'); btn.disabled = true; }
            }
          }
        } catch (e) {
          hideLoading();
          console.error('Re-score after auth failed:', e);
        }
      } else {
        hideLoading();
        try {
          const resp = await fetch('/api/routes', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ score_data: route, gpx_raw: '' }),
          });
          if (resp.ok) {
            posthog.capture('route_saved', { route_name: route.name, score: route.composite, source: 'post_auth_resume' });
          }
        } catch (e) {
          console.error('Auto-save after auth failed:', e);
        }
      }
    }
    // Resume restore: re-score all stashed routes and display (from voluntary sign-in)
    if (action.type === 'restore' && action.gpxRaw) {
      const gpxEntries = Object.entries(action.gpxRaw);
      loadedRoutes = [];
      rawGpxStore = {};

      for (let i = 0; i < gpxEntries.length; i++) {
        const gpxText = gpxEntries[i][1];
        const fileName = (action.routes && action.routes[i] ? action.routes[i].name : 'route_' + i) + '.gpx';
        const result = await reScoreGpx(gpxText, fileName);
        if (result) {
          const idx = loadedRoutes.length;
          loadedRoutes.push(result);
          rawGpxStore[idx] = gpxText;
        }
      }

      hideLoading();
      if (loadedRoutes.length > 0) {
        updateUI();
        posthog.capture('routes_restored', { routes: loadedRoutes.length, source: 'post_auth_restore' });
      }
    }
  } catch (e) {
    hideLoading();
    console.error('Resume pending action error:', e);
  }
}

// Init auth check on load
posthog.capture('$pageview');
loadSidebarRoutes();  // Show sidebar hint for anon users; checkAuth will refresh for auth'd

// Default: expanded for unauthorized users
const explainerEl = document.getElementById('explainerDetails');
if (explainerEl) explainerEl.open = true;

checkAuth();

// Gate modal close button
document.getElementById('gateClose').addEventListener('click', hideGateModal);
document.getElementById('gateModal').addEventListener('click', (e) => {
  if (e.target === e.currentTarget) hideGateModal();
});

document.getElementById('gpxHelpModal').addEventListener('click', (e) => {
  if (e.target === e.currentTarget) document.getElementById('gpxHelpModal').classList.remove('visible');
});

// ============ GPX SCORING (Server-side) ============
// All scoring logic runs on the server. The client just uploads the file.



// ============ RENDERING ============
function renderResults(results) {
  const section = document.getElementById('results');
  const compDiv = document.getElementById('comparison');
  const profDiv = document.getElementById('profiles');

  section.classList.add('visible');
  compDiv.innerHTML = '';
  profDiv.innerHTML = '';
  compDiv.className = 'comparison' + (results.length === 1 ? ' single' : '');

  // Compute global elevation range across all routes for consistent Y-axis
  let globalMinEle = Infinity, globalMaxEle = -Infinity;
  results.forEach(r => {
    if (r.minEle < globalMinEle) globalMinEle = r.minEle;
    if (r.maxEle > globalMaxEle) globalMaxEle = r.maxEle;
  });
  // Enforce minimum range so flat routes don't look artificially hilly
  const MIN_GLOBAL_ELE_RANGE = 500; // meters (~1640ft)
  if (globalMaxEle - globalMinEle < MIN_GLOBAL_ELE_RANGE) {
    const mid = (globalMinEle + globalMaxEle) / 2;
    globalMinEle = mid - MIN_GLOBAL_ELE_RANGE / 2;
    globalMaxEle = mid + MIN_GLOBAL_ELE_RANGE / 2;
  }

  results.forEach((r, idx) => {
    // Score card
    const card = document.createElement('div');
    card.className = 'score-card';

    const bandEntries = Object.entries(r.bands);
    const totalBandDist = bandEntries.reduce((s, [,b]) => s + b.dist, 0);

    const dateLine = r.date ? ` on ${r.date}` : '';
    card.innerHTML = `
      <div class="score-card-header">
        <div>
          <div class="route-name">${esc(r.name)}</div>
          <div class="route-meta">${(r.totalDist * 0.621371).toFixed(2)} mi${esc(dateLine)}</div>
          <div class="thumb-profile"><canvas id="thumb-canvas-${idx}"></canvas></div>
        </div>
        <div style="text-align:right">
          <div class="big-score ${esc(r.scoreClass)}">${r.composite}</div>
          <div class="score-label">VertHurt</div>
          <div class="score-descriptor ${esc(r.scoreClass)}">${esc(r.descriptor)}</div>
        </div>
      </div>
      <div class="component-scores">
        <div class="component">
          <div class="tooltip">Elevation gain per mile. Higher = more total climbing packed into the route.</div>
          <div class="component-value">${r.densityScore}</div>
          <div class="component-label">Climb Density</div>
        </div>
        <div class="component">
          <div class="tooltip">How steep the climbs are, weighted by distance. Steeper grades contribute disproportionately more.</div>
          <div class="component-value">${r.intensityScore}</div>
          <div class="component-label">Intensity</div>
        </div>
        <div class="component">
          <div class="tooltip">How sustained the climbs are. One long climb scores higher than many short bumps with the same total gain.</div>
          <div class="component-value">${r.continuityScore}</div>
          <div class="component-label">Continuity</div>
        </div>
      </div>
      <div class="stats-grid">
        <div class="stat-item">
          <div class="stat-value">${(r.totalGain * 3.28084).toFixed(0)} ft</div>
          <div class="stat-label">Elevation Gain</div>
        </div>
        <div class="stat-item">
          <div class="stat-value">${(r.totalLoss * 3.28084).toFixed(0)} ft</div>
          <div class="stat-label">Elevation Loss</div>
        </div>
        <div class="stat-item">
          <div class="stat-value">${(r.gainPerKm * 3.28084 * 1.60934).toFixed(0)} ft/mi</div>
          <div class="stat-label">Gain per Mile</div>
        </div>
        <div class="stat-item">
          <div class="stat-value">${(r.minEle * 3.28084).toFixed(0)}–${(r.maxEle * 3.28084).toFixed(0)} ft</div>
          <div class="stat-label">Elevation Range</div>
        </div>
      </div>
      <div class="gradient-bar-container">
        <div class="gradient-bar-label">Climb Gradient Distribution</div>
        <div class="gradient-bar" id="gradbar-${idx}"></div>
        <div class="gradient-legend" id="gradlegend-${idx}"></div>
      </div>
      <div class="card-actions">
        <button class="btn-save" data-idx="${idx}" onclick="saveRoute(${idx})">Save Route</button>
        <button class="btn-delete-saved" data-idx="${idx}" onclick="unsaveRoute(${idx})" title="Remove from library" style="display:none;">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6m3 0V4a2 2 0 012-2h4a2 2 0 012 2v2"/></svg>
        </button>
      </div>
    `;
    compDiv.appendChild(card);

    // Render gradient bar
    const bar = card.querySelector(`#gradbar-${idx}`);
    const legend = card.querySelector(`#gradlegend-${idx}`);
    bandEntries.forEach(([key, band]) => {
      const pct = totalBandDist > 0 ? (band.dist / totalBandDist * 100) : 0;
      if (pct > 0) {
        const seg = document.createElement('div');
        seg.className = 'segment';
        seg.style.width = pct + '%';
        seg.style.background = r.bandColors[key];
        if (pct > 8) seg.textContent = Math.round(pct) + '%';
        bar.appendChild(seg);
      }
      const li = document.createElement('div');
      li.className = 'legend-item';
      li.innerHTML = `<span class="legend-dot" style="background:${r.bandColors[key]}"></span>${band.label} (${pct.toFixed(0)}%)`;
      legend.appendChild(li);
    });

    // Elevation profile
    const profCard = document.createElement('div');
    profCard.className = 'profile-section';
    profCard.innerHTML = `
      <div class="profile-title">${esc(r.name)} — Elevation Profile</div>
      <div class="profile-canvas-wrapper">
        <canvas id="profile-canvas-${idx}"></canvas>
      </div>
    `;
    profDiv.appendChild(profCard);

    // If this route is already saved, mark the button
    if (r._savedRouteId) {
      const saveBtn = card.querySelector('.btn-save');
      if (saveBtn) { saveBtn.textContent = 'Saved'; saveBtn.classList.add('saved'); saveBtn.disabled = true; }
      const delBtn = card.querySelector('.btn-delete-saved');
      if (delBtn) delBtn.style.display = 'inline-flex';
    }

    requestAnimationFrame(() => {
      drawProfile(r, idx, globalMinEle, globalMaxEle);
      drawThumbProfile(r, idx, globalMinEle, globalMaxEle);
    });
  });
}

function drawProfile(result, idx, globalMinE, globalMaxE) {
  const canvas = document.getElementById(`profile-canvas-${idx}`);
  if (!canvas) return;
  const wrapper = canvas.parentElement;
  const dpr = window.devicePixelRatio || 1;
  const w = wrapper.clientWidth;
  const h = wrapper.clientHeight;
  canvas.width = w * dpr;
  canvas.height = h * dpr;
  canvas.style.width = w + 'px';
  canvas.style.height = h + 'px';
  const ctx = canvas.getContext('2d');
  ctx.scale(dpr, dpr);

  const prof = result.profile;
  const pad = { top: 16, right: 16, bottom: 32, left: 58 };
  const plotW = w - pad.left - pad.right;
  const plotH = h - pad.top - pad.bottom;

  const maxDist = prof[prof.length - 1].dist;
  let minE = (globalMinE != null ? globalMinE : Math.min(...prof.map(p => p.ele))) - 5;
  let maxE = (globalMaxE != null ? globalMaxE : Math.max(...prof.map(p => p.ele))) + 5;
  // Enforce minimum Y-axis range so flat routes don't look artificially hilly
  const MIN_ELE_RANGE = 500; // meters (~1640ft)
  if (maxE - minE < MIN_ELE_RANGE) {
    const mid = (minE + maxE) / 2;
    minE = mid - MIN_ELE_RANGE / 2;
    maxE = mid + MIN_ELE_RANGE / 2;
  }
  const eleRange = maxE - minE || 1;

  const toX = d => pad.left + (d / maxDist) * plotW;
  const toY = e => pad.top + (1 - (e - minE) / eleRange) * plotH;

  // Grid lines
  ctx.strokeStyle = '#E5E7EB';
  ctx.lineWidth = 0.5;
  const nGridH = 4;
  for (let i = 0; i <= nGridH; i++) {
    const y = pad.top + (i / nGridH) * plotH;
    ctx.beginPath(); ctx.moveTo(pad.left, y); ctx.lineTo(w - pad.right, y); ctx.stroke();
    const eleVal = maxE - (i / nGridH) * eleRange;
    ctx.fillStyle = '#9CA3AF';
    ctx.font = '11px -apple-system, sans-serif';
    ctx.textAlign = 'right';
    ctx.fillText((eleVal * 3.28084).toFixed(0) + 'ft', pad.left - 6, y + 4);
  }

  // Distance labels
  ctx.textAlign = 'center';
  const nGridD = 5;
  for (let i = 0; i <= nGridD; i++) {
    const d = (i / nGridD) * maxDist;
    const x = toX(d);
    ctx.beginPath(); ctx.moveTo(x, pad.top); ctx.lineTo(x, h - pad.bottom); ctx.stroke();
    ctx.fillStyle = '#9CA3AF';
    ctx.fillText((d / 1000 * 0.621371).toFixed(1) + 'mi', x, h - pad.bottom + 16);
  }

  // Gradient-colored fill
  // We'll draw segment by segment with color based on gradient
  for (let i = 1; i < prof.length; i++) {
    const x0 = toX(prof[i-1].dist);
    const x1 = toX(prof[i].dist);
    const y0 = toY(prof[i-1].ele);
    const y1 = toY(prof[i].ele);
    const yBottom = toY(minE);

    const dDist = prof[i].dist - prof[i-1].dist;
    const grad = dDist > 0 ? ((prof[i].ele - prof[i-1].ele) / dDist) * 100 : 0;

    let color;
    if (grad < 0.5) color = 'rgba(229,231,235,0.5)';      // flat/downhill
    else if (grad < 4) color = 'rgba(5,150,105,0.35)';     // easy
    else if (grad < 8) color = 'rgba(8,145,178,0.4)';      // moderate
    else if (grad < 12) color = 'rgba(217,119,6,0.45)';    // hard
    else color = 'rgba(220,38,38,0.5)';                     // severe

    ctx.fillStyle = color;
    ctx.beginPath();
    ctx.moveTo(x0, y0);
    ctx.lineTo(x1, y1);
    ctx.lineTo(x1, yBottom);
    ctx.lineTo(x0, yBottom);
    ctx.closePath();
    ctx.fill();
  }

  // Profile line
  ctx.strokeStyle = '#374151';
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  prof.forEach((p, i) => {
    const x = toX(p.dist);
    const y = toY(p.ele);
    i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
  });
  ctx.stroke();
}

// ============ WEIGHT DEFAULTS ============
// ============ THUMBNAIL ELEVATION PROFILE ============
function drawThumbProfile(result, idx, globalMinE, globalMaxE) {
  const canvas = document.getElementById(`thumb-canvas-${idx}`);
  if (!canvas) return;
  const wrapper = canvas.parentElement;
  const dpr = window.devicePixelRatio || 1;
  const w = wrapper.clientWidth;
  const h = wrapper.clientHeight;
  canvas.width = w * dpr;
  canvas.height = h * dpr;
  canvas.style.width = w + 'px';
  canvas.style.height = h + 'px';
  const ctx = canvas.getContext('2d');
  ctx.scale(dpr, dpr);

  const prof = result.profile;
  if (prof.length < 2) return;
  const maxDist = prof[prof.length - 1].dist;
  const pad = 2;
  let minE = (globalMinE != null ? globalMinE : Math.min(...prof.map(p => p.ele))) - 3;
  let maxE = (globalMaxE != null ? globalMaxE : Math.max(...prof.map(p => p.ele))) + 3;
  // Enforce minimum Y-axis range (same as main profile)
  const MIN_ELE_RANGE_THUMB = 500;
  if (maxE - minE < MIN_ELE_RANGE_THUMB) {
    const mid = (minE + maxE) / 2;
    minE = mid - MIN_ELE_RANGE_THUMB / 2;
    maxE = mid + MIN_ELE_RANGE_THUMB / 2;
  }
  const eleRange = maxE - minE || 1;

  const toX = d => pad + (d / maxDist) * (w - pad * 2);
  const toY = e => pad + (1 - (e - minE) / eleRange) * (h - pad * 2);

  // Filled area
  ctx.beginPath();
  ctx.moveTo(toX(prof[0].dist), h);
  prof.forEach(p => ctx.lineTo(toX(p.dist), toY(p.ele)));
  ctx.lineTo(toX(prof[prof.length - 1].dist), h);
  ctx.closePath();
  const grad = ctx.createLinearGradient(0, 0, 0, h);
  grad.addColorStop(0, 'rgba(37,99,235,0.25)');
  grad.addColorStop(1, 'rgba(37,99,235,0.05)');
  ctx.fillStyle = grad;
  ctx.fill();

  // Line
  ctx.strokeStyle = '#2563EB';
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  prof.forEach((p, i) => {
    const x = toX(p.dist);
    const y = toY(p.ele);
    i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
  });
  ctx.stroke();
}

// ============ FILE HANDLING ============
let loadedRoutes = [];

function handleFiles(files) {
  const gpxFiles = Array.from(files).filter(f => f.name.toLowerCase().endsWith('.gpx'));
  if (gpxFiles.length === 0) return;

  // Check gate: comparing (multiple files loaded) requires auth
  if (gpxFiles.length > 1 && shouldGate('compare')) {
    // Read all GPX files before gating so we can stash them
    Promise.all(gpxFiles.map(f => f.text().then(text => ({ name: f.name, text })))).then(gpxData => {
      const pendingGpx = {};
      gpxData.forEach((g, i) => { pendingGpx[i] = g.text; });
      showGateModal({ type: 'compare', pendingGpxFiles: pendingGpx, fileNames: gpxData.map(g => g.name) });
    });
    posthog.capture('gate_triggered', { reason: 'compare', score_count: getScoreCount() });
    return;
  }
  if (loadedRoutes.length > 0 && gpxFiles.length > 0 && shouldGate('compare')) {
    // Read the new files and combine with already-loaded raw GPX
    Promise.all(gpxFiles.map(f => f.text().then(text => ({ name: f.name, text })))).then(gpxData => {
      const pendingGpx = { ...rawGpxStore };
      const fileNames = loadedRoutes.map(r => r.name);
      gpxData.forEach((g, i) => {
        const idx = loadedRoutes.length + i;
        pendingGpx[idx] = g.text;
        fileNames.push(g.name);
      });
      showGateModal({ type: 'compare', pendingGpxFiles: pendingGpx, fileNames });
    });
    posthog.capture('gate_triggered', { reason: 'compare_additional', score_count: getScoreCount() });
    return;
  }

  gpxFiles.forEach(async file => {
    // Check gate: score limit
    if (shouldGate('score')) {
      showGateModal({ type: 'score' });
      posthog.capture('gate_triggered', { reason: 'score_limit', score_count: getScoreCount() });
      return;
    }

    // Read raw GPX for saving later
    const rawGpx = await file.text();

    // Show loading animation
    showLoading();

    // Upload to server for scoring
    const formData = new FormData();
    formData.append('file', file);

    try {
      const resp = await fetch('/api/score', { method: 'POST', body: formData });
      hideLoading();
      if (!resp.ok) {
        const err = await resp.json();
        console.error('Scoring error:', err.error);
        return;
      }
      const result = await resp.json();
      const routeIdx = loadedRoutes.length;
      loadedRoutes.push(result);
      rawGpxStore[routeIdx] = rawGpx;

      // Increment score count for anonymous users
      if (!isAuthenticated()) {
        incrementScoreCount();
      }

      // Track route analysis
      posthog.capture('route_analyzed', {
        route_name: result.name,
        distance_mi: (result.totalDist * 0.621371).toFixed(2),
        elevation_gain_ft: (result.totalGain * 3.28084).toFixed(0),
        hilliness_score: result.composite,
        descriptor: result.descriptor,
        gain_per_mile_ft: (result.gainPerKm * 3.28084 / 1.60934).toFixed(0),
        routes_loaded: loadedRoutes.length,
        authenticated: isAuthenticated()
      });

      updateUI();
    } catch (err) {
      hideLoading();
      console.error('Failed to score route:', err);
    }
  });
}

function updateUI() {
  // File chips
  const chipsDiv = document.getElementById('loadedFiles');
  chipsDiv.innerHTML = '';
  loadedRoutes.forEach((r, i) => {
    const chip = document.createElement('div');
    chip.className = 'file-chip';
    chip.innerHTML = `<span class="dot"></span>${esc(r.name)}<span class="remove" data-idx="${i}">&times;</span>`;
    chipsDiv.appendChild(chip);
  });

  // Remove handler
  chipsDiv.querySelectorAll('.remove').forEach(btn => {
    btn.addEventListener('click', e => {
      const removedIdx = parseInt(e.target.dataset.idx);
      loadedRoutes.splice(removedIdx, 1);
      // Re-index rawGpxStore after removal
      const newStore = {};
      loadedRoutes.forEach((r, i) => {
        const oldIdx = i >= removedIdx ? i + 1 : i;
        if (rawGpxStore[oldIdx] !== undefined) newStore[i] = rawGpxStore[oldIdx];
      });
      rawGpxStore = newStore;
      updateUI();
    });
  });

  // Keep sidebar active states in sync
  renderSidebarItems();

  // Animate dropzone compact/expand
  const dz = document.getElementById('dropzone');
  if (loadedRoutes.length > 0) {
    dz.classList.add('compact');
    renderResults(loadedRoutes);
  } else {
    dz.classList.remove('compact');
    document.getElementById('results').classList.remove('visible');
  }
}

// ============ SAVE ROUTE ============
// Store raw GPX content alongside scored results for saving
let rawGpxStore = {};  // keyed by route index

async function saveRoute(idx) {
  if (shouldGate('save')) {
    showGateModal({ type: 'save', idx: idx });
    posthog.capture('gate_triggered', { reason: 'save', score_count: getScoreCount() });
    return;
  }

  const route = loadedRoutes[idx];
  const btn = document.querySelector(`.btn-save[data-idx="${idx}"]`);
  btn.textContent = 'Saving...';
  btn.disabled = true;

  try {
    const resp = await fetch('/api/routes', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        score_data: route,
        gpx_raw: rawGpxStore[idx] || '',
      }),
    });

    const data = await resp.json();
    if (resp.ok || data.status === 'already_saved') {
      btn.textContent = data.status === 'already_saved' ? 'Already Saved' : 'Saved';
      btn.classList.add('saved');
      btn.disabled = true;
      // Store route_id for potential delete, show trash icon
      const routeId = data.route_id;
      if (routeId) route._savedRouteId = routeId;
      const delBtn = document.querySelector(`.btn-delete-saved[data-idx="${idx}"]`);
      if (delBtn) delBtn.style.display = 'inline-flex';
      if (data.status !== 'already_saved') {
        posthog.capture('route_saved', { route_name: route.name, score: route.composite });
      }
      loadSidebarRoutes();  // Refresh sidebar with new route
    } else {
      btn.textContent = 'Error';
    }
  } catch (err) {
    btn.textContent = 'Save Failed';
    console.error('Save error:', err);
  }
}

async function unsaveRoute(idx) {
  const route = loadedRoutes[idx];
  const routeId = route?._savedRouteId;
  if (!routeId) return;

  try {
    const resp = await fetch(`/api/routes/${routeId}`, { method: 'DELETE' });
    if (resp.ok) {
      // Reset save button
      const btn = document.querySelector(`.btn-save[data-idx="${idx}"]`);
      if (btn) { btn.textContent = 'Save Route'; btn.classList.remove('saved'); btn.disabled = false; }
      const delBtn = document.querySelector(`.btn-delete-saved[data-idx="${idx}"]`);
      if (delBtn) delBtn.style.display = 'none';
      delete route._savedRouteId;
      posthog.capture('route_unsaved', { route_name: route.name });
      loadSidebarRoutes();  // Refresh sidebar
    }
  } catch (err) {
    console.error('Unsave error:', err);
  }
}

// ============ SAVED ROUTES SIDEBAR ============
const scoreBadgeColors = {
  'score-flat': '#059669',
  'score-rolling': '#0891B2',
  'score-hilly': '#D97706',
  'score-mountainous': '#DC2626',
};

let sidebarRoutesCache = null;

async function loadSidebarRoutes() {
  const sidebar = document.getElementById('savedRoutesSidebar');
  const list = document.getElementById('sidebarRouteList');

  if (!isAuthenticated()) {
    // Show sidebar with sign-in prompt as feature hint
    sidebar.classList.add('visible');
    document.querySelector('.container').classList.add('has-sidebar');
    list.innerHTML = `<div class="sidebar-empty">
      <div style="font-size:1.5rem;margin-bottom:8px;">&#x1F4BE;</div>
      <div>Sign in to save routes and compare them side-by-side.</div>
    </div>`;
    return;
  }

  try {
    const resp = await fetch('/api/routes');
    if (!resp.ok) {
      sidebar.classList.remove('visible');
      document.querySelector('.container').classList.remove('has-sidebar');
      return;
    }
    const data = await resp.json();
    sidebarRoutesCache = data.routes;

    if (data.routes.length === 0) {
      sidebar.classList.add('visible');
      document.querySelector('.container').classList.add('has-sidebar');
      list.innerHTML = `<div class="sidebar-empty">
        <div>No saved routes yet.</div>
        <div style="margin-top:4px;">Score a route and hit Save to start your library.</div>
      </div>`;
      return;
    }

    sidebar.classList.add('visible');
    document.querySelector('.container').classList.add('has-sidebar');
    renderSidebarItems();
  } catch (err) {
    console.error('Sidebar load error:', err);
  }
}

function renderSidebarItems() {
  const list = document.getElementById('sidebarRouteList');
  if (!sidebarRoutesCache) return;

  list.innerHTML = '';
  sidebarRoutesCache.forEach(route => {
    const isActive = loadedRoutes.some(r => r._savedRouteId === route.id);
    const distMi = (route.totalDist * 0.621371).toFixed(1);
    const badgeColor = scoreBadgeColors[route.scoreClass] || '#6B7280';

    const item = document.createElement('div');
    item.className = 'sidebar-route-item' + (isActive ? ' active' : '');
    item.dataset.routeId = route.id;
    item.innerHTML = `
      <div class="sidebar-score-badge" style="background:${badgeColor}">${route.composite}</div>
      <div class="sidebar-route-info">
        <div class="sidebar-route-name">${esc(route.name)}</div>
        <div class="sidebar-route-meta">${distMi} mi · ${esc(route.descriptor)}</div>
      </div>
    `;
    item.addEventListener('click', () => toggleSidebarRoute(route.id));
    list.appendChild(item);
  });
}

async function toggleSidebarRoute(routeId) {
  const existingIdx = loadedRoutes.findIndex(r => r._savedRouteId === routeId);

  if (existingIdx !== -1) {
    // Remove from comparison
    loadedRoutes.splice(existingIdx, 1);
    // Re-index rawGpxStore after removal
    const newStore = {};
    loadedRoutes.forEach((r, i) => {
      const oldIdx = i >= existingIdx ? i + 1 : i;
      if (rawGpxStore[oldIdx] !== undefined) newStore[i] = rawGpxStore[oldIdx];
    });
    rawGpxStore = newStore;
    updateUI();
    renderSidebarItems();
    posthog.capture('sidebar_route_toggled', { route_id: routeId, action: 'removed' });
    return;
  }

  // Add to comparison — fetch full route data
  const item = document.querySelector(`.sidebar-route-item[data-route-id="${routeId}"]`);
  if (item) item.classList.add('loading');

  try {
    const resp = await fetch(`/api/routes/${routeId}`);
    if (!resp.ok) {
      if (item) item.classList.remove('loading');
      return;
    }
    const route = await resp.json();
    route._savedRouteId = routeId;
    route._fromSidebar = true;
    loadedRoutes.push(route);
    updateUI();
    renderSidebarItems();
    posthog.capture('sidebar_route_toggled', { route_id: routeId, action: 'added' });
  } catch (err) {
    console.error('Toggle sidebar route error:', err);
    if (item) item.classList.remove('loading');
  }
}

// ============ MY ROUTES ============
async function loadMyRoutes() {
  const grid = document.getElementById('routesGrid');
  grid.innerHTML = '<div class="empty-routes"><p>Loading...</p></div>';

  try {
    const resp = await fetch('/api/routes');
    if (resp.status === 401) {
      grid.innerHTML = '<div class="empty-routes"><p>Sign in to save and view your routes.</p></div>';
      return;
    }
    if (!resp.ok) {
      grid.innerHTML = '<div class="empty-routes"><p>Failed to load routes. Please try again.</p></div>';
      return;
    }

    const data = await resp.json();
    if (data.routes.length === 0) {
      grid.innerHTML = '<div class="empty-routes"><p>No saved routes yet.</p><p>Score a route and hit Save to start your library.</p></div>';
      return;
    }

    grid.innerHTML = '';
    data.routes.forEach(route => {
      const card = document.createElement('div');
      card.className = 'route-card';
      const distMi = (route.totalDist * 0.621371).toFixed(1);
      const gainFt = (route.totalGain * 3.28084).toFixed(0);
      card.innerHTML = `
        <button class="delete-btn" onclick="event.stopPropagation(); deleteRoute('${route.id}', '${route.link_id}')" title="Remove from library">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6m3 0V4a2 2 0 012-2h4a2 2 0 012 2v2"/></svg>
        </button>
        <div class="route-card-header">
          <div>
            <div class="route-name">${esc(route.name)}</div>
            <div class="route-meta">${distMi} mi${route.date ? ' · ' + esc(route.date) : ''}</div>
          </div>
          <div style="text-align:right">
            <div class="mini-score ${esc(route.scoreClass)}">${route.composite}</div>
            <div class="mini-descriptor ${esc(route.scoreClass)}">${esc(route.descriptor)}</div>
          </div>
        </div>
        <div class="route-card-stats">
          <div>Gain: <span>${gainFt} ft</span></div>
        </div>
      `;
      card.addEventListener('click', () => viewSavedRoute(route.id));
      grid.appendChild(card);
    });
  } catch (err) {
    grid.innerHTML = '<div class="empty-routes"><p>Failed to load routes</p></div>';
    console.error('Load routes error:', err);
  }
}

async function viewSavedRoute(routeId) {
  // If already loaded, just switch to score tab
  if (loadedRoutes.some(r => r._savedRouteId === routeId)) {
    switchTab('score');
    return;
  }

  try {
    const resp = await fetch(`/api/routes/${routeId}`);
    if (!resp.ok) return;
    const route = await resp.json();

    // Add to comparison (not replace) — mark as saved
    route._savedRouteId = routeId;
    route._fromSidebar = true;
    loadedRoutes.push(route);
    switchTab('score');
    updateUI();
    renderSidebarItems();
  } catch (err) {
    console.error('View route error:', err);
  }
}

async function deleteRoute(routeId, linkId) {
  if (!confirm('Remove this route from your library?')) return;

  try {
    await fetch(`/api/routes/${routeId}`, { method: 'DELETE' });
    posthog.capture('route_deleted', { route_id: routeId });
    loadMyRoutes();  // Refresh
    loadSidebarRoutes();  // Refresh sidebar
  } catch (err) {
    console.error('Delete error:', err);
  }
}

// ============ TABS ============
function switchTab(tab) {
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.querySelector(`.tab[data-tab="${tab}"]`).classList.add('active');
  posthog.capture('tab_switched', { tab: tab });

  if (tab === 'score') {
    document.getElementById('scoreTab').style.display = '';
    document.getElementById('myRoutesTab').classList.remove('visible');
  } else {
    document.getElementById('scoreTab').style.display = 'none';
    document.getElementById('myRoutesTab').classList.add('visible');
    loadMyRoutes();
  }
}

document.querySelectorAll('.tab').forEach(tab => {
  tab.addEventListener('click', () => switchTab(tab.dataset.tab));
});

// Show tabs when authenticated
function updateTabs() {
  document.getElementById('tabBar').style.display = isAuthenticated() ? 'flex' : 'none';
}

// ============ EVENT LISTENERS ============
const dropzone = document.getElementById('dropzone');
const fileInput = document.getElementById('fileInput');

dropzone.addEventListener('click', () => fileInput.click());
dropzone.addEventListener('dragover', e => { e.preventDefault(); dropzone.classList.add('dragover'); });
dropzone.addEventListener('dragleave', () => dropzone.classList.remove('dragover'));
dropzone.addEventListener('drop', e => {
  e.preventDefault();
  dropzone.classList.remove('dragover');
  handleFiles(e.dataTransfer.files);
});
fileInput.addEventListener('change', e => handleFiles(e.target.files));

// Resize handling
window.addEventListener('resize', () => {
  if (loadedRoutes.length > 0) {
    let gMin = Infinity, gMax = -Infinity;
    loadedRoutes.forEach(r => { if (r.minEle < gMin) gMin = r.minEle; if (r.maxEle > gMax) gMax = r.maxEle; });
    // Enforce minimum range (same as renderResults)
    if (gMax - gMin < 500) { const mid = (gMin + gMax) / 2; gMin = mid - 250; gMax = mid + 250; }
    loadedRoutes.forEach((r, i) => {
      drawProfile(r, i, gMin, gMax);
      drawThumbProfile(r, i, gMin, gMax);
    });
  }
});
