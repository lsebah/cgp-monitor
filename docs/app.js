/**
 * CGP Monitor - Dashboard Frontend
 * Tracks CGP firms across French professional associations.
 */

const NTFY_TOPIC = 'cgp-monitor-cmf';
const STATUS_KEY = 'cgp-status';          // { [id]: { status, date } }
const FOLK_KEY = 'cgp-folk';              // { [id]: date }
const LEGACY_CONTACTED_KEY = 'cgp-contacted';
const SYNC_CONFIG_KEY = 'cgp-sync-config'; // { gistId, token }
const PAGE_SIZE = 50;

const STATUS_LABELS = {
    '':          'Non contacte',
    pending:     'En cours',
    contacted:   'Contacte',
    refused:     'Refus',
};

let allMembers = [];
let newMembers = [];
let groupementsData = {};
let displayOffset = 0;
let isSyncing = false;
let saveTimer = null;

// ============================================================
// STATUS + FOLK TRACKER (localStorage)
// ============================================================
function migrateLegacyContacted() {
    try {
        const legacy = JSON.parse(localStorage.getItem(LEGACY_CONTACTED_KEY) || 'null');
        if (!legacy) return;
        const status = JSON.parse(localStorage.getItem(STATUS_KEY) || '{}');
        let migrated = 0;
        for (const [id, date] of Object.entries(legacy)) {
            if (!status[id]) {
                status[id] = { status: 'contacted', date: typeof date === 'string' ? date : todayISO() };
                migrated++;
            }
        }
        if (migrated > 0) {
            localStorage.setItem(STATUS_KEY, JSON.stringify(status));
        }
        localStorage.removeItem(LEGACY_CONTACTED_KEY);
    } catch (e) {
        console.warn('Legacy migration failed:', e);
    }
}

function todayISO() { return new Date().toISOString().slice(0, 10); }

function getStatusMap() {
    try { return JSON.parse(localStorage.getItem(STATUS_KEY)) || {}; }
    catch { return {}; }
}
function getStatus(id) {
    return getStatusMap()[id]?.status || '';
}
function setStatus(id, status) {
    const map = getStatusMap();
    if (!status) {
        delete map[id];
    } else {
        map[id] = { status, date: todayISO() };
    }
    localStorage.setItem(STATUS_KEY, JSON.stringify(map));
    updateStats();
    renderCurrentTab();
    scheduleCloudSave();
}

function getFolkMap() {
    try { return JSON.parse(localStorage.getItem(FOLK_KEY)) || {}; }
    catch { return {}; }
}
function isInFolk(id) { return !!getFolkMap()[id]; }
function toggleFolk(id) {
    const map = getFolkMap();
    if (map[id]) delete map[id];
    else map[id] = todayISO();
    localStorage.setItem(FOLK_KEY, JSON.stringify(map));
    updateStats();
    renderCurrentTab();
    scheduleCloudSave();
}

function updateStats() {
    const statusMap = getStatusMap();
    const counts = { contacted: 0, pending: 0, refused: 0 };
    for (const v of Object.values(statusMap)) {
        if (counts[v.status] !== undefined) counts[v.status]++;
    }
    const elContacted = document.getElementById('statContacted');
    if (elContacted) elContacted.textContent = counts.contacted;
    const elPending = document.getElementById('statPending');
    if (elPending) elPending.textContent = counts.pending;
    const elRefused = document.getElementById('statRefused');
    if (elRefused) elRefused.textContent = counts.refused;
    const elFolk = document.getElementById('statFolk');
    if (elFolk) elFolk.textContent = Object.keys(getFolkMap()).length;
}

// ============================================================
// CLOUD SYNC (GitHub Gist - user-configurable)
// ============================================================
function getSyncConfig() {
    try { return JSON.parse(localStorage.getItem(SYNC_CONFIG_KEY)) || {}; }
    catch { return {}; }
}
function setSyncConfig(cfg) {
    localStorage.setItem(SYNC_CONFIG_KEY, JSON.stringify(cfg));
}

