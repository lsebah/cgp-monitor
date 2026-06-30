/**
 * CGP Monitor - Dashboard Frontend
 * Tracks CGP firms across French professional associations.
 */

const APP_VERSION = '12';
const NTFY_TOPIC = 'cgp-monitor-cmf';
const STATUS_KEY = 'cgp-status';          // { [id]: { status, date } }
const FOLK_KEY = 'cgp-folk';              // { [id]: date }
const LEGACY_CONTACTED_KEY = 'cgp-contacted';
const SYNC_CONFIG_KEY = 'cgp-sync-config'; // { gistId, token }
const FOLK_API_KEY = 'cgp-folk-api-key';  // Folk CRM API key (localStorage)
const FOLK_API_BASE = 'https://api.folk.app/v1';
const PAGE_SIZE = 50;

const STATUS_LABELS = {
    '':          'Non contacte',
    pending:     'En cours',
    contacted:   'Contacte',
    refused:     'Refus',
};

// Clean labels for the register / association badges on each cabinet card.
const ASSOC_LABELS = {
    cncgp: 'CNCGP', cncef: 'CNCEF', anacofi: 'ANACOFI',
    affo: 'AFFO', ucgp: 'UCGP', orias: 'ORIAS',
};
const ASSOC_TITLES = {
    cncgp: 'Chambre Nationale des Conseillers en Gestion de Patrimoine',
    cncef: 'Chambre Nationale des Conseils Experts Financiers',
    anacofi: 'Association Nationale des Conseils Financiers',
    affo: 'Association Francaise du Family Office',
    ucgp: 'Union des Conseils en Gestion de Patrimoine',
    orias: 'Registre officiel ORIAS',
};
// Internal bookkeeping tags that are NOT registers — never shown as a badge.
const ASSOC_HIDDEN = new Set(['registre', 'manuel', 'leaders_league']);

let allMembers = [];
let newMembers = [];
let groupementsData = {};
let cartoData = null;          // cartographie acteurs (groupements/assos/reseaux/FO)
let actorsIndex = [];          // flat list of entities + CGP cabinets
let actorsDisplayOffset = 0;
let displayOffset = 0;
let isSyncing = false;
let saveTimer = null;

const ACTORS_PAGE_SIZE = 60;

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
    refreshCardInPlace(id);
    scheduleCloudSave();
}

function getFolkMap() {
    try { return JSON.parse(localStorage.getItem(FOLK_KEY)) || {}; }
    catch { return {}; }
}
function isInFolk(id) { return !!getFolkMap()[id]; }
function toggleFolk(id) {
    const map = getFolkMap();
    const adding = !map[id];
    if (adding) map[id] = todayISO();
    else delete map[id];
    localStorage.setItem(FOLK_KEY, JSON.stringify(map));
    updateStats();
    refreshCardInPlace(id);
    scheduleCloudSave();
    if (adding && getFolkApiKey()) {
        const m = allMembers.find(x => x.id === id);
        if (m) folkPushContact(m);
    }
}

// Replace only the affected card DOM (in all visible tabs) instead of
// re-rendering the entire tab. Preserves pagination / scroll position.
function refreshCardInPlace(id) {
    const esc = (typeof CSS !== 'undefined' && CSS.escape) ? CSS.escape(id) : id.replace(/"/g, '\\"');
    let replaced = 0;

    // Member cards (Annuaire, Alerts, Dashboard, Acteurs cabinets)
    const memberCards = document.querySelectorAll(`[data-member-id="${esc}"]`);
    if (memberCards.length) {
        const m = allMembers.find(x => x.id === id);
        if (m) {
            memberCards.forEach(card => {
                const tmp = document.createElement('div');
                tmp.innerHTML = renderMemberCard(m);
                const fresh = tmp.firstElementChild;
                if (fresh) { card.replaceWith(fresh); replaced++; }
            });
        }
    }

    // Actor (cartographie) cards
    const actorCards = document.querySelectorAll(`[data-actor-id="${esc}"]`);
    if (actorCards.length) {
        const a = actorsIndex.find(x => x._key === id);
        if (a) {
            actorCards.forEach(card => {
                const tmp = document.createElement('div');
                tmp.innerHTML = a._type === 'cabinet' ? renderMemberCard(a._member) : renderActorCard(a);
                const fresh = tmp.firstElementChild;
                if (fresh) { card.replaceWith(fresh); replaced++; }
            });
        }
    }

    console.debug(`[refreshCardInPlace] id=${id} replaced=${replaced} cards`);

    // Update the Acteurs tab stat pills if that tab is currently visible
    if (document.getElementById('tab-actors')?.classList.contains('active')) {
        renderActorsStats();
    }
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
        const [membersResp, newResp, groupResp, cartoResp] = await Promise.all([
            // cache:'no-cache' = always revalidate with the server (ETag/304) but
            // reuse the cached copy when unchanged. Fresh data without re-downloading
            // the 15 MB file on every visit -> much faster than 'no-store'.
            fetch('data/members.json', { cache: 'no-cache' }).catch(() => null),
            fetch('data/new_members.json', { cache: 'no-cache' }).catch(() => null),
            fetch('data/groupements.json', { cache: 'no-cache' }).catch(() => null),
            fetch('data/20260413_cartographie_groupements_cgp.json', { cache: 'no-cache' }).catch(() => null),
        ]);

        if (membersResp?.ok) {
            const data = await membersResp.json();
            const rawMembers = data.members || [];
            const stats = data.stats || {};

            // Keep the full member list. The "actionable" filter (must have
            // email/phone/website/director) used to run here, but it hid all
            // 3243 ANACOFI entries (whose source provides only name + SIREN)
            // and 346/353 CNCEF entries — making the association filter
            // useless for those two. Now the contact filter only applies
            // when the user is browsing the default view (no association
            // selected), see getFilteredMembers().
            allMembers = rawMembers;
            const withContact = rawMembers.filter(m =>
                m.email || m.phone || m.website || (m.directors && m.directors.length > 0)
            ).length;
            console.info(`Members loaded: ${allMembers.length} total, ${withContact} with contact info`);

            // Total CGP shows the actionable count (with contact) by default.
            document.getElementById('statTotal').textContent = withContact;
            // Stay aligned with Total CGP: only count members that have at least
            // one piece of contact info, so these tiles can never exceed Total.
            // Use the SHARED helpers (UTC) so the tiles and the Annuaire filter
            // can never diverge (they used to: local-midnight vs UTC gave 5 vs 2).
            let recent30d = 0, recent6m = 0;
            for (const m of allMembers) {
                if (!(m.email || m.phone || m.website || (m.directors && m.directors.length))) continue;
                if (isCreatedWithin(m, '30d')) recent30d++;
                if (isCreatedWithin(m, '6m')) recent6m++;
            }
            const elRecent30d = document.getElementById('statRecent30d');
            if (elRecent30d) elRecent30d.textContent = recent30d;
            const elRecent6m = document.getElementById('statRecent6m');
            if (elRecent6m) elRecent6m.textContent = recent6m;

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

            // Populate groupement filter (Cyrus, Laplace, Magnacarta, Actualis...)
            const grpCounts = {};
            allMembers.forEach(m => {
                const g = m.groupement
                    || (m.associations && m.associations.ucgp && m.associations.ucgp.groupement)
                    || '';
                if (g) grpCounts[g] = (grpCounts[g] || 0) + 1;
            });
            const grpSelect = document.getElementById('filterGroupement');
            if (grpSelect) {
                Object.keys(grpCounts).sort().forEach(g => {
                    const opt = document.createElement('option');
                    opt.value = g;
                    opt.textContent = `${g} (${grpCounts[g]})`;
                    grpSelect.appendChild(opt);
                });
            }

            renderAssociationCards(data.scrape_status || {}, stats.by_association || {});
        }

        if (newResp?.ok) {
            const data = await newResp.json();
            newMembers = data.new_members || [];
            const badgeNew = document.getElementById('badgeNew');
            if (badgeNew) badgeNew.textContent = newMembers.length || '';
        }

        if (groupResp?.ok) {
            groupementsData = await groupResp.json();
            renderGroupements();
        }

        if (cartoResp?.ok) {
            cartoData = await cartoResp.json();
            buildActorsIndex();
            populateActorsFilters();
            renderActors();
        }

        // Keep the Annuaire badge identical to the Total CGP tile (browsable count
        // = members with at least one contact detail), so the numbers never differ.
        const browsableCount = allMembers.filter(m =>
            m.email || m.phone || m.website || (m.directors && m.directors.length > 0)
        ).length;
        document.getElementById('badgeTotal').textContent = browsableCount || '';
        updateStats();
        renderDashboard();
        renderDirectory();

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
        { key: 'other', name: 'Hors association', full: 'CIF hors des 3 chambres' },
    ];

    // Count each cabinet in only ONE association (priority: cncgp > cncef > anacofi)
    // so a cabinet in two associations isn't double-counted. This makes the cards
    // a clean partition: their sum = number of affiliated cabinets.
    const liveCounts = { cncgp: 0, cncef: 0, anacofi: 0 };
    let affiliated = 0, unaffiliated = 0;
    for (const m of allMembers) {
        if (!(m.email || m.phone || m.website || (m.directors && m.directors.length))) continue;
        const as = m.associations || {};
        if (as.cncgp) { liveCounts.cncgp++; affiliated++; }
        else if (as.cncef) { liveCounts.cncef++; affiliated++; }
        else if (as.anacofi) { liveCounts.anacofi++; affiliated++; }
        else { unaffiliated++; }
    }
    liveCounts.other = unaffiliated;

    grid.innerHTML = assocs.map(a => {
        const count = liveCounts[a.key] || 0;
        const clickable = a.clickable !== false && count > 0;
        const onclick = clickable ? `onclick="filterByAssociation('${a.key}')" role="button" tabindex="0"` : '';
        return `
            <div class="assoc-card" ${onclick}>
                <div class="assoc-name">${a.name}</div>
                <div class="assoc-label">${a.full}</div>
                <div class="assoc-count">${count.toLocaleString('fr-FR')}</div>
                <div class="assoc-label">membres</div>
            </div>
        `;
    }).join('');
}

