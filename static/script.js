(() => {
  'use strict';

  const $ = (selector) => document.querySelector(selector);

  let authMode = 'login';
  let currentUser = null;
  let events = [];
  let registrations = [];
  let categories = [];
  let activeCategory = 'All';
  let searchTerm = '';
  let activeTab = 'dashboard';

  // ----- input rules -----
  // Student ID: numbers only (hyphens allowed, e.g. 2023-00123).
  // Name: letters only (spaces, periods, apostrophes and hyphens allowed).
  const STUDENT_ID_RE = /^\d+(?:-\d+)*$/;
  const NAME_RE = /^\p{L}+(?:[ .'’\-]+\p{L}+)*\.?$/u;

  const STUDENT_ID_MESSAGE =
    'Student ID can only contain numbers (and hyphens).';
  const NAME_MESSAGE =
    'Name can only contain letters, spaces, periods, apostrophes and hyphens.';

  // Remove wrong characters while the user types or pastes.
  document.addEventListener('input', (event) => {
    const field = event.target;

    if (!field || !field.id) return;

    if (field.id === 'auth-student-id') {
      field.value = field.value.replace(/[^0-9-]/g, '');
    }

    if (field.id === 'auth-name' || field.id === 'registration-name') {
      field.value = field.value.replace(/[0-9_]|[^\p{L} .'’\-]/gu, '');
    }
  });

  async function api(path, options = {}) {
    const response = await fetch(path, {
      credentials: 'same-origin',
      ...options,
      headers: {
        'Content-Type': 'application/json',
        ...(options.headers || {})
      }
    });

    const data = await response.json().catch(() => ({}));

    if (!response.ok) {
      if (response.status === 401 && path !== '/api/auth/login') {
        showAuth();
      }

      throw new Error(data.error || 'Something went wrong. Please try again.');
    }

    return data;
  }

  function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, (char) => ({
      '&': '&amp;',
      '<': '&lt;',
      '>': '&gt;',
      '"': '&quot;',
      "'": '&#39;'
    })[char]);
  }

  function fmtDate(value) {
    if (!value) return 'Date to be announced';

    const date = new Date(`${value}T00:00:00`);

    if (Number.isNaN(date.getTime())) return value;

    return date.toLocaleDateString('en-PH', {
      month: 'short',
      day: 'numeric',
      year: 'numeric'
    });
  }

  // An event is finished when the server says so (marked done by the admin
  // or its date/time has passed). The date check is only a fallback.
  function isEventFinished(event) {
    if (!event) return false;

    if (typeof event.isFinished === 'boolean') {
      return event.isFinished;
    }

    const when = new Date(`${event.date}T${event.time || '23:59'}`);

    return !Number.isNaN(when.getTime()) && when < new Date();
  }

  // Course, section, year level and attendance answer of a registration.
  function registrationDetails(registration) {
    const classInfo = [
      registration.course,
      registration.section,
      registration.year
    ].filter(Boolean).join(' · ');

    const attending =
      `Attending: ${registration.attending === 'no' ? 'No' : 'Yes'}`;

    return classInfo ? `${classInfo} · ${attending}` : attending;
  }

  // ----- toast notifications -----
  // type: "success" (green) or "error" (red). Toasts stack at the top right.
  function showToast(message, type = 'success', duration = 4000) {
    const root = $('#toast-root');
    if (!root) return;

    const toast = document.createElement('div');
    toast.className = 'toast' + (type === 'error' ? ' error' : '');
    toast.setAttribute('role', type === 'error' ? 'alert' : 'status');

    const icon = document.createElement('span');
    icon.className = 'toast-icon';
    icon.textContent = type === 'error' ? '✕' : '✓';

    const text = document.createElement('span');
    text.className = 'toast-text';
    text.textContent = message;

    const close = document.createElement('button');
    close.type = 'button';
    close.className = 'toast-close';
    close.setAttribute('aria-label', 'Dismiss');
    close.textContent = '✕';

    toast.append(icon, text, close);
    root.appendChild(toast);

    let timer;
    const dismiss = () => {
      clearTimeout(timer);
      toast.classList.add('leaving');
      toast.addEventListener('animationend', () => toast.remove(), { once: true });
    };

    close.addEventListener('click', dismiss);
    timer = setTimeout(dismiss, duration);
  }

  function showAuth() {
    $('#auth-view').hidden = false;
    $('#participant-app').hidden = true;
    $('#modal-root').innerHTML = '';
  }

  function showApp() {
    $('#auth-view').hidden = true;
    $('#participant-app').hidden = false;
    $('#header-user').textContent = currentUser?.name || '';
  }

  // ----- sign in / sign up screen -----
  function setAuthMode(mode) {
    authMode = mode;

    const isSignup = mode === 'signup';

    // Tabs (hidden by CSS, kept in sync anyway)
    $('#login-tab').classList.toggle('active', !isSignup);
    $('#signup-tab').classList.toggle('active', isSignup);

    // Extra sign-up fields
    $('#student-id-field').hidden = !isSignup;
    $('#name-field').hidden = !isSignup;
    $('#auth-student-id').required = isSignup;
    $('#auth-name').required = isSignup;

    // Remember me / forgot password only make sense when logging in
    $('#login-options').hidden = isSignup;

    // Password autocomplete hint
    $('#auth-password').autocomplete = isSignup
      ? 'new-password'
      : 'current-password';

    // Texts
    $('#auth-title').innerHTML = isSignup
      ? 'Sign Up<span>.</span>'
      : 'Sign In<span>.</span>';

    $('#auth-subtitle').textContent = isSignup
      ? 'Create an account to register for events.'
      : 'Log in to discover and register for events.';

    $('#art-title').textContent = isSignup ? 'Hello there!' : 'Welcome back!';

    $('#art-text').textContent = isSignup
      ? 'Create a free account to discover and join school events.'
      : 'You can sign in to access your existing account and join school events.';

    $('#auth-submit').textContent = isSignup ? 'Create Account' : 'Login';

    $('#auth-switch-text').textContent = isSignup
      ? 'Already registered?'
      : 'New here?';

    $('#auth-switch-btn').textContent = isSignup
      ? 'Log in'
      : 'Create an Account';

    // Clear previous values and errors when switching modes
    $('#auth-form').reset();
    $('#auth-error').textContent = '';
    $('#auth-error').hidden = true;
  }

  $('#login-tab').addEventListener('click', () => setAuthMode('login'));
  $('#signup-tab').addEventListener('click', () => setAuthMode('signup'));

  $('#auth-switch-btn').addEventListener('click', () => {
    setAuthMode(authMode === 'signup' ? 'login' : 'signup');
  });

  $('#forgot-btn').addEventListener('click', () => {
    showToast('Please contact your administrator to reset your password.');
  });

  // Show the number keypad on phones for the Student ID box.
  $('#auth-student-id').setAttribute('inputmode', 'numeric');

  $('#auth-form').addEventListener('submit', async (event) => {
    event.preventDefault();

    const error = $('#auth-error');
    const button = $('#auth-submit');
    const submittedMode = authMode;

    error.hidden = true;
    button.disabled = true;
    button.textContent = 'Please wait...';

    const studentId = $('#auth-student-id').value.trim();
    const name = $('#auth-name').value.trim();
    const email = $('#auth-email').value.trim();
    const password = $('#auth-password').value;

    try {
      // ===== SIGN UP: create the account, then go to the sign-in form =====
      if (submittedMode === 'signup') {
        if (!studentId || !name || !email || !password) {
          throw new Error('Please complete all required fields.');
        }

        // Student ID must be numbers only; name must have no numbers.
        if (!STUDENT_ID_RE.test(studentId)) {
          throw new Error(STUDENT_ID_MESSAGE);
        }

        if (!NAME_RE.test(name)) {
          throw new Error(NAME_MESSAGE);
        }

        await api('/api/auth/register', {
          method: 'POST',
          body: JSON.stringify({
            student_id: studentId,
            name,
            email,
            password
          })
        });

        // If the server signs the new user in automatically, end that
        // session so the person must log in on their own.
        try {
          await api('/api/auth/logout', { method: 'POST', body: '{}' });
        } catch (ignored) {
          // No session to end; nothing to do.
        }

        // Switch to the sign-in form and keep the email filled in.
        setAuthMode('login');
        $('#auth-email').value = email;
        $('#auth-password').focus();

        showToast('Account created successfully! Please sign in.');
        return;
      }

      // ===== LOGIN =====
      await api('/api/auth/login', {
        method: 'POST',
        body: JSON.stringify({ email, password })
      });

      const userData = await api('/api/auth/me');

      // Admins use the same form and are sent to the admin page.
      if (userData.role === 'admin') {
        window.location.href = '/admin';
        return;
      }

      if (userData.role !== 'participant' || !userData.user) {
        throw new Error('This account cannot sign in here.');
      }

      currentUser = userData.user;

      await loadData();

      showApp();
      renderAll();

      showToast(`Login successful! Welcome, ${currentUser.name || 'participant'}.`);

      setAuthMode('login');
    } catch (err) {
      error.textContent = err.message;
      error.hidden = false;
      showToast(err.message, 'error');
    } finally {
      button.disabled = false;
      button.textContent = authMode === 'signup' ? 'Create Account' : 'Login';
    }
  });

  $('#logout-btn').addEventListener('click', async () => {
    try {
      await api('/api/auth/logout', {
        method: 'POST',
        body: '{}'
      });
    } catch (error) {
      showToast(error.message, 'error');
      return;
    }

    currentUser = null;
    events = [];
    registrations = [];
    categories = [];
    activeTab = 'dashboard';

    showAuth();
    setAuthMode('login');

    showToast('You have been logged out successfully.');
  });

  async function loadData() {
    const [eventData, categoryData, registrationData] = await Promise.all([
      api('/api/events'),
      api('/api/categories'),
      api('/api/my-registrations')
    ]);

    events = Array.isArray(eventData) ? eventData : [];
    categories = Array.isArray(categoryData) ? categoryData : [];
    registrations = Array.isArray(registrationData) ? registrationData : [];
  }

  // Browse Events shows upcoming events only.
  function getFilteredEvents() {
    return events.filter((event) => {
      if (isEventFinished(event)) return false;

      const categoryMatches =
        activeCategory === 'All' || event.category === activeCategory;

      const searchable = [
        event.title,
        event.location,
        event.category,
        event.description
      ].join(' ').toLowerCase();

      return categoryMatches &&
        (!searchTerm || searchable.includes(searchTerm.toLowerCase()));
    }).sort((a, b) => new Date(a.date) - new Date(b.date));
  }

  function eventCard(event) {
    const registered = Number(event.registered || 0);
    const capacity = Number(event.capacity || 0);
    const spots = Math.max(0, capacity - registered);
    const finished = isEventFinished(event);
    const full = spots <= 0;

    const stubPrice = finished ? 'ENDED' : (full ? 'FULL' : 'FREE');
    const stubSpots = finished
      ? 'Registration closed'
      : (full ? 'No spots left' : `${spots} spots left`);
    const badge = finished
      ? 'EVENT FINISHED'
      : (full ? 'FULLY BOOKED' : 'REGISTER NOW');

    return `
      <article class="ticket-card ${full || finished ? 'sold-out' : ''}"
        data-event-id="${escapeHtml(event.id)}"
        ${finished ? 'data-finished="true"' : ''}>
        <div class="ticket-main">
          <span class="cat-tag">${escapeHtml(event.category || 'Event')}</span>
          <h3 class="ticket-title">${escapeHtml(event.title)}</h3>
          <div class="ticket-meta">
            <div>📅 ${escapeHtml(fmtDate(event.date))} · ${escapeHtml(event.time || 'Time TBA')}</div>
            <div>📍 ${escapeHtml(event.location || 'Location TBA')}</div>
          </div>
          <div class="ticket-desc">${escapeHtml(event.description || '')}</div>
        </div>
        <div class="ticket-stub">
          <div class="stub-label">REGISTRATION</div>
          <div class="stub-price">${stubPrice}</div>
          <div class="stub-spots">${stubSpots}</div>
          <div class="admit-badge">${badge}</div>
        </div>
      </article>
    `;
  }

  function renderStats() {
    // Only upcoming events count as "available".
    $('#stat-events').textContent = events.filter(
      event => !isEventFinished(event)
    ).length;

    $('#stat-registrations').textContent = events.reduce(
      (total, event) => total + Number(event.registered || 0),
      0
    );

    $('#stat-mine').textContent = registrations.length;
  }

  function renderFilters() {
    const options = ['All', ...categories];

    if (!options.includes(activeCategory)) {
      activeCategory = 'All';
    }

    $('#filters-row').innerHTML = `
      <div class="filters">
        ${options.map(category => `
          <button type="button"
            class="chip ${category === activeCategory ? 'active' : ''}"
            data-category="${escapeHtml(category)}">
            ${escapeHtml(category)}
          </button>
        `).join('')}
      </div>
    `;

    $('#filters-row').querySelectorAll('[data-category]').forEach(button => {
      button.addEventListener('click', () => {
        activeCategory = button.dataset.category;
        renderFilters();
        renderEvents();
      });
    });
  }

  function attachEventCardHandlers(container) {
    container.querySelectorAll('[data-event-id]').forEach(card => {
      card.addEventListener('click', () => {
        const selectedEvent = events.find(
          item => item.id === card.dataset.eventId
        );

        if (!selectedEvent) return;

        // Finished events cannot be opened.
        if (isEventFinished(selectedEvent)) {
          showToast('This event has already finished.', 'error');
          return;
        }

        openRegistrationModal(selectedEvent);
      });
    });
  }

  function renderEvents() {
    const list = getFilteredEvents();

    $('#events-count').textContent =
      `${list.length} event${list.length === 1 ? '' : 's'}`;

    $('#grid').innerHTML = list.length
      ? list.map(eventCard).join('')
      : '<div class="empty-state" style="grid-column:1/-1">No events found. Try another search or category.</div>';

    attachEventCardHandlers($('#grid'));
  }

  function renderDashboard() {
    // Featured Events: upcoming only.
    const upcoming = events
      .filter(event => !isEventFinished(event))
      .sort((a, b) => new Date(a.date) - new Date(b.date))
      .slice(0, 3);

    $('#dashboard-events').innerHTML = upcoming.length
      ? upcoming.map(eventCard).join('')
      : '<div class="empty-state">No upcoming events available.</div>';

    attachEventCardHandlers($('#dashboard-events'));

    const recent = [...registrations]
      .sort((a, b) =>
        new Date(b.registeredAt || 0) - new Date(a.registeredAt || 0)
      )
      .slice(0, 3);

    $('#dashboard-registrations').innerHTML = recent.length
      ? recent.map(registration => `
          <div class="my-ticket-row">
            <div>
              <div class="mt-title">${escapeHtml(registration.eventTitle || 'Event')}</div>
              <div class="mt-sub">${escapeHtml(registrationDetails(registration))}</div>
            </div>
            <span class="attendance-status">
              ${escapeHtml(registration.attendanceStatus || 'registered').toUpperCase()}
            </span>
          </div>
        `).join('')
      : '<div class="empty-state">You have not registered for any events yet.</div>';
  }

  function renderMine() {
    const sorted = [...registrations].sort((a, b) =>
      new Date(b.registeredAt || 0) - new Date(a.registeredAt || 0)
    );

    $('#mine-list').innerHTML = sorted.length
      ? sorted.map(registration => {
        const event = events.find(item => item.id === registration.eventId);
        const date = event
          ? `${fmtDate(event.date)} · ${event.time || ''}`
          : 'Event details unavailable';

        const status = String(
          registration.attendanceStatus || 'registered'
        ).toLowerCase();

        // The "absent" label is not shown in My Registrations.
        const showStatus = status !== 'absent';

        // No cancelling once attended, marked absent, or the event finished.
        const canCancel =
          status !== 'attended' &&
          status !== 'absent' &&
          !isEventFinished(event);

        return `
            <div class="my-ticket-row">
              <div>
                <div class="mt-title">
                  ${escapeHtml(registration.eventTitle || event?.title || 'Event')}
                </div>
                <div class="mt-sub">${escapeHtml(date)}</div>
                <div class="mt-sub">
                  ${escapeHtml(registrationDetails(registration))}
                </div>
                ${showStatus
            ? `<div class="attendance-status">${escapeHtml(status.toUpperCase())}</div>`
            : ''}
              </div>
              ${canCancel ? `
                <button type="button" class="btn btn-ghost"
                  data-cancel-id="${escapeHtml(registration.id)}">
                  Cancel Registration
                </button>
              ` : ''}
            </div>
          `;
      }).join('')
      : '<div class="empty-state">No registrations yet. Browse events to register for a free spot.</div>';

    $('#mine-list').querySelectorAll('[data-cancel-id]').forEach(button => {
      button.addEventListener('click', async () => {
        if (!confirm('Cancel this registration?')) return;

        button.disabled = true;

        try {
          await api(
            `/api/registrations/${encodeURIComponent(button.dataset.cancelId)}/cancel`,
            { method: 'POST', body: '{}' }
          );

          await loadData();
          renderAll();
          showToast('Registration cancelled.');
        } catch (error) {
          showToast(error.message, 'error');
          button.disabled = false;
        }
      });
    });
  }

  function renderProfile() {
    const name = currentUser?.name || 'Participant';
    const studentId = currentUser?.student_id || currentUser?.studentId || '';
    const email = currentUser?.email || '';

    $('#profile-content').innerHTML = `
      <div class="profile-card">
        <div class="profile-avatar">👤</div>
        <div class="profile-details">
          <span class="dashboard-card-label">PARTICIPANT ACCOUNT</span>
          <h3>${escapeHtml(name)}</h3>
          <p><strong>Student ID:</strong> ${escapeHtml(studentId || 'Not available')}</p>
          <p><strong>Email:</strong> ${escapeHtml(email)}</p>
          <div class="profile-meta">Registrations: ${registrations.length}</div>
          <p class="profile-note">
            Your account information is used for your event registrations.
          </p>
        </div>
      </div>
    `;
  }

  function renderAttended() {
    const attended = registrations.filter(
      registration =>
        String(registration.attendanceStatus || '').toLowerCase() === 'attended'
    );

    $('#attended-list').innerHTML = attended.length
      ? attended.map(registration => {
        const event = events.find(item => item.id === registration.eventId);

        return `
            <div class="my-ticket-row">
              <div>
                <div class="mt-title">
                  ${escapeHtml(registration.eventTitle || event?.title || 'Event')}
                </div>
                <div class="mt-sub">
                  ${event
            ? escapeHtml(`${fmtDate(event.date)} · ${event.time || ''}`)
            : 'Event details unavailable'}
                </div>
              </div>
              <span class="attendance-status">ATTENDED</span>
            </div>
          `;
      }).join('')
      : '<div class="empty-state">Your attended events will appear here after the admin records your attendance.</div>';
  }

  function updateActiveTab() {
    const views = {
      dashboard: '#dashboard-view',
      events: '#events-view',
      mine: '#mine-view',
      profile: '#profile-view',
      attended: '#attended-view'
    };

    Object.entries(views).forEach(([tab, selector]) => {
      $(selector).hidden = activeTab !== tab;
    });

    document.querySelectorAll('[data-tab]').forEach(button => {
      button.classList.toggle('active', button.dataset.tab === activeTab);
    });

    const titles = {
      dashboard: 'Dashboard',
      events: 'Browse Events',
      mine: 'My Registrations',
      profile: 'My Profile',
      attended: 'Attended Events'
    };

    $('#page-heading').innerHTML =
      `${escapeHtml(titles[activeTab] || 'Dashboard')}<span>.</span>`;

    $('#search-wrap').hidden = activeTab !== 'events';
    $('#welcome-banner').hidden = activeTab !== 'dashboard';
  }

  function renderAll() {
    renderStats();
    renderFilters();
    renderEvents();
    renderDashboard();
    renderMine();
    renderProfile();
    renderAttended();
    updateActiveTab();
  }

  function openRegistrationModal(event) {
    // Finished events cannot be opened or registered for.
    if (isEventFinished(event)) {
      showToast('This event has already finished.', 'error');
      return;
    }

    const registered = Number(event.registered || 0);
    const capacity = Number(event.capacity || 0);
    const spots = Math.max(0, capacity - registered);

    if (spots <= 0) {
      showToast('This event has no spots left.', 'error');
      return;
    }

    const alreadyRegistered = registrations.some(
      item => item.eventId === event.id
    );

    if (alreadyRegistered) {
      showToast('You are already registered for this event.', 'error');
      return;
    }

    $('#modal-root').innerHTML = `
      <div class="overlay" id="registration-overlay">
        <div class="modal">
          <div class="modal-head">
            <div>
              <h3>${escapeHtml(event.title)}</h3>
              <div class="sub">
                ${escapeHtml(fmtDate(event.date))} ·
                ${escapeHtml(event.time || 'Time TBA')} ·
                ${escapeHtml(event.location || 'Location TBA')}
              </div>
            </div>
            <button type="button" class="close-x" id="registration-close">✕</button>
          </div>

          <div class="modal-body">
            <p>Fill in your details to register for this event.</p>

            <div class="field">
              <label for="registration-name">Full Name</label>
              <input id="registration-name" type="text" maxlength="100"
                value="${escapeHtml(currentUser?.name || '')}"
                placeholder="Enter your full name" required>
            </div>

            <div class="field">
              <label for="registration-course">Course</label>
              <select id="registration-course" required>
                <option value="">Select your course</option>
                <option value="BSCS">BSCS</option>
                <option value="BSIT">BSIT</option>
              </select>
            </div>

            <div class="field">
              <label for="registration-section">Section</label>
              <input id="registration-section" type="text" maxlength="1"
                placeholder="e.g. A" required>
            </div>

            <div class="field">
              <label for="registration-year">Year Level</label>
              <select id="registration-year" required>
                <option value="">Select year level</option>
                <option value="1st Year">1st Year</option>
                <option value="2nd Year">2nd Year</option>
                <option value="3rd Year">3rd Year</option>
                <option value="4th Year">4th Year</option>
              </select>
            </div>

            <div class="field">
              <label>Will you attend this event?</label>
              <div style="display:flex; gap:24px; margin-top:6px;">
                <label style="display:flex; align-items:center; gap:8px; font-weight:600; cursor:pointer;">
                  <input type="radio" name="registration-attending"
                    value="yes" style="width:auto; margin:0;"> Yes
                </label>
                <label style="display:flex; align-items:center; gap:8px; font-weight:600; cursor:pointer;">
                  <input type="radio" name="registration-attending"
                    value="no" style="width:auto; margin:0;"> No
                </label>
              </div>
            </div>
            <div id="registration-error" class="form-err" hidden></div>

            <button type="button" class="btn btn-gold submit-btn"
              id="registration-submit">
              Confirm Registration
            </button>
          </div>
        </div>
      </div>
    `;

    const close = () => {
      $('#modal-root').innerHTML = '';
    };

    $('#registration-close').addEventListener('click', close);

    $('#registration-overlay').addEventListener('click', clickEvent => {
      if (clickEvent.target.id === 'registration-overlay') close();
    });

    $('#registration-submit').addEventListener('click', async () => {
      const error = $('#registration-error');
      const button = $('#registration-submit');

      const name = $('#registration-name').value.trim();
      const course = $('#registration-course').value.trim();
      const section = $('#registration-section').value.trim();
      const year = $('#registration-year').value;
      const checked = document.querySelector(
        'input[name="registration-attending"]:checked'
      );
      const attending = checked ? checked.value : '';

      if (!name || !course || !section || !year || !attending) {
        error.textContent =
          'Please fill in all fields and answer whether you will attend.';
        error.hidden = false;
        return;
      }

      // Name must have no numbers.
      if (!NAME_RE.test(name)) {
        error.textContent = NAME_MESSAGE;
        error.hidden = false;
        return;
      }

      button.disabled = true;
      button.textContent = 'Submitting...';
      error.hidden = true;

      try {
        await api(
          `/api/events/${encodeURIComponent(event.id)}/register`,
          {
            method: 'POST',
            body: JSON.stringify({ name, course, section, year, attending })
          }
        );

        await loadData();
        close();
        renderAll();
        showToast(
          attending === 'yes'
            ? 'You are registered for this event!'
            : 'Thanks! Your response has been recorded.'
        );
      } catch (err) {
        error.textContent = err.message;
        error.hidden = false;
        button.disabled = false;
        button.textContent = 'Confirm Registration';
      }
    });
  }

  document.querySelectorAll('.side-link').forEach(button => {
    button.addEventListener('click', () => {
      activeTab = button.dataset.tab;
      updateActiveTab();
    });
  });

  document.querySelectorAll('[data-go-tab]').forEach(button => {
    button.addEventListener('click', event => {
      event.preventDefault();
      activeTab = button.dataset.goTab;
      updateActiveTab();
    });
  });

  $('#search').addEventListener('input', event => {
    searchTerm = event.target.value.trim();
    renderEvents();
  });

  async function init() {
    setAuthMode('login');

    try {
      const data = await api('/api/auth/me');

      if (data.role === 'admin') {
        window.location.href = '/admin';
        return;
      }

      if (data.role === 'participant' && data.user) {
        currentUser = data.user;
        await loadData();
        showApp();
        renderAll();
        return;
      }
    } catch (error) {
      // No active session; show the login form.
    }

    showAuth();
  }

  init();
})();