function setSyncStatus(status, detail) {
    const el = document.getElementById('syncStatus');
    if (!el) return;
    const states = {
        syncing: { text: 'Sync...', color: 'var(--accent-orange)' },
        synced:  { text: 'Synced',  color: 'var(--accent-green)' },
        error:   { text: 'Erreur',  color: 'var(--accent-red)' },
        offline: { text: 'Local',   color: 'var(--text-muted)' },
    };
    const s = states[status] || states.offline;
    el.textContent = detail || s.text;
    el.style.color = s.color;
}

async function cloudLoad() {
    const { gistId, token } = getSyncConfig();
    if (!gistId) { setSyncStatus('offline'); return; }
    try {
        setSyncStatus('syncing');
        const headers = { 'Accept': 'application/vnd.github+json' };
        if (token) headers['Authorization'] = `token ${token}`;
        const resp = await fetch(`https://api.github.com/gists/${gistId}`, { headers });
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const gist = await resp.json();
        const content = gist.files?.['cgp-monitor-state.json']?.content
                     || gist.files?.['cgp-contacted.json']?.content; // backwards compat
        if (!content) { setSyncStatus('synced', 'Cloud vide'); return; }

        const cloudData = JSON.parse(content);
        // Merge status map
        const localStatus = getStatusMap();
        const cloudStatus = cloudData.status || {};
        // Legacy shape: { contacted: { id: date } }
        if (cloudData.contacted && !cloudData.status) {
            for (const [id, date] of Object.entries(cloudData.contacted)) {
                if (!cloudStatus[id]) cloudStatus[id] = { status: 'contacted', date };
            }
        }
        const mergedStatus = { ...cloudStatus, ...localStatus };
        localStorage.setItem(STATUS_KEY, JSON.stringify(mergedStatus));

        // Merge folk map
        const localFolk = getFolkMap();
        const cloudFolk = cloudData.folk || {};
        const mergedFolk = { ...cloudFolk, ...localFolk };
        localStorage.setItem(FOLK_KEY, JSON.stringify(mergedFolk));

        updateStats();
        renderCurrentTab();
        setSyncStatus('synced');
    } catch (e) {
        console.warn('Cloud load failed:', e);
        setSyncStatus('error', 'Load err');
    }
}

function scheduleCloudSave() {
    if (saveTimer) clearTimeout(saveTimer);
    saveTimer = setTimeout(cloudSave, 800);
}

async function cloudSave() {
    const { gistId, token } = getSyncConfig();
    if (!gistId || !token || isSyncing) {
        if (!gistId) setSyncStatus('offline');
        return;
    }
    isSyncing = true;
    try {
        setSyncStatus('syncing');
        const payload = JSON.stringify({
            status: getStatusMap(),
            folk: getFolkMap(),
            last_sync: new Date().toISOString(),
        }, null, 2);
        const resp = await fetch(`https://api.github.com/gists/${gistId}`, {
            method: 'PATCH',
            headers: {
                'Accept': 'application/vnd.github+json',
                'Content-Type': 'application/json',
                'Authorization': `token ${token}`,
            },
            body: JSON.stringify({
                files: { 'cgp-monitor-state.json': { content: payload } }
            }),
        });
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        setSyncStatus('synced');
    } catch (e) {
        console.warn('Cloud save failed:', e);
        setSyncStatus('error');
    } finally {
        isSyncing = false;
    }
}