// Click on an association card → switch to Annuaire tab and apply the filter.
function filterByAssociation(key) {
    const filterEl = document.getElementById('filterAssociation');
    window.synthFilters = {};
    if (key === 'other') {
        // CIF outside the 3 chambers (e.g. manually added cabinets)
        if (filterEl) filterEl.value = '';
        window.synthFilters = { unaffiliated: true };
    } else if (filterEl) {
        filterEl.value = key;
    }
    _switchToDirectory();
}

// --- Creation-date window helpers (UTC, SHARED by the stat tiles AND the
// Annuaire creation filter) so the two can never disagree. A `key` is like
// '30d' (days), '6m' (months) or '1'..'5' (years).
function _todayIsoUTC() {
    const d = new Date();
    d.setUTCHours(0, 0, 0, 0);
    return d.toISOString().slice(0, 10);
}
function creationCutoffIso(key) {
    const d = new Date();
    d.setUTCHours(0, 0, 0, 0);
    if (key.endsWith('d')) d.setUTCDate(d.getUTCDate() - parseInt(key, 10));
    else if (key.endsWith('m')) d.setUTCMonth(d.getUTCMonth() - parseInt(key, 10));
    else d.setUTCFullYear(d.getUTCFullYear() - parseInt(key, 10));
    return d.toISOString().slice(0, 10);
}
// True if member was created within the window [cutoff(key), today], excluding
// future-dated (post-dated) registrations.
function isCreatedWithin(m, key) {
    const cd = m.creation_date;
    if (!cd) return false;
    if (cd > _todayIsoUTC()) return false;
    return cd >= creationCutoffIso(key);
}

// Click on a top dashboard stat tile → switch to Annuaire and apply the matching filter.
// `kind` is one of: all | recent30d | recent6m | pending | contacted | refused | folk
function applyDashboardFilter(kind) {
    // Highlight the clicked tile (active state). Tiles are in DOM order:
    // all, recent30d, recent6m, pending, contacted, refused, folk
    const order = ['all', 'recent30d', 'recent6m', 'pending', 'contacted', 'refused', 'folk'];
    const cards = document.querySelectorAll('.stats-bar .stat-card');
    cards.forEach(c => c.classList.remove('active'));
    const idx = order.indexOf(kind);
    if (idx >= 0 && cards[idx]) cards[idx].classList.add('active');

    // Reset all form filters first
    const reset = ['searchInput', 'filterAssociation', 'filterDepartment',
                   'filterActivity', 'filterStatus', 'filterCreation',
                   'filterGroupement', 'filterCa', 'filterAum',
                   'filterStructured', 'filterExpertise'];
    reset.forEach(id => { const el = document.getElementById(id); if (el) el.value = ''; });
    const hide = document.getElementById('filterHideProcessed');
    if (hide) hide.checked = false;
    window.synthFilters = {};

    switch (kind) {
        case 'all':
            // No extra filter — default actionable view (hides no-contact cabinets).
            break;
        case 'recent30d':
            document.getElementById('filterCreation').value = '30d';
            break;
        case 'recent6m':
            document.getElementById('filterCreation').value = '6m';
            break;
        case 'pending':
            document.getElementById('filterStatus').value = 'pending';
            break;
        case 'contacted':
            document.getElementById('filterStatus').value = 'contacted';
            break;
        case 'refused':
            document.getElementById('filterStatus').value = 'refused';
            break;
        case 'folk':
            window.synthFilters = { folkOnly: true };
            break;
    }
    _switchToDirectory();
}

function _switchToDirectory() {
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
    const directoryTab = document.querySelector('.tab[data-tab="directory"]');
    const directoryContent = document.getElementById('tab-directory');
    if (directoryTab) directoryTab.classList.add('active');
    if (directoryContent) directoryContent.classList.add('active');
    renderDirectory();
}

