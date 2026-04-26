/**
 * Options Detective - Frontend Application
 * Connects to FastAPI backend
 */

const API_BASE = window.location.origin;

// State
let currentScanResults = [];
let positions = [];

// Initialize app immediately (DOM already loaded as script is at end of body)
initEventListeners();
loadPositions();
loadAccountSummary();

function initEventListeners() {
    // Scan form
    document.getElementById('scanForm').addEventListener('submit', handleScan);
    
    // Auto-trade switch
    const autoTradeCheckbox = document.getElementById('autoTradeEnabled');
    if (autoTradeCheckbox) {
        // Load saved preference
        const saved = localStorage.getItem('autoTradeEnabled');
        if (saved !== null) {
            autoTradeCheckbox.checked = saved === 'true';
        }
        updateAutoTradeStatus();
        
        autoTradeCheckbox.addEventListener('change', () => {
            localStorage.setItem('autoTradeEnabled', autoTradeCheckbox.checked);
            updateAutoTradeStatus();
        });
    }
    
    function updateAutoTradeStatus() {
        const statusEl = document.getElementById('autoTradeStatus');
        if (statusEl && autoTradeCheckbox) {
            statusEl.innerHTML = autoTradeCheckbox.checked ? 
                '<span class="text-success">Enabled - will auto-trade top strategy</span>' : 
                'Disabled';
        }
    }
    
    // Refresh button
    document.getElementById('refreshBtn').addEventListener('click', () => {
        loadRecentStrategies();
        loadPositions();
    });
    
    // Min POP slider
    document.getElementById('minPop').addEventListener('input', (e) => {
        document.getElementById('minPopValue').textContent = e.target.value;
    });
    
    // Trade modal confirmation
    document.getElementById('confirmTrade').addEventListener('click', executePaperTrade);
}

// API Calls

// UI Utilities
function showLoading(show) {
    const overlay = document.getElementById('loadingOverlay');
    if (overlay) {
        overlay.style.display = show ? 'flex' : 'none';
    }
}

function showToast(title, message, type = 'info') {
    // Simple alert fallback (for demo)
    const icon = type === 'success' ? '✓' : type === 'danger' ? '✗' : 'ℹ';
    alert(`${icon} ${title}: ${message}`);
}


// Auto-Trading
function isAutoTradeEnabled() {
    const checkbox = document.getElementById('autoTradeEnabled');
    return checkbox ? checkbox.checked : false;
}

async function executeAutoTrade(strategy) {
    // Strategy should have an 'id' field (from saved scan)
    if (!strategy.id) {
        console.error('Cannot auto-trade: strategy has no ID');
        return false;
    }
    
    try {
        const response = await fetch(`${API_BASE}/api/paper-trade`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                scan_id: strategy.id,
                quantity: 1  // default quantity
            })
        });
        
        if (!response.ok) throw new Error('Auto-trade failed');
        
        const result = await response.json();
        showToast('Auto-Trade Executed', `${result.symbol} ${result.strategy} - 1 contract(s)`, 'success');
        loadPositions();
        loadAccountSummary();
        return true;
    } catch (error) {
        console.error('Auto-trade error:', error);
        showToast('Auto-Trade Failed', error.message, 'danger');
        return false;
    }
}

// Modify handleScan to include auto-trade
async function handleScan(e) {
    e.preventDefault();
    
    const symbol = document.getElementById('symbol').value.toUpperCase();
    const dte = parseInt(document.getElementById('dte').value);
    
    showLoading(true);
    
    try {
        const response = await fetch(`${API_BASE}/api/scan`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                symbol,
                expiration_days: dte,
                limit: 20
            })
        });
        
        if (!response.ok) throw new Error('Scan failed');
        
        const data = await response.json();
        currentScanResults = data.strategies;
        renderResults(currentScanResults);
        
        showToast('Scan Complete', `Found ${data.count} strategies for ${symbol}`, 'success');
        document.getElementById('lastScan').textContent = new Date().toLocaleTimeString();
        
        // Auto-trade if enabled and we have results
        if (isAutoTradeEnabled() && data.strategies.length > 0) {
            const topStrategy = data.strategies[0];  // Already ranked
            showToast('Auto-Trading', `Executing top strategy: ${topStrategy.strategy}...`, 'info');
            await executeAutoTrade(topStrategy);
        }
        
    } catch (error) {
        console.error('Scan error:', error);
        showToast('Error', 'Failed to scan. Make sure backend is running.', 'danger');
    } finally {
        showLoading(false);
    }
}