// ============================================================
// DATA LOADING
// ============================================================
async function loadData() {
    try {
        const [membersResp, newResp, groupResp] = await Promise.all([
            fetch('data/members.json').catch(() => null),
            fetch('data/new_members.json').catch(() => null),
            fetch('data/groupements.json').catch(() => null),
        ]);

        if (membersResp?.ok) {
            const data = await membersResp.json();
            allMembers = data.members || [];
            const stats = data.stats || {};

            document.getElementById('statTotal').textContent = stats.total_members || allMembers.length;
            document.getElementById('statNew').textContent = stats.new_this_week || 0;
            document.getElementById('statMonth').textContent = stats.new_this_month || 0;

            if (data.last_updated) {
                const d = new Date(data.last_updated);
                document.getElementById('lastUpdate').textContent =
                    `Mis a jour: ${d.toLocaleDateString('fr-FR')} ${d.toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' })}`;
            }

            // Populate department filter
            const depts = new Set();
            allMembers.forEach(m => {
                const d = m.address?.department;
                if (d) depts.add(d);
            });
            const deptSelect = document.getElementById('filterDepartment');
            Array.from(depts).sort().forEach(d => {
                const opt = document.createElement('option');
                opt.value = d;
                opt.textContent = `${d} - ${allMembers.find(m => m.address?.department === d)?.address?.department_name || d}`;
                deptSelect.appendChild(opt);
            });

            renderAssociationCards(data.scrape_status || {}, stats.by_association || {});
        }

        if (newResp?.ok) {
            const data = await newResp.json();
            newMembers = data.new_members || [];
            document.getElementById('badgeNew').textContent = newMembers.length || '';
        }

        if (groupResp?.ok) {
            groupementsData = await groupResp.json();
            renderGroupements();
        }

        document.getElementById('badgeTotal').textContent = allMembers.length || '';
        updateStats();
        renderDashboard();
        renderDirectory();
        renderAlerts();

    } catch (e) {
        console.warn('Error loading data:', e);
        document.getElementById('lastUpdate').textContent = 'En attente du premier scrape';
    }
}

// ============================================================
// RENDERING - Dashboard
// ============================================================
function renderAssociationCards(scrapeStatus, byAssociation) {
    const grid = document.getElementById('assocGrid');
    const assocs = [
        { key: 'cncgp', name: 'CNCGP', full: 'Conseillers en Gestion de Patrimoine' },
        { key: 'cncef', name: 'CNCEF', full: 'Conseils Experts Financiers' },
        { key: 'anacofi', name: 'ANACOFI', full: 'Conseils Financiers' },
        { key: 'orias', name: 'ORIAS', full: 'Registre officiel' },
    ];

    grid.innerHTML = assocs.map(a => {
        const status = scrapeStatus[a.key];
        const count = byAssociation[a.key] || 0;
        const statusClass = status?.status === 'success' ? 'success' : (status?.status === 'error' ? 'error' : '');
        const statusText = status?.status === 'success' ? 'OK' : (status?.status === 'error' ? 'Erreur' : 'En attente');

        return `
            <div class="assoc-card">
                <div class="assoc-name">${a.name}</div>
                <div class="assoc-label">${a.full}</div>
                <div class="assoc-count">${count.toLocaleString('fr-FR')}</div>
                <div class="assoc-label">membres</div>
                ${status ? `<span class="assoc-status ${statusClass}">${statusText}</span>` : ''}
            </div>
        `;
    }).join('');
}

function renderDashboard() {
    const grid = document.getElementById('recentNew');
    const recent = allMembers
        .filter(m => m.is_new)
        .sort((a, b) => (b.first_seen || '').localeCompare(a.first_seen || ''))
        .slice(0, 20);

    if (!recent.length) {
        grid.innerHTML = '<div class="empty-state"><p>Aucun nouveau membre detecte</p><p>Les alertes apparaitront ici apres le premier scrape.</p></div>';
        return;
    }
    grid.innerHTML = recent.map(m => renderMemberCard(m)).join('');
}

