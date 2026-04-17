/**
 * CGP Monitor - Dashboard Frontend
 * Tracks CGP firms across French professional associations.
 */

const NTFY_TOPIC = 'cgp-monitor-cmf';
const CONTACTED_KEY = 'cgp-contacted';
const GIST_ID = ''; // Will be set after creating the Gist
const PAGE_SIZE = 50;

let allMembers = [];
let newMembers = [];
let groupementsData = {};
let displayOffset = 0;
let isSyncing = false;

// ============================================================
// CONTACTED TRACKER (localStorage)
// ============================================================
function getContacted() {
    try {
        return JSON.parse(localStorage.getItem(CONTACTED_KEY)) || {};
    } catch { return {}; }
}

function isContacted(memberId) {
    return !!getContacted()[memberId];
}

function toggleContacted(memberId) {
    const contacted = getContacted();
    if (contacted[memberId]) {
        delete contacted[memberId];
    } else {
        contacted[memberId] = new Date().toISOString().slice(0, 10);
    }
    localStorage.setItem(CONTACTED_KEY, JSON.stringify(contacted));
    updateContactedStat();
    renderCurrentTab();
    cloudSave();
}

function getContactedCount() {
    return Object.keys(getContacted()).length;
}

function updateContactedStat() {
    const el = document.getElementById('statContacted');
    if (el) el.textContent = getContactedCount();
}

// ============================================================
// CLOUD SYNC (GitHub Gist)
// ============================================================
function setSyncStatus(status, detail) {
    const el = document.getElementById('syncStatus');
    if (!el) return;
    const states = {
        syncing: { text: 'Sync...', color: 'var(--accent-orange)' },
        synced:  { text: 'Synced', color: 'var(--accent-green)' },
        error:   { text: 'Sync error', color: 'var(--accent-red)' },
        offline: { text: 'Local', color: 'var(--text-muted)' },
    };
    const s = states[status] || states.offline;
    el.textContent = detail || s.text;
    el.style.color = s.color;
}

async function cloudLoad() {
    if (!GIST_ID) { setSyncStatus('offline'); return; }
    try {
        setSyncStatus('syncing');
        const resp = await fetch(`https://api.github.com/gists/${GIST_ID}`, {
            headers: { 'Accept': 'application/vnd.github+json' },
        });
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const gist = await resp.json();
        const content = gist.files?.['cgp-contacted.json']?.content;
        if (!content) { setSyncStatus('synced', 'Cloud empty'); return; }

        const cloudData = JSON.parse(content);
        const localContacted = getContacted();
        const cloudContacted = cloudData.contacted || {};
        const merged = { ...cloudContacted, ...localContacted };

        localStorage.setItem(CONTACTED_KEY, JSON.stringify(merged));
        updateContactedStat();
        setSyncStatus('synced');
    } catch (e) {
        console.warn('Cloud load failed:', e);
        setSyncStatus('error', 'Load error');
    }
}

async function cloudSave() {
    if (!GIST_ID || isSyncing) return;
    isSyncing = true;
    try {
        setSyncStatus('syncing');
        const payload = JSON.stringify({
            contacted: getContacted(),
            last_sync: new Date().toISOString(),
        });
        const resp = await fetch(`https://api.github.com/gists/${GIST_ID}`, {
            method: 'PATCH',
            headers: {
                'Accept': 'application/vnd.github+json',
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                files: { 'cgp-contacted.json': { content: payload } }
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

            // Render dashboard association cards
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
        updateContactedStat();
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
    // Show 20 most recent new members on dashboard
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
    const hideContacted = document.getElementById('filterHideContacted')?.checked || false;

    return allMembers.filter(m => {
        if (hideContacted && isContacted(m.id)) return false;
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
    const hideContacted = document.getElementById('alertsHideContacted')?.checked || false;

    let alerts = allMembers
        .filter(m => m.is_new)
        .sort((a, b) => (b.first_seen || '').localeCompare(a.first_seen || ''));

    if (hideContacted) {
        alerts = alerts.filter(m => !isContacted(m.id));
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
    // Associations
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

    // Groupements
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
function renderMemberCard(m) {
    const contacted = isContacted(m.id);
    const assocBadges = Object.keys(m.associations || {})
        .map(a => `<span class="badge badge-assoc">${a.toUpperCase()}</span>`)
        .join('');
    const actBadges = (m.activities || [])
        .map(a => `<span class="badge badge-activity">${a}</span>`)
        .join('');
    const newBadge = m.is_new ? '<span class="badge-new">NOUVEAU</span>' : '';

    const addr = m.address || {};
    const location = [addr.city, addr.department ? `(${addr.department})` : ''].filter(Boolean).join(' ');

    const directors = (m.directors || []).map(d => {
        const safeName = escHtml(d.name || '');
        const safeRole = d.role ? ' - ' + escHtml(d.role) : '';
        const searchUrl = `https://www.linkedin.com/search/results/people/?keywords=${encodeURIComponent((d.name || '') + ' ' + (m.company_name || ''))}`;
        return `<a class="director-link" href="${searchUrl}" target="_blank" rel="noopener" title="Rechercher sur LinkedIn">${safeName}<span class="li-icon">in</span></a>${safeRole}`;
    }).join(', ');

    const contactInfo = [];
    if (m.phone) contactInfo.push(`<a href="tel:${m.phone}">${m.phone}</a>`);
    if (m.email) contactInfo.push(`<a href="mailto:${m.email}">${m.email}</a>`);
    if (m.website) contactInfo.push(`<a href="${m.website.startsWith('http') ? m.website : 'https://' + m.website}" target="_blank">${m.website}</a>`);

    return `
        <div class="member-card ${m.is_new ? 'is-new' : ''} ${contacted ? 'is-contacted' : ''}">
            <div class="member-info">
                <div class="member-header">
                    <span class="member-name">${escHtml(m.company_name)}</span>
                    ${newBadge}
                    ${assocBadges}
                    ${actBadges}
                </div>
                <div class="member-meta">
                    ${location ? `<span>${location}</span>` : ''}
                    ${m.siren ? `<span>SIREN: ${m.siren}</span>` : ''}
                    ${m.orias_number ? `<span>ORIAS: ${m.orias_number}</span>` : ''}
                </div>
                ${directors ? `<div class="member-directors">Dirigeant(s): ${directors}</div>` : ''}
                ${contactInfo.length ? `<div class="member-contact">${contactInfo.join('')}</div>` : ''}
            </div>
            <div class="member-actions">
                <label class="contact-toggle" title="Marquer comme contacte">
                    <input type="checkbox" ${contacted ? 'checked' : ''} onchange="toggleContacted('${m.id}')">
                    <span class="toggle-switch"></span>
                    Contacte
                </label>
                ${m.first_seen ? `<div class="member-date">Detecte: ${m.first_seen}</div>` : ''}
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
['searchInput', 'filterAssociation', 'filterDepartment', 'filterActivity'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.addEventListener(el.type === 'text' ? 'input' : 'change', renderDirectory);
});
document.getElementById('filterHideContacted')?.addEventListener('change', renderDirectory);
document.getElementById('alertsHideContacted')?.addEventListener('change', renderAlerts);

// ============================================================
// NOTIFICATIONS
// ============================================================
function setupNotifications() {
    document.getElementById('notifModal').style.display = 'flex';
}
function closeModal() {
    document.getElementById('notifModal').style.display = 'none';
}

// ============================================================
// INIT
// ============================================================
async function init() {
    await loadData();
    await cloudLoad();
}

init();
