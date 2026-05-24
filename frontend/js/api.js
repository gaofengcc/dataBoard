/**
 * DataBoard — REST API 调用封装
 */

const API_BASE = '/api';

async function fetchConfig() {
    const res = await fetch(`${API_BASE}/config`);
    if (!res.ok) throw new Error(`Config fetch failed: ${res.status}`);
    return res.json();
}

async function fetchData() {
    const res = await fetch(`${API_BASE}/data`);
    if (!res.ok) throw new Error(`Data fetch failed: ${res.status}`);
    return res.json();
}

async function fetchHealth() {
    const res = await fetch(`${API_BASE}/health`);
    if (!res.ok) throw new Error(`Health fetch failed: ${res.status}`);
    return res.json();
}
