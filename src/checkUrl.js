const fetch = require('node-fetch');

async function checkUrl(url, { token } = {}) {
  const headers = {};
  if (token) headers.Authorization = `Bearer ${token}`;
  const res = await fetch(url, { headers });
  return { ok: res.ok, status: res.status };
}

module.exports = { checkUrl };