// ============================================================
// RENDERING - Directory
// ============================================================
function getFilteredMembers() {
    const search = document.getElementById('searchInput')?.value.toLowerCase() || '';
    const assocFilter = document.getElementById('filterAssociation')?.value || '';
    const deptFilter = document.getElementById('filterDepartment')?.value || '';
    const actFilter = document.getElementById('filterActivity')?.value || '';
    const statusFilter = document.getElementById('filterStatus')?.value || '';
    const hideProcessed = document.getElementById('filterHideProcessed')?.checked || false;

    return allMembers.filter(m => {
        const currentStatus = getStatus(m.id);
        if (hideProcessed && currentStatus) return false;
        if (statusFilter === '__none__' && currentStatus) return false;
        if (statusFilter && statusFilter !== '__none__' && currentStatus !== statusFilter) return false;
        if (assocFilter && !m.associations?.[assocFilter]) return false;
        if (deptFilter && m.address?.department !== deptFilter) return false;
        if (actFilter && !m.activities?.includes(actFilter)) return false;
        if (search) {
            const haystack = [
                m.company_name, m.address?.city, m.address?.department_name,
                m.email, m.phone, m.siren, m.orias_number,
                ...(m.directors || []).map(d => d.name),
                ...(m.activities || []),
            ].join(' ').toLowerCase();
            if (!haystack.includes(search)) return false;
        }
        return true;
    });
}

function renderDirectory() {
    displayOffset = 0;
    const filtered = getFilteredMembers();
    const grid = document.getElementById('membersGrid');
    const countEl = document.getElementById('membersCount');
    const loadBtn = document.getElementById('loadMoreBtn');

    countEl.textContent = `${filtered.length.toLocaleString('fr-FR')} CGP trouves`;

    const page = filtered.slice(0, PAGE_SIZE);
    grid.innerHTML = page.length
        ? page.map(m => renderMemberCard(m)).join('')
        : '<div class="empty-state"><p>Aucun resultat</p></div>';

    displayOffset = PAGE_SIZE;
    loadBtn.style.display = filtered.length > PAGE_SIZE ? 'block' : 'none';
}

function loadMore() {
    const filtered = getFilteredMembers();
    const grid = document.getElementById('membersGrid');
    const loadBtn = document.getElementById('loadMoreBtn');

    const page = filtered.slice(displayOffset, displayOffset + PAGE_SIZE);
    grid.innerHTML += page.map(m => renderMemberCard(m)).join('');
    displayOffset += PAGE_SIZE;
    loadBtn.style.display = displayOffset < filtered.length ? 'block' : 'none';
}

// ============================================================
// RENDERING - Alerts
// ============================================================
function renderAlerts() {
    const grid = document.getElementById('alertsGrid');
    const hideProcessed = document.getElementById('alertsHideProcessed')?.checked || false;

    let alerts = allMembers
        .filter(m => m.is_new)
        .sort((a, b) => (b.first_seen || '').localeCompare(a.first_seen || ''));

    if (hideProcessed) {
        alerts = alerts.filter(m => !getStatus(m.id));
    }

    if (!alerts.length) {
        grid.innerHTML = '<div class="empty-state"><p>Aucune alerte</p></div>';
        return;
    }
    grid.innerHTML = alerts.map(m => renderMemberCard(m)).join('');
}

// ============================================================
// RENDERING - Groupements
// ============================================================
function renderGroupements() {
    const assocGrid = document.getElementById('associationsGrid');
    const associations = groupementsData.associations || [];
    assocGrid.innerHTML = associations.map(a => `
        <div class="groupement-card">
            <div class="groupement-name">${a.name}</div>
            <span class="groupement-type groupement">${a.full_name}</span>
            <div class="groupement-desc">${a.description}</div>
            <div style="margin-top:4px;font-size:13px;color:var(--text-muted)">~${a.members_approx} membres</div>
            <a href="${a.website}" target="_blank" class="groupement-link">${a.website}</a>
        </div>
    `).join('');

    const grpGrid = document.getElementById('groupementsGrid');
    const groupements = groupementsData.groupements || [];
    grpGrid.innerHTML = groupements.map(g => `
        <div class="groupement-card">
            <div class="groupement-name">${g.name}</div>
            <span class="groupement-type ${g.type}">${g.type}</span>
            <div class="groupement-desc">${g.description}</div>
            ${g.website ? `<a href="${g.website}" target="_blank" class="groupement-link">${g.website}</a>` : ''}
        </div>
    `).join('');
}