function renderDashboard() {
    const grid = document.getElementById('recentNew');
    const recent = allMembers
        .filter(m => m.is_new)
        .sort((a, b) => (b.first_seen || '').localeCompare(a.first_seen || ''))
        .slice(0, 20);

    if (!recent.length) {
        grid.innerHTML = '<div class="empty-state"><p>Aucun nouveau membre detecte</p><p>Les nouveaux cabinets apparaitront ici apres le prochain scrape.</p></div>';
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
    const creationFilter = document.getElementById('filterCreation')?.value || '';
    const groupementFilter = document.getElementById('filterGroupement')?.value || '';
    const caFilter = document.getElementById('filterCa')?.value || '';
    const aumFilter = document.getElementById('filterAum')?.value || '';
    const structuredFilter = document.getElementById('filterStructured')?.value || '';
    const expertiseFilter = document.getElementById('filterExpertise')?.value || '';
    const hideProcessed = document.getElementById('filterHideProcessed')?.checked || false;

    // Threshold maps for the CA/AUM range filters
    const CA_THRESHOLDS = { any: 0, '100k': 100_000, '500k': 500_000,
                            '1M': 1_000_000, '5M': 5_000_000 };
    const AUM_THRESHOLDS = { any: 0, '100M': 100_000_000,
                             '500M': 500_000_000, '1B': 1_000_000_000 };

    // creation_date window — uses the SAME shared helper as the stat tiles so
    // the "Créés < 7j" count and this filter always return the same set.
    // Filter values: "7d"/"30d" (days), "4m" (months), or "1".."5" (years).
    const creationCutoff = creationFilter ? creationCutoffIso(creationFilter) : null;

    // synthFilters is set by applyDashboardFilter (clicks on top stat tiles)
    // for filters that have no UI widget (first_seen, Folk membership).
    const synth = window.synthFilters || {};
    const folkMap = synth.folkOnly ? getFolkMap() : null;
    let firstSeenCutoff = null;
    if (synth.firstSeenDays) {
        const d = new Date();
        d.setDate(d.getDate() - synth.firstSeenDays);
        firstSeenCutoff = d.toISOString().slice(0, 10);
    }

    const result = allMembers.filter(m => {
        // Default view (no association picked): hide cabinets with no
        // reachable contact info — they're not actionable for prospection.
        // When the user explicitly filters by an association (especially
        // ANACOFI / CNCEF, where the source ships only name + SIREN), this
        // filter is bypassed so the filter actually returns rows.
        if (!assocFilter && !synth.unaffiliated) {
            const hasContact = m.email || m.phone || m.website || (m.directors && m.directors.length > 0);
            if (!hasContact) return false;
        }
        if (synth.unaffiliated) {
            const a = m.associations || {};
            if (a.cncgp || a.cncef || a.anacofi) return false;
        }
        if (folkMap && !folkMap[m.id]) return false;
        if (firstSeenCutoff) {
            if (!m.first_seen || m.first_seen < firstSeenCutoff) return false;
        }
        const currentStatus = getStatus(m.id);
        if (hideProcessed && currentStatus) return false;
        if (statusFilter === '__none__' && currentStatus) return false;
        if (statusFilter && statusFilter !== '__none__' && currentStatus !== statusFilter) return false;
        if (assocFilter && !m.associations?.[assocFilter]) return false;
        if (deptFilter && m.address?.department !== deptFilter) return false;
        if (actFilter && !m.activities?.includes(actFilter)) return false;
        if (groupementFilter) {
            const memberGroupement = m.groupement
                || (m.associations && m.associations.ucgp && m.associations.ucgp.groupement)
                || '';
            if (groupementFilter === '__none__') {
                if (memberGroupement) return false;
            } else if (memberGroupement !== groupementFilter) {
                return false;
            }
        }
        if (creationCutoff) {
            // Reject if no creation_date, older than cutoff, or FUTURE-dated.
            if (!m.creation_date || m.creation_date < creationCutoff
                || m.creation_date > _todayIsoUTC()) return false;
        }
        if (caFilter) {
            const ca = m.finances_data_gouv?.ca_eur;
            const fy = m.finances_data_gouv?.year;
            // Reject pre-2023 OR missing OR ca:0 (data.gouv's "no declaration" code)
            if (ca == null || ca === 0 || !fy || fy < 2023) return false;
            const threshold = CA_THRESHOLDS[caFilter] ?? 0;
            if (ca < threshold) return false;
        }
        if (aumFilter) {
            const aum = m.website_data?.aum_eur;
            if (aum == null) return false;
            const threshold = AUM_THRESHOLDS[aumFilter] ?? 0;
            if (aum < threshold) return false;
        }
        if (structuredFilter) {
            const sp = m.website_data?.has_structured_products;
            if (structuredFilter === 'yes' && sp !== true) return false;
            if (structuredFilter === 'no' && sp !== false) return false;
        }
        if (expertiseFilter) {
            const expertises = m.website_data?.expertises || [];
            if (!expertises.includes(expertiseFilter)) return false;
        }
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

    // Sort by company creation date, most recent first. Cabinets without a
    // known creation date go last (kept in name order among themselves).
    result.sort((a, b) => {
        const da = a.creation_date || '';
        const db = b.creation_date || '';
        if (da && db) return db.localeCompare(da);
        if (da) return -1;
        if (db) return 1;
        return (a.company_name || '').localeCompare(b.company_name || '');
    });
    return result;
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
    // insertAdjacentHTML preserves existing DOM nodes (incl. event listeners,
    // focus, form state) instead of rebuilding the whole list like innerHTML +=.
    grid.insertAdjacentHTML('beforeend', page.map(m => renderMemberCard(m)).join(''));
    displayOffset += PAGE_SIZE;
    loadBtn.style.display = displayOffset < filtered.length ? 'block' : 'none';
}

// ============================================================
// EXPORT - Directory CSV (Folk-compatible, respects active filters)
// ============================================================
function csvCell(value) {
    const s = value == null ? '' : String(value);
    return /[",\r\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
}

// Reproduces scraper/folk_export.py: one row per director, same columns.
function buildFolkCsv(members) {
    const columns = ['First Name', 'Last Name', 'Job Title', 'Company',
        'Email', 'Phone', 'Address', 'City', 'Postal Code', 'Website', 'Notes'];
    const rows = [columns.join(',')];

    members.forEach(m => {
        const directors = (m.directors && m.directors.length)
            ? m.directors : [{ name: '', role: '' }];

        directors.forEach(d => {
            const parts = (d.name || '').trim().split(/\s+/).filter(Boolean);
            const firstName = parts[0] || '';
            const lastName = parts.slice(1).join(' ');

            const notes = [];
            const assocs = Object.keys(m.associations || {});
            if (assocs.length) notes.push(`Associations: ${assocs.join(', ')}`);
            if (m.activities && m.activities.length) notes.push(`Activites: ${m.activities.join(', ')}`);
            if (m.orias_number) notes.push(`ORIAS: ${m.orias_number}`);
            if (m.siren) notes.push(`SIREN: ${m.siren}`);
            if (m.first_seen) notes.push(`Detecte: ${m.first_seen}`);
            notes.push('Source: CGP Monitor');

            const addr = m.address || {};
            const cells = [
                firstName, lastName, d.role || 'Dirigeant', m.company_name || '',
                m.email || '', m.phone || '', addr.street || '', addr.city || '',
                addr.postal_code || '', m.website || '', notes.join(' | '),
            ];
            rows.push(cells.map(csvCell).join(','));
        });
    });
    return rows.join('\r\n');
}

function exportDirectoryCsv() {
    const members = getFilteredMembers();
    if (!members.length) {
        alert('Aucun resultat a exporter avec les filtres actuels.');
        return;
    }
    // Leading BOM (utf-8-sig) so Excel reads accents correctly, matching folk_export.py.
    const blob = new Blob(['\uFEFF' + buildFolkCsv(members)], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `annuaire_cgp_${todayISO()}.csv`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
}

// ============================================================
// RENDERING - Groupements
// ============================================================
function renderGroupements() {
    const assocGrid = document.getElementById('associationsGrid');
    const associations = groupementsData.associations || [];
    // Live cabinet count per association (asso code = name lowercased)
    const assocCount = (code) => allMembers.filter(
        m => (m.associations || {})[code]?.member
    ).length;
    assocGrid.innerHTML = associations.map(a => {
        const code = a.name.toLowerCase();
        const live = assocCount(code);
        return `
        <div class="groupement-card clickable" onclick="filterByAssociation('${code}')">
            <div class="groupement-name">${escHtml(a.name)}</div>
            <span class="groupement-type groupement">${escHtml(a.full_name)}</span>
            <div class="groupement-desc">${escHtml(a.description)}</div>
            <div style="margin-top:4px;font-size:13px;color:var(--text-muted)">
                <strong>${live.toLocaleString('fr-FR')}</strong> cabinets en base
                ${a.members_approx ? ` (sur ~${a.members_approx} annoncés)` : ''}
            </div>
            <a href="${a.website}" target="_blank" rel="noopener" class="groupement-link" onclick="event.stopPropagation()">${escHtml(a.website)}</a>
        </div>`;
    }).join('');

    const grpGrid = document.getElementById('groupementsGrid');
    const groupements = groupementsData.groupements || [];

    // Live cabinet count per groupement (matches member.groupement OR
    // member.associations.ucgp.groupement)
    const grpCount = (name) => allMembers.filter(m => {
        const g = m.groupement
            || (m.associations && m.associations.ucgp && m.associations.ucgp.groupement)
            || '';
        return g === name;
    }).length;

    // Sort: tier order first (Incontournable > Excellent > ... ), then count desc
    const TIER_ORDER = {
        "Incontournable": 1, "Excellent": 2, "Forte notoriété": 3,
        "Pratique réputée": 4, "Pratique de qualité": 5, "User-listed": 6,
        "": 99,
    };
    const sorted = [...groupements].sort((a, b) => {
        const ta = TIER_ORDER[a.tier || ""] || 99;
        const tb = TIER_ORDER[b.tier || ""] || 99;
        if (ta !== tb) return ta - tb;
        return grpCount(b.name) - grpCount(a.name);
    });

    grpGrid.innerHTML = sorted.map(g => {
        const live = grpCount(g.name);
        const tierBadge = g.tier
            ? `<span class="groupement-tier" title="Classement Leaders League 2025">${escHtml(g.tier)}</span>`
            : '';
        const countBadge = live
            ? `<div style="margin-top:4px;font-size:13px;color:var(--text-muted)"><strong>${live}</strong> cabinet${live > 1 ? 's' : ''} dans la base</div>`
            : `<div style="margin-top:4px;font-size:12px;color:var(--text-muted);font-style:italic">aucun cabinet identifié dans la base</div>`;
        const clickable = live > 0;
        return `
        <div class="groupement-card ${clickable ? 'clickable' : ''}"
             ${clickable ? `onclick="filterByGroupement('${g.name.replace(/'/g, "\\'")}')"` : ''}>
            <div class="groupement-name">${escHtml(g.name)} ${tierBadge}</div>
            <span class="groupement-type ${g.type}">${g.type}</span>
            <div class="groupement-desc">${escHtml(g.description)}</div>
            ${countBadge}
            ${g.website ? `<a href="${g.website}" target="_blank" rel="noopener" class="groupement-link" onclick="event.stopPropagation()">${escHtml(g.website)}</a>` : ''}
        </div>`;
    }).join('');
}

// Click a groupement card -> switch to Annuaire and apply the filter.
function filterByGroupement(name) {
    const sel = document.getElementById('filterGroupement');
    if (sel) sel.value = name;
    _switchToDirectory();
}

// ============================================================
// RENDERING - Member Card (shared)
// ============================================================
function linkedinSearchUrl(name) {
    return `https://www.linkedin.com/search/results/people/?keywords=${encodeURIComponent(name || '')}&origin=GLOBAL_SEARCH_HEADER`;
}

function renderDirectorsHtml(m) {
    const directors = m.directors || [];
    if (!directors.length) return '';
    const html = directors.map(d => {
        const safeName = escHtml(d.name || '');
        const url = linkedinSearchUrl(d.name);
        const role = d.role && d.role.toLowerCase() !== 'adherent' ? d.role : '';
        const roleTxt = role ? ` <span class="director-role">- ${escHtml(role)}</span>` : '';
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
        .filter(a => !ASSOC_HIDDEN.has(a))
        .map(a => `<span class="badge badge-assoc" title="${ASSOC_TITLES[a] || ''}">${ASSOC_LABELS[a] || a.toUpperCase().replace(/_/g, ' ')}</span>`)
        .join('');
    const actBadges = (m.activities || [])
        .map(a => `<span class="badge badge-activity">${a}</span>`)
        .join('');
    // Groupement = parent network (Cyrus, Laplace, Magnacarta, Actualis, ...).
    // Stored as m.groupement (canonical) — also nested in m.associations.ucgp.groupement.
    const groupementName = m.groupement
        || (m.associations && m.associations.ucgp && m.associations.ucgp.groupement)
        || '';
    const groupementBadge = groupementName
        ? `<span class="badge badge-groupement" title="Groupement parent">${escHtml(groupementName)}</span>`
        : '';
    const newBadge = m.is_new ? '<span class="badge-new">NOUVEAU</span>' : '';
    const statusBadge = currentStatus
        ? `<span class="badge-status status-${currentStatus}">${STATUS_LABELS[currentStatus]}</span>`
        : '';

    // Website-derived data (Phase B enrichment): structurés badge + expertises chips
    const wd = m.website_data || {};
    const structuredBadge = wd.has_structured_products
        ? `<span class="badge badge-structured" title="${escHtml(wd.structured_evidence || 'Produits structures detectes sur le site')}">STRUCTURÉS</span>`
        : '';
    const expertiseBadges = (wd.expertises || [])
        .filter(e => e !== 'produits-structures')
        .map(e => `<span class="badge badge-expertise">${escHtml(EXPERTISE_LABELS[e] || e)}</span>`)
        .join('');

    // KPI line (CA / AUM / effectif): only render if at least one is present.
    // CA / RN: skip anything older than 2023 — pre-2023 figures are too stale
    // to inform prospection (data.gouv often serves 2017-2019 for radiated SAS).
    const fd = m.finances_data_gouv || {};
    // Treat 0 like missing — data.gouv returns ca:0 for empty declarations.
    const showCa = fd.ca_eur != null && fd.ca_eur > 0 && fd.year && fd.year >= 2023;
    // RN can legitimately be 0 or negative — only filter on year + non-null.
    const showRn = fd.resultat_net_eur != null && fd.year && fd.year >= 2023;
    const kpis = [];
    if (wd.aum_eur) {
        kpis.push(`<span class="kpi kpi-aum" title="Encours sous gestion (site web)">AUM: ${escHtml(formatEur(wd.aum_eur))}</span>`);
    }
    if (showCa) {
        kpis.push(`<span class="kpi kpi-ca" title="Chiffre d'affaires (data.gouv ${fd.year})">CA: ${escHtml(formatEur(fd.ca_eur))} (${fd.year})</span>`);
    }
    if (showRn) {
        const cls = fd.resultat_net_eur < 0 ? 'kpi-rn-neg' : 'kpi-rn-pos';
        kpis.push(`<span class="kpi ${cls}" title="Resultat net (data.gouv ${fd.year})">RN: ${escHtml(formatEur(fd.resultat_net_eur))}</span>`);
    }
    if (fd.effectif_label && fd.effectif_tranche && fd.effectif_tranche !== 'NN') {
        kpis.push(`<span class="kpi kpi-eff" title="Tranche d'effectif INSEE">${escHtml(fd.effectif_label)}</span>`);
    }
    const kpiLine = kpis.length ? `<div class="member-kpis">${kpis.join('')}</div>` : '';

    const teamLink = wd.team_url
        ? `<a class="meta-link" href="${escHtml(wd.team_url)}" target="_blank" rel="noopener" title="Page equipe">Equipe</a>`
        : '';
    const liCompanyLink = wd.linkedin_company_url
        ? `<a class="meta-link" href="${escHtml(wd.linkedin_company_url)}" target="_blank" rel="noopener" title="LinkedIn entreprise">LinkedIn</a>`
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
                    ${groupementBadge}
                    ${structuredBadge}
                    ${actBadges}
                    ${expertiseBadges}
                </div>
                <div class="member-meta">
                    ${location ? `<span itemprop="address" itemscope itemtype="https://schema.org/PostalAddress"><span itemprop="addressLocality">${escHtml(location)}</span></span>` : ''}
                    ${m.siren ? `<span>SIREN: ${escHtml(m.siren)}</span>` : ''}
                    ${m.orias_number ? `<span>ORIAS: ${escHtml(m.orias_number)}</span>` : ''}
                    ${m.creation_date ? `<span title="Date de creation (data.gouv)">Cree: ${escHtml(formatCreationDate(m.creation_date))}</span>` : ''}
                    ${m.orias_inscription_date ? `<span title="Premiere inscription au registre ORIAS">ORIAS depuis: ${escHtml(formatCreationDate(m.orias_inscription_date))}</span>` : ''}
                    ${teamLink}
                    ${liCompanyLink}
                </div>
                ${kpiLine}
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

// Format an ISO date "2014-04-10" → "avr. 2014" — short and FR-friendly.
function formatCreationDate(iso) {
    if (!iso || iso.length < 7) return iso;
    const [y, mo] = iso.split('-');
    const months = ['janv.','fevr.','mars','avr.','mai','juin','juil.','aout','sept.','oct.','nov.','dec.'];
    const m = parseInt(mo, 10);
    return (months[m - 1] || mo) + ' ' + y;
}

// Format euros: 1_234_567 → "1,2 M€"; 250_000_000 → "250 M€"; 1_500_000_000 → "1,5 Md€".
function formatEur(n) {
    if (n == null || isNaN(n)) return '';
    const abs = Math.abs(n);
    if (abs >= 1_000_000_000) {
        const v = n / 1_000_000_000;
        return (v >= 100 ? v.toFixed(0) : v.toFixed(1).replace('.', ',')) + ' Md€';
    }
    if (abs >= 1_000_000) {
        const v = n / 1_000_000;
        return (v >= 100 ? v.toFixed(0) : v.toFixed(1).replace('.', ',')) + ' M€';
    }
    if (abs >= 1_000) {
        return Math.round(n / 1_000).toLocaleString('fr-FR') + ' K€';
    }
    return Math.round(n).toLocaleString('fr-FR') + ' €';
}

const EXPERTISE_LABELS = {
    'gestion-privee': 'Gestion privée',
    'immobilier': 'Immobilier',
    'retraite': 'Retraite',
    'assurance-vie': 'Assurance vie',
    'fiscalite': 'Fiscalité',
    'succession': 'Succession',
    'credit': 'Crédit',
    'entreprise': 'Chef d\'entreprise',
    'international': 'International',
    'esg': 'ESG / ISR',
    'private-equity': 'Private Equity',
    'produits-structures': 'Structurés',
};

function escHtml(text) {
    const div = document.createElement('div');
    div.textContent = text || '';
    return div.innerHTML;
}

// ============================================================
// RENDERING - Actors (cartographie)
// ============================================================
function actorTier(actorId) {
    if (!cartoData?.priorites_prospection_cmf) return null;
    const p = cartoData.priorites_prospection_cmf;
    if (p.tier1_contact_immediat?.includes(actorId)) return 'tier1';
    if (p.tier2_moyen_terme?.includes(actorId)) return 'tier2';
    if (p.tier3_veille?.includes(actorId)) return 'tier3';
    return null;
}

function buildActorsIndex() {
    actorsIndex = [];

    // 1. Cartographie entities (high-level: associations, groupements, reseaux, FO, plateformes)
    if (cartoData) {
        for (const cat of cartoData.categories || []) {
            for (const e of cat.entites || []) {
                actorsIndex.push({
                    ...e,
                    _type: 'entity',
                    _key: `actor:${e.id}`,
                    category_id: cat.id,
                    category_label: cat.label,
                    category_color: cat.couleur,
                    tier: actorTier(e.id),
                });
            }
        }
        const pd = cartoData.plateformes_distribution;
        if (pd?.acteurs) {
            for (const a of pd.acteurs) {
                const id = (a.nom || '').toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_|_$/g, '');
                actorsIndex.push({
                    ...a,
                    id,
                    _type: 'entity',
                    _key: `actor:${id}`,
                    category_id: 'plateformes',
                    category_label: 'Plateformes de distribution',
                    category_color: '#0E7C8A',
                    tier: actorTier(id),
                });
            }
        }
    }

    // 2. CGP cabinets from members.json
    for (const m of allMembers) {
        actorsIndex.push({
            _type: 'cabinet',
            _key: m.id,  // no prefix - reuses existing cgp-status keys
            _member: m,
            id: m.id,
            nom: m.company_name,
            category_id: 'cabinets_cgp',
            category_label: 'Cabinets CGP',
            category_color: '#4a9eff',
            tier: null,
        });
    }

    document.getElementById('badgeActors').textContent = actorsIndex.length || '';
    if (cartoData?.meta?.description) {
        const intro = document.getElementById('actorsIntro');
        if (intro) intro.textContent = cartoData.meta.description + ' Inclut egalement les ' + allMembers.length.toLocaleString('fr-FR') + ' cabinets CGP scrapes.';
    }
}

function populateActorsFilters() {
    const sel = document.getElementById('actorsCategoryFilter');
    if (!sel) return;
    while (sel.options.length > 1) sel.remove(1);
    if (cartoData) {
        for (const cat of cartoData.categories || []) {
            const opt = document.createElement('option');
            opt.value = cat.id;
            opt.textContent = cat.label;
            sel.appendChild(opt);
        }
        if (cartoData.plateformes_distribution?.acteurs?.length) {
            const opt = document.createElement('option');
            opt.value = 'plateformes';
            opt.textContent = 'Plateformes de distribution';
            sel.appendChild(opt);
        }
    }
    if (allMembers.length) {
        const opt = document.createElement('option');
        opt.value = 'cabinets_cgp';
        opt.textContent = `Cabinets CGP (${allMembers.length.toLocaleString('fr-FR')})`;
        sel.appendChild(opt);
    }
}

function getFilteredActors() {
    const search = document.getElementById('actorsSearch')?.value.toLowerCase() || '';
    const catFilter = document.getElementById('actorsCategoryFilter')?.value || '';
    const tierFilter = document.getElementById('actorsTierFilter')?.value || '';
    const statusFilter = document.getElementById('actorsStatusFilter')?.value || '';
    const hideProcessed = document.getElementById('actorsHideProcessed')?.checked || false;

    return actorsIndex.filter(a => {
        const cur = getStatus(a._key);
        if (hideProcessed && cur) return false;
        if (statusFilter === '__none__' && cur) return false;
        if (statusFilter && statusFilter !== '__none__' && cur !== statusFilter) return false;
        if (catFilter && a.category_id !== catFilter) return false;
        if (tierFilter) {
            if (a._type === 'cabinet') return false; // cabinets have no tier
            if (a.tier !== tierFilter) return false;
        }
        if (search) {
            let haystack;
            if (a._type === 'cabinet') {
                const m = a._member;
                haystack = [
                    m.company_name, m.address?.city, m.address?.department_name,
                    m.email, m.phone, m.siren, m.orias_number,
                    ...(m.directors || []).map(d => d.name),
                    ...(m.activities || []),
                ].filter(Boolean).join(' ').toLowerCase();
            } else {
                haystack = [
                    a.nom, a.nom_complet, a.president, a.directeur_executif,
                    a.cabinet_president, a.actionnaire, a.groupe,
                    a.pertinence_cmf, a.contact_cle, a.notes, a.description,
                    ...(a.statuts || []), ...(a.membres_notables || []),
                ].filter(Boolean).join(' ').toLowerCase();
            }
            if (!haystack.includes(search)) return false;
        }
        return true;
    });
}

function renderActorsStats() {
    const el = document.getElementById('actorsStats');
    if (!el) return;
    let t1 = 0, t2 = 0, t3 = 0, cabinets = 0, contacted = 0, pending = 0, refused = 0, folk = 0;
    for (const a of actorsIndex) {
        if (a._type === 'cabinet') cabinets++;
        else if (a.tier === 'tier1') t1++;
        else if (a.tier === 'tier2') t2++;
        else if (a.tier === 'tier3') t3++;
        const s = getStatus(a._key);
        if (s === 'contacted') contacted++;
        else if (s === 'pending') pending++;
        else if (s === 'refused') refused++;
        if (isInFolk(a._key)) folk++;
    }
    el.innerHTML = `
        <div class="actor-stat-pill tier1">Tier 1 : <b>${t1}</b></div>
        <div class="actor-stat-pill tier2">Tier 2 : <b>${t2}</b></div>
        <div class="actor-stat-pill tier3">Tier 3 : <b>${t3}</b></div>
        <div class="actor-stat-pill cabinets">Cabinets CGP : <b>${cabinets.toLocaleString('fr-FR')}</b></div>
        <div class="actor-stat-pill pending">En cours : <b>${pending}</b></div>
        <div class="actor-stat-pill contacted">Contactes : <b>${contacted}</b></div>
        <div class="actor-stat-pill refused">Refus : <b>${refused}</b></div>
        <div class="actor-stat-pill folk">Folk : <b>${folk}</b></div>
    `;
}

function sortActors(list) {
    // entities first (tier1 > tier2 > tier3 > no tier), then cabinets alpha
    const tierOrder = { tier1: 0, tier2: 1, tier3: 2 };
    return [...list].sort((a, b) => {
        if (a._type !== b._type) return a._type === 'entity' ? -1 : 1;
        if (a._type === 'entity') {
            const ta = tierOrder[a.tier] ?? 9;
            const tb = tierOrder[b.tier] ?? 9;
            if (ta !== tb) return ta - tb;
        }
        return (a.nom || '').localeCompare(b.nom || '');
    });
}

function renderOneActor(a) {
    return a._type === 'cabinet' ? renderMemberCard(a._member) : renderActorCard(a);
}

function renderActors() {
    if (!actorsIndex.length) return;
    renderActorsStats();
    actorsDisplayOffset = 0;
    const filtered = sortActors(getFilteredActors());
    const grid = document.getElementById('actorsGrid');
    const countEl = document.getElementById('actorsCount');
    const loadBtn = document.getElementById('actorsLoadMoreBtn');

    if (countEl) countEl.textContent = `${filtered.length.toLocaleString('fr-FR')} acteur${filtered.length > 1 ? 's' : ''} sur ${actorsIndex.length.toLocaleString('fr-FR')}`;

    if (!filtered.length) {
        grid.innerHTML = '<div class="empty-state"><p>Aucun acteur correspondant aux filtres</p></div>';
        if (loadBtn) loadBtn.style.display = 'none';
        return;
    }

    const page = filtered.slice(0, ACTORS_PAGE_SIZE);
    grid.innerHTML = page.map(renderOneActor).join('');
    actorsDisplayOffset = ACTORS_PAGE_SIZE;
    if (loadBtn) loadBtn.style.display = filtered.length > ACTORS_PAGE_SIZE ? 'block' : 'none';
    // Store for loadMoreActors
    renderActors._filtered = filtered;
}

function loadMoreActors() {
    const filtered = renderActors._filtered || sortActors(getFilteredActors());
    const grid = document.getElementById('actorsGrid');
    const loadBtn = document.getElementById('actorsLoadMoreBtn');

    const page = filtered.slice(actorsDisplayOffset, actorsDisplayOffset + ACTORS_PAGE_SIZE);
    grid.insertAdjacentHTML('beforeend', page.map(renderOneActor).join(''));
    actorsDisplayOffset += ACTORS_PAGE_SIZE;
    if (loadBtn) loadBtn.style.display = actorsDisplayOffset < filtered.length ? 'block' : 'none';
}

function fmtMds(v) {
    if (v == null) return null;
    if (v >= 1) return `${v} Md€`;
    return `${(v * 1000).toFixed(0)} M€`;
}

function renderActorCard(a) {
    const cur = getStatus(a._key);
    const folk = isInFolk(a._key);
    const tierLabel = { tier1: 'TIER 1', tier2: 'TIER 2', tier3: 'TIER 3' }[a.tier] || '';
    const statusLabel = cur ? STATUS_LABELS[cur] : '';

    const facts = [];
    if (a.fondation) facts.push(`<span><b>Fondation</b> ${a.fondation}</span>`);
    if (a.cabinets) facts.push(`<span><b>${a.cabinets}</b> cabinets</span>`);
    if (a.conseillers) facts.push(`<span><b>${a.conseillers}</b> conseillers</span>`);
    if (a.cabinets_membres) facts.push(`<span><b>${a.cabinets_membres}</b> cabinets membres</span>`);
    if (a.groupements_membres) facts.push(`<span><b>${a.groupements_membres}</b> groupements</span>`);
    if (a.membres_benevoles) facts.push(`<span><b>${a.membres_benevoles}</b> benevoles</span>`);
    if (a.encours_mds != null) facts.push(`<span><b>${fmtMds(a.encours_mds)}</b> encours</span>`);
    if (a.ca_mds != null) facts.push(`<span><b>${fmtMds(a.ca_mds)}</b> CA</span>`);
    if (a.collecte_annuelle_mds != null) facts.push(`<span><b>${fmtMds(a.collecte_annuelle_mds)}</b> collecte/an</span>`);
    if (a.part_marche_ca_cif) facts.push(`<span><b>${a.part_marche_ca_cif}</b> CA CIF</span>`);
    if (a.part_marche_cabinets) facts.push(`<span><b>${a.part_marche_cabinets}</b> cabinets</span>`);

    const people = [];
    if (a.president) people.push(`Pres. ${escHtml(a.president)}${a.cabinet_president ? ` (${escHtml(a.cabinet_president)})` : ''}`);
    if (a.directeur_executif) people.push(`Dir. ${escHtml(a.directeur_executif)}`);
    if (a.actionnaire) people.push(`Actionnaire : ${escHtml(a.actionnaire)}`);
    if (a.groupe) people.push(`Groupe : ${escHtml(a.groupe)}`);

    const statuts = (a.statuts || []).map(s => `<span class="badge badge-activity">${escHtml(s)}</span>`).join('');
    const membres = a.membres_notables?.length
        ? `<div class="actor-members"><b>Membres notables :</b> ${a.membres_notables.map(escHtml).join(', ')}</div>`
        : '';

    const linkedinUrl = a.president
        ? linkedinSearchUrl(a.president)
        : null;

    const statusOptions = Object.entries(STATUS_LABELS)
        .map(([val, label]) => `<option value="${val}" ${cur === val ? 'selected' : ''}>${label}</option>`)
        .join('');

    const cardClasses = [
        'actor-card',
        cur ? `has-status status-${cur}` : '',
        folk ? 'in-folk' : '',
    ].filter(Boolean).join(' ');

    const colorBar = a.category_color
        ? `style="border-top: 3px solid ${a.category_color}"`
        : '';

    return `
        <div class="${cardClasses}" ${colorBar} itemscope itemtype="https://schema.org/Organization" data-actor-id="${a._key}">
            <div class="actor-header">
                <div class="actor-title-row">
                    <span class="actor-name" itemprop="name">${escHtml(a.nom)}</span>
                    ${tierLabel ? `<span class="tier-badge ${a.tier}">${tierLabel}</span>` : ''}
                    ${statusLabel ? `<span class="badge-status status-${cur}">${escHtml(statusLabel)}</span>` : ''}
                </div>
                ${a.nom_complet && a.nom_complet !== a.nom ? `<div class="actor-fullname">${escHtml(a.nom_complet)}</div>` : ''}
                <div class="actor-category" style="${a.category_color ? `color: ${a.category_color}` : ''}">${escHtml(a.category_label)}</div>
            </div>

            ${people.length ? `<div class="actor-people">${people.map(p => `<div>${p}</div>`).join('')}</div>` : ''}

            ${facts.length ? `<div class="actor-facts">${facts.join('')}</div>` : ''}

            ${statuts ? `<div class="actor-statuts">${statuts}</div>` : ''}

            ${a.pertinence_cmf ? `<div class="actor-pertinence"><b>Pertinence CMF :</b> ${escHtml(a.pertinence_cmf)}</div>` : ''}

            ${a.contact_cle ? `<div class="actor-contact-cle"><b>Contact cle :</b> ${escHtml(a.contact_cle)}</div>` : ''}

            ${membres}

            ${a.notes ? `<div class="actor-notes">${escHtml(a.notes)}</div>` : ''}

            <div class="actor-footer">
                <div class="actor-links">
                    ${a.site ? `<a href="${a.site}" target="_blank" rel="noopener" itemprop="url">Site</a>` : ''}
                    ${a.site_event ? `<a href="${a.site_event}" target="_blank" rel="noopener">Evenement</a>` : ''}
                    ${linkedinUrl ? `<a href="${linkedinUrl}" target="_blank" rel="noopener" class="director-link"><span class="linkedin-ico">in</span>${escHtml(a.president)}</a>` : ''}
                </div>
                <div class="actor-actions">
                    <select class="status-select status-select-${cur || 'none'}"
                            onchange="setStatus('${a._key}', this.value)"
                            title="Statut de prospection">
                        ${statusOptions}
                    </select>
                    <label class="folk-toggle" title="Marquer comme ajoute dans Folk">
                        <input type="checkbox" ${folk ? 'checked' : ''} onchange="toggleFolk('${a._key}')">
                        <span class="toggle-switch folk-switch"></span>
                        <span>Folk</span>
                    </label>
                </div>
            </div>
        </div>
    `;
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
        case 'actors': renderActors(); break;
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
['searchInput', 'filterAssociation', 'filterDepartment', 'filterActivity', 'filterStatus', 'filterCreation', 'filterGroupement', 'filterCa', 'filterAum', 'filterStructured', 'filterExpertise'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.addEventListener(el.type === 'text' ? 'input' : 'change', renderDirectory);
});
document.getElementById('filterHideProcessed')?.addEventListener('change', renderDirectory);

// Actors filters
['actorsSearch', 'actorsCategoryFilter', 'actorsTierFilter', 'actorsStatusFilter'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.addEventListener(el.type === 'text' ? 'input' : 'change', renderActors);
});
document.getElementById('actorsHideProcessed')?.addEventListener('change', renderActors);

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
    try {
        const gistIdInput = document.getElementById('syncGistId');
        const tokenInput = document.getElementById('syncToken');
        if (!gistIdInput || !tokenInput) {
            alert('Erreur: champs du formulaire introuvables (app.js pas a jour ?)');
            return;
        }
        const gistId = gistIdInput.value.trim();
        const token = tokenInput.value.trim();

        // Test localStorage availability
        try {
            localStorage.setItem('__test', '1');
            localStorage.removeItem('__test');
        } catch (e) {
            alert('Erreur: localStorage indisponible sur cet appareil.\n\n' +
                  'Causes possibles:\n' +
                  '- Mode navigation privee\n' +
                  '- Stockage desactive dans les parametres Safari\n' +
                  '- Trop peu d\'espace libre\n\n' +
                  'Detail: ' + e.message);
            return;
        }

        setSyncConfig({ gistId, token });

        // Verify write
        const check = getSyncConfig();
        if (check.gistId !== gistId || check.token !== token) {
            alert('Erreur: ecriture localStorage echouee (les valeurs n\'ont pas ete persistees).');
            return;
        }

        closeModal('syncModal');

        if (gistId) {
            try { await cloudLoad(); } catch (e) { console.warn('cloudLoad err', e); }
            if (token) {
                try { await cloudSave(); } catch (e) { console.warn('cloudSave err', e); }
            }
            const s = document.getElementById('syncStatus')?.textContent || '';
            if (/err/i.test(s)) {
                alert('Config enregistree mais le Gist GitHub a renvoye une erreur.\n\n' +
                      'Verifie:\n' +
                      '- Que le Gist ID est correct (sans le nom d\'utilisateur, juste la chaine apres le /)\n' +
                      '- Que le token a bien le scope "gist"\n' +
                      '- Que le Gist existe et est accessible avec ce token\n\n' +
                      'Les identifiants sont conserves, tu peux reessayer depuis le bouton Sync.');
            }
        } else {
            setSyncStatus('offline');
        }
    } catch (e) {
        console.error('saveSyncSettings failed', e);
        alert('Erreur inattendue: ' + e.message);
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
window.loadMoreActors = loadMoreActors;
window.setupNotifications = setupNotifications;
window.closeModal = closeModal;
window.openSyncSettings = openSyncSettings;
window.saveSyncSettings = saveSyncSettings;
window.clearSyncSettings = clearSyncSettings;

// ============================================================
// FOLK CRM INTEGRATION (API key in localStorage, never on GitHub)
// ============================================================
function getFolkApiKey() { return localStorage.getItem(FOLK_API_KEY) || ''; }
function setFolkApiKey(key) { localStorage.setItem(FOLK_API_KEY, key); }

function openFolkSettings() {
    document.getElementById('folkApiKeyInput').value = getFolkApiKey();
    document.getElementById('folkModal').style.display = 'flex';
}

function saveFolkSettings() {
    const key = document.getElementById('folkApiKeyInput').value.trim();
    setFolkApiKey(key);
    closeModal('folkModal');
    const indicator = document.getElementById('folkSyncStatus');
    if (indicator) indicator.textContent = key ? 'Folk OK' : 'Folk Off';
    if (indicator) indicator.style.color = key ? 'var(--accent-green)' : 'var(--text-muted)';
    // NB: do NOT live-test against api.folk.app from the browser — it is
    // CORS-blocked and would falsely show "Folk Err". The key is used for the
    // server-side push (folk-push workflow); in-app, Folk works via CSV export.
}

function clearFolkSettings() {
    if (!confirm('Effacer la cle API Folk ?')) return;
    localStorage.removeItem(FOLK_API_KEY);
    closeModal('folkModal');
    const indicator = document.getElementById('folkSyncStatus');
    if (indicator) { indicator.textContent = 'Folk Off'; indicator.style.color = 'var(--text-muted)'; }
}

async function testFolkConnection(key) {
    try {
        const r = await fetch(FOLK_API_BASE + '/groups', {
            headers: { 'Authorization': 'Bearer ' + key }
        });
        const indicator = document.getElementById('folkSyncStatus');
        if (r.ok) {
            if (indicator) { indicator.textContent = 'Folk OK'; indicator.style.color = 'var(--accent-green)'; }
        } else {
            if (indicator) { indicator.textContent = 'Folk Err'; indicator.style.color = 'var(--accent-red)'; }
            console.warn('Folk API test failed:', r.status);
        }
    } catch (e) {
        console.warn('Folk API test error:', e);
    }
}

async function folkPushContact(member) {
    const key = getFolkApiKey();
    if (!key || !member) return;
    try {
        const payload = { name: member.company_name || '' };
        if (member.phone) payload.phones = [member.phone];
        if (member.email) payload.emails = [member.email];
        if (member.website) {
            const url = member.website.startsWith('http') ? member.website : 'https://' + member.website;
            payload.urls = [url];
        }
        if (member.address) {
            const a = member.address;
            payload.addresses = [{
                city: a.city || '',
                postalCode: a.postal_code || '',
                country: 'France',
            }];
        }
        const r = await fetch(FOLK_API_BASE + '/companies', {
            method: 'POST',
            headers: { 'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        if (r.ok) {
            console.info(`[Folk] Pushed ${member.company_name}`);
            return true;
        } else if (r.status === 409) {
            console.info(`[Folk] ${member.company_name} already exists`);
            return true;
        } else {
            const err = await r.text().catch(() => '');
            console.warn(`[Folk] Push failed for ${member.company_name}: HTTP ${r.status}`, err);
            return false;
        }
    } catch (e) {
        console.warn(`[Folk] Push error for ${member.company_name}:`, e);
        return false;
    }
}

// Folk's API blocks browser calls (CORS), so the reliable, self-service path is
// a CSV export of the Folk-marked cabinets, ready to import into Folk.
function folkPushAll() {
    const folkMap = getFolkMap();
    const ids = new Set(Object.keys(folkMap));
    if (!ids.size) {
        alert('Aucun cabinet marque Folk.\n\nCochez le toggle "Folk" sur les fiches a exporter, puis recliquez ici.');
        return;
    }
    const marked = allMembers.filter(m => ids.has(m.id));
    if (!marked.length) {
        alert('Les cabinets marques Folk ne sont plus dans la base.');
        return;
    }
    const blob = new Blob(['﻿' + buildFolkCsv(marked)], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `folk_import_${todayISO()}.csv`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
    alert(`${marked.length} cabinet(s) Folk exporte(s) en CSV.\n\nImportez ce fichier dans Folk : Folk > Import > CSV.`);
}

window.openFolkSettings = openFolkSettings;
window.saveFolkSettings = saveFolkSettings;
window.clearFolkSettings = clearFolkSettings;
window.folkPushAll = folkPushAll;

// ============================================================
// INIT
// ============================================================
async function init() {
    // Show app version (helps verify whether the browser is on fresh code)
    const lastUpdate = document.getElementById('lastUpdate');
    if (lastUpdate) lastUpdate.title = `App v${APP_VERSION}`;
    console.info(`CGP Monitor v${APP_VERSION} loaded`);
    migrateLegacyContacted();
    await loadData();
    await cloudLoad();

    // If the user just clicked MAJ, show the click time (not the data file date)
    // and give a brief green confirmation on the button.
    try {
        if (sessionStorage.getItem('cgpMajClicked')) {
            sessionStorage.removeItem('cgpMajClicked');
            const now = new Date();
            const el = document.getElementById('lastUpdate');
            if (el) el.textContent =
                `Mis a jour: ${now.toLocaleDateString('fr-FR')} ${now.toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' })}`;
            const btn = document.querySelector('[onclick="refreshData()"]');
            if (btn) {
                btn.textContent = '✓ MAJ';
                btn.style.color = 'var(--accent-green)';
                btn.style.borderColor = 'var(--accent-green)';
                setTimeout(() => {
                    btn.textContent = '↻ MAJ';
                    btn.style.color = '#4a9eff';
                    btn.style.borderColor = '#4a9eff';
                }, 1800);
            }
        }
    } catch (e) {}
}

async function refreshData() {
    const btn = document.querySelector('[onclick="refreshData()"]');
    if (btn) { btn.textContent = '⏳'; btn.disabled = true; }
    // Wipe any cached assets, then hard-reload the WHOLE page (HTML+CSS+JS)
    // with a cache-busting URL so the browser can never serve a stale version.
    try {
        if ('serviceWorker' in navigator) {
            const regs = await navigator.serviceWorker.getRegistrations();
            await Promise.all(regs.map(r => r.unregister()));
        }
        if ('caches' in window) {
            const keys = await caches.keys();
            await Promise.all(keys.map(k => caches.delete(k)));
        }
    } catch (e) { console.warn('cache clear failed', e); }
    // Remember that the user clicked MAJ so we can show the click time after reload
    try { sessionStorage.setItem('cgpMajClicked', '1'); } catch (e) {}
    // Reload with a fresh URL (?_=timestamp) -> bypasses the browser HTTP cache
    const base = location.origin + location.pathname;
    location.replace(base + '?_=' + Date.now());
}
window.refreshData = refreshData;

function toggleFilters(panelId, btn) {
    const panel = document.getElementById(panelId || 'advancedFilters');
    if (!panel) return;
    const isHidden = panel.style.display === 'none';
    panel.style.display = isHidden ? 'flex' : 'none';
    if (btn) {
        btn.classList.toggle('open', isHidden);
        btn.textContent = isHidden ? '− Filtres' : '+ Filtres';
    }
}
window.toggleFilters = toggleFilters;

init();