async function loadRecentStrategies() {
    try {
        const response = await fetch(`${API_BASE}/api/strategies?limit=10`);
        if (!response.ok) return;
        
        const data = await response.json();
        currentScanResults = data.map((s, i) => ({
            rank: i + 1,
            strategy: s.strategy_type,
            symbol: s.symbol,
            probability: s.probability,
            max_profit: 0,  // Not stored in summary
            max_loss: 0,
            net_credit: s.net_credit,
            score: s.score,
            expiration: 'Recent',
            days_to_expiry: 0,
            id: s.id
        }));
        renderResults(currentScanResults);
    } catch (error) {
        console.error('Failed to load strategies:', error);
    }
}

async function loadPositions() {
    try {
        const response = await fetch(`${API_BASE}/api/positions`);
        if (!response.ok) return;
        
        positions = await response.json();
        renderPositions(positions);
        
        document.getElementById('positionsCount').textContent = positions.length;
        document.getElementById('openPositions').textContent = positions.length;
        
    } catch (error) {
        console.error('Failed to load positions:', error);
    }
}

async function loadAccountSummary() {
    try {
        const response = await fetch(`${API_BASE}/api/account`);
        if (!response.ok) return;
        
        const data = await response.json();
        const totalPnl = data.total_pnl;
        
        document.getElementById('balance').textContent = 
            (data.initial_balance + totalPnl).toLocaleString('en-US', {minimumFractionDigits: 2});
        document.getElementById('totalPnl').textContent = 
            `+$${totalPnl.toLocaleString('en-US', {minimumFractionDigits: 2})}`;
        document.getElementById('totalPnl').className = 
            totalPnl >= 0 ? 'h4 mb-0 profit-positive' : 'h4 mb-0 profit-negative';
            
        // Win rate would need closed positions history
        document.getElementById('winRate').textContent = '--';
        
    } catch (error) {
        console.error('Failed to load account:', error);
    }
}

async function executePaperTrade() {
    const positionId = parseInt(document.getElementById('confirmTrade').dataset.positionId);
    const quantity = parseInt(document.getElementById('tradeQuantity').value);
    
    try {
        const response = await fetch(`${API_BASE}/api/paper-trade`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                scan_id: positionId,
                quantity: quantity
            })
        });
        
        if (!response.ok) throw new Error('Trade failed');
        
        const result = await response.json();
        bootstrap.Modal.getInstance(document.getElementById('tradeModal')).hide();
        
        showToast('Trade Executed', `${result.symbol} ${result.strategy} - ${quantity} contract(s)`, 'success');
        loadPositions();
        loadAccountSummary();
        
    } catch (error) {
        showToast('Error', 'Failed to execute paper trade', 'danger');
    }
}