// ============================================================
// RENDERING - Member Card (shared)
// ============================================================
function linkedinSearchUrl(name, company) {
    const query = [name, company].filter(Boolean).join(' ');
    return `https://www.linkedin.com/search/results/people/?keywords=${encodeURIComponent(query)}&origin=GLOBAL_SEARCH_HEADER`;
}

function renderDirectorsHtml(m) {
    const directors = m.directors || [];
    if (!directors.length) return '';
    const html = directors.map(d => {
        const safeName = escHtml(d.name || '');
        const url = linkedinSearchUrl(d.name, m.company_name);
        const roleTxt = d.role ? ` <span class="director-role">- ${escHtml(d.role)}</span>` : '';
        return `<a class="director-link" href="${url}" target="_blank" rel="noopener" title="Rechercher ${safeName} sur LinkedIn" itemprop="name">
                    <span class="linkedin-ico" aria-hidden="true">in</span>${safeName}
                </a>${roleTxt}`;
    }).join(', ');
    return `<div class="member-directors">Dirigeant(s) : ${html}</div>`;
}

function renderMemberCard(m) {
    const currentStatus = getStatus(m.id);
    const inFolk = isInFolk(m.id);

    const assocBadges = Object.keys(m.associations || {})
        .map(a => `<span class="badge badge-assoc">${a.toUpperCase()}</span>`)
        .join('');
    const actBadges = (m.activities || [])
        .map(a => `<span class="badge badge-activity">${a}</span>`)
        .join('');
    const newBadge = m.is_new ? '<span class="badge-new">NOUVEAU</span>' : '';
    const statusBadge = currentStatus
        ? `<span class="badge-status status-${currentStatus}">${STATUS_LABELS[currentStatus]}</span>`
        : '';

    const addr = m.address || {};
    const location = [addr.city, addr.department ? `(${addr.department})` : ''].filter(Boolean).join(' ');

    const contactInfo = [];
    if (m.phone) contactInfo.push(`<a href="tel:${m.phone}" itemprop="telephone">${escHtml(m.phone)}</a>`);
    if (m.email) contactInfo.push(`<a href="mailto:${m.email}" itemprop="email">${escHtml(m.email)}</a>`);
    if (m.website) {
        const href = m.website.startsWith('http') ? m.website : 'https://' + m.website;
        contactInfo.push(`<a href="${href}" target="_blank" rel="noopener" itemprop="url">${escHtml(m.website)}</a>`);
    }

    const cardClasses = [
        'member-card',
        m.is_new ? 'is-new' : '',
        currentStatus ? `has-status status-${currentStatus}` : '',
        inFolk ? 'in-folk' : '',
    ].filter(Boolean).join(' ');

    // Status options
    const statusOptions = Object.entries(STATUS_LABELS)
        .map(([val, label]) => `<option value="${val}" ${currentStatus === val ? 'selected' : ''}>${label}</option>`)
        .join('');

    return `
        <div class="${cardClasses}" itemscope itemtype="https://schema.org/Organization" data-member-id="${m.id}">
            <div class="member-info">
                <div class="member-header">
                    <span class="member-name" itemprop="name">${escHtml(m.company_name)}</span>
                    ${newBadge}
                    ${statusBadge}
                    ${assocBadges}
                    ${actBadges}
                </div>
                <div class="member-meta">
                    ${location ? `<span itemprop="address" itemscope itemtype="https://schema.org/PostalAddress"><span itemprop="addressLocality">${escHtml(location)}</span></span>` : ''}
                    ${m.siren ? `<span>SIREN: ${escHtml(m.siren)}</span>` : ''}
                    ${m.orias_number ? `<span>ORIAS: ${escHtml(m.orias_number)}</span>` : ''}
                </div>
                ${renderDirectorsHtml(m)}
                ${contactInfo.length ? `<div class="member-contact">${contactInfo.join('')}</div>` : ''}
            </div>
            <div class="member-actions">
                <select class="status-select status-select-${currentStatus || 'none'}"
                        onchange="setStatus('${m.id}', this.value)"
                        title="Statut de contact">
                    ${statusOptions}
                </select>
                <label class="folk-toggle" title="Marquer comme ajoute dans Folk">
                    <input type="checkbox" ${inFolk ? 'checked' : ''} onchange="toggleFolk('${m.id}')">
                    <span class="toggle-switch folk-switch"></span>
                    <span>Folk</span>
                </label>
                ${m.first_seen ? `<div class="member-date">Detecte: ${escHtml(m.first_seen)}</div>` : ''}
            </div>
        </div>
    `;
}

function escHtml(text) {
    const div = document.createElement('div');
    div.textContent = text || '';
    return div.innerHTML;
}

// ============================================================
// TABS
// ============================================================
function renderCurrentTab() {
    const active = document.querySelector('.tab.active');
    if (!active) return;
    const tabName = active.dataset.tab;
    switch (tabName) {
        case 'dashboard': renderDashboard(); break;
        case 'directory': renderDirectory(); break;
        case 'alerts': renderAlerts(); break;
        case 'groupements': renderGroupements(); break;
    }
}

document.querySelectorAll('.tab').forEach(tab => {
    tab.addEventListener('click', () => {
        document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
        document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
        tab.classList.add('active');
        document.getElementById(`tab-${tab.dataset.tab}`).classList.add('active');
        renderCurrentTab();
    });
});

// Filter event listeners
['searchInput', 'filterAssociation', 'filterDepartment', 'filterActivity', 'filterStatus'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.addEventListener(el.type === 'text' ? 'input' : 'change', renderDirectory);
});
document.getElementById('filterHideProcessed')?.addEventListener('change', renderDirectory);
document.getElementById('alertsHideProcessed')?.addEventListener('change', renderAlerts);

// ============================================================
// NOTIFICATIONS / SYNC SETTINGS MODALS
// ============================================================
function setupNotifications() {
    document.getElementById('notifModal').style.display = 'flex';
}
function closeModal(id) {
    document.getElementById(id || 'notifModal').style.display = 'none';
}

function openSyncSettings() {
    const { gistId, token } = getSyncConfig();
    document.getElementById('syncGistId').value = gistId || '';
    document.getElementById('syncToken').value = token || '';
    document.getElementById('syncModal').style.display = 'flex';
}

async function saveSyncSettings() {
    const gistId = document.getElementById('syncGistId').value.trim();
    const token = document.getElementById('syncToken').value.trim();
    setSyncConfig({ gistId, token });
    closeModal('syncModal');
    if (gistId) {
        await cloudLoad();
        if (token) await cloudSave();
    } else {
        setSyncStatus('offline');
    }
}

function clearSyncSettings() {
    if (!confirm('Effacer les identifiants de sync ?')) return;
    localStorage.removeItem(SYNC_CONFIG_KEY);
    closeModal('syncModal');
    setSyncStatus('offline');
}

// Expose handlers used by inline HTML
window.setStatus = setStatus;
window.toggleFolk = toggleFolk;
window.loadMore = loadMore;
window.setupNotifications = setupNotifications;
window.closeModal = closeModal;
window.openSyncSettings = openSyncSettings;
window.saveSyncSettings = saveSyncSettings;
window.clearSyncSettings = clearSyncSettings;

// ============================================================
// INIT
// ============================================================
async function init() {
    migrateLegacyContacted();
    await loadData();
    await cloudLoad();
}

init();