// Rendering
function renderResults(strategies) {
    const tbody = document.getElementById('resultsTable');
    
    if (strategies.length === 0) {
        tbody.innerHTML = `
            <tr>
                <td colspan="9" class="text-center text-muted py-4">
                    <i class="bi bi-inbox display-4 d-block mb-3"></i>
                    No strategies meet your criteria
                </td>
            </tr>
        `;
        return;
    }
    
    const minPop = parseInt(document.getElementById('minPop').value);
    
    tbody.innerHTML = strategies.map(s => {
        const popColor = s.probability >= 70 ? 'badge-high' : 
                        s.probability >= 50 ? 'badge-medium' : 'badge-low';
        const profitClass = s.net_credit >= 0 ? 'profit-positive' : 'profit-negative';
        const hasData = s.max_profit !== 0;
        
        return `
            <tr class="strategy-row" data-id="${s.id || ''}">
                <td><span class="badge bg-secondary">#${s.rank}</span></td>
                <td>
                    <div class="fw-bold">${s.strategy}</div>
                    <small class="text-muted">${s.symbol} ${s.expiration}</small>
                </td>
                <td class="text-center">
                    <span class="badge ${popColor}">${s.probability}% POP</span>
                </td>
                <td class="text-end ${profitClass}">
                    ${s.net_credit >= 0 ? '+' : ''}$${s.net_credit.toFixed(2)}
                </td>
                <td class="text-end profit-positive">
                    +$${hasData ? s.max_profit.toFixed(2) : '--'}
                </td>
                <td class="text-end profit-negative">
                    -$${hasData ? Math.abs(s.max_loss).toFixed(2) : '--'}
                </td>
                <td class="text-center">
                    <div class="progress" style="height: 6px; width: 60px; margin: 0 auto;">
                        <div class="progress-bar bg-primary" style="width: ${Math.min(s.score, 100)}%"></div>
                    </div>
                    <small class="text-muted">${s.score.toFixed(1)}</small>
                </td>
                <td>
                    <button class="btn btn-sm btn-success" onclick="openTradeModal(${s.id})" 
                            ${!s.id ? 'disabled' : ''}>
                        <i class="bi bi-play-fill"></i>
                    </button>
                </td>
            </tr>
        `;
    }).join('');
}

function renderPositions(positions) {
    const container = document.getElementById('positionsList');
    
    if (positions.length === 0) {
        container.innerHTML = `
            <p class="text-muted text-center small">
                <i class="bi bi-basket"></i><br>
                No open positions. Execute a trade to see it here.
            </p>
        `;
        return;
    }
    
    container.innerHTML = positions.map(p => {
        const pnlClass = p.unrealized_pnl >= 0 ? 'profit-positive' : 'profit-negative';
        return `
            <div class="card bg-dark border-secondary mb-2 p-3">
                <div class="d-flex justify-content-between align-items-center">
                    <div>
                        <div class="fw-bold">${p.symbol} ${p.strategy}</div>
                        <small class="text-muted">Entry: $${p.entry_price.toFixed(2)}</small>
                    </div>
                    <div class="text-end">
                        <div class="${pnlClass}">$${p.unrealized_pnl.toFixed(2)}</div>
                        <small class="text-muted">${p.days_open}d open</small>
                    </div>
                </div>
            </div>
        `;
    }).join('');
}

// Modal
function openTradeModal(strategyId) {
    const strategy = currentScanResults.find(s => s.id === strategyId);
    if (!strategy) return;
    
    document.getElementById('tradeDetails').innerHTML = `
        <div class="alert alert-info py-2">
            <i class="bi bi-info-circle"></i>
            ${strategy.strategy} on ${strategy.symbol}<br>
            Credit: $${strategy.net_credit.toFixed(2)}/contract<br>
            POP: ${strategy.probability}%
        </div>
    `;
    
    document.getElementById('confirmTrade').dataset.positionId = strategyId;
    new bootstrap.Modal(document.getElementById('tradeModal')).show();
}

// Utilities
function showLoading(show) {
    const overlay = document.getElementById('loadingOverlay');
    overlay.classList.toggle('d-none', !show);
}

function showToast(title, message, type = 'info') {
    const toastEl = document.getElementById('liveToast');
    const toastTitle = document.getElementById('toastTitle');
    const toastMessage = document.getElementById('toastMessage');
    const toastIcon = document.getElementById('toastIcon');
    
    toastTitle.textContent = title;
    toastMessage.textContent = message;
    
    // Icon color
    const icons = { success: 'bi-check-circle', danger: 'bi-exclamation-circle', info: 'bi-info-circle' };
    toastIcon.className = `bi ${icons[type]} me-2`;
    
    const toast = new bootstrap.Toast(toastEl);
    toast.show();
}

// Initial load
loadRecentStrategies();
