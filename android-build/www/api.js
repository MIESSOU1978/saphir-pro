/* api.js — Intercepts fetch() calls and routes them to localStorage.
   Include this script BEFORE the main HTML script.
   Usage in HTML: <script type="module" src="api.js"></script> */
import { initDB, saveEleve, updateEleve, listEleves, getEleve, deleteEleve, deleteMultipleEleves, clearAll } from './db.js';

let dbReady = false;

export async function bootAPI() {
  await initDB();
  dbReady = true;

  const originalFetch = window.fetch;
  window.fetch = async function(url, opts = {}) {
    const method = (opts.method || 'GET').toUpperCase();
    const path = typeof url === 'string' ? url : url.url || '';

    if (!path.startsWith('/api/')) {
      return originalFetch(url, opts);
    }

    await waitForDB();

    try {
      if (path === '/api/eleves' && method === 'GET') {
        const data = await listEleves();
        return jsonResponse(data);
      }

      if (path === '/api/eleves' && method === 'POST') {
        const body = JSON.parse(opts.body || '{}');
        const result = await saveEleve(
          body.nom || '', body.matricule || '', body.classe || '',
          body.etablissement || '', body.annee || '',
          body.total || 0, body.mo || 0, body.mention || '',
          body.matieres || {}
        );
        return jsonResponse(result);
      }

      if (path === '/api/eleves/clear' && method === 'DELETE') {
        const count = await clearAll();
        return jsonResponse({ ok: true, cleared: count });
      }

      if (path === '/api/eleves/delete-multiple' && method === 'POST') {
        const body = JSON.parse(opts.body || '{}');
        const ids = body.ids || [];
        const count = await deleteMultipleEleves(ids);
        return jsonResponse({ ok: true, deleted: count });
      }

      const idMatch = path.match(/^\/api\/eleves\/(\d+)$/);
      if (idMatch) {
        const id = parseInt(idMatch[1]);
        if (method === 'GET') {
          const data = await getEleve(id);
          if (!data) return jsonError('Not found', 404);
          return jsonResponse(data);
        }
        if (method === 'DELETE') {
          await deleteEleve(id);
          return jsonResponse({ ok: true });
        }
      }

      if (method === 'PUT') {
        const putMatch = path.match(/^\/api\/eleves\/(\d+)$/);
        if (putMatch) {
          const id = parseInt(putMatch[1]);
          const body = JSON.parse(opts.body || '{}');
          const result = await updateEleve(
            id,
            body.nom || '', body.matricule || '', body.classe || '',
            body.etablissement || '', body.annee || '',
            body.total || 0, body.mo || 0, body.mention || '',
            body.matieres || {}
          );
          if (!result) return jsonError('Not found', 404);
          return jsonResponse(result);
        }
      }

      const dupMatch = path.match(/^\/api\/eleves\/(\d+)$/);
      if (dupMatch && method === 'POST' && path.endsWith('/duplicate')) {
        const srcId = parseInt(dupMatch[1]);
        const src = await getEleve(srcId);
        if (!src) return jsonError('Not found', 404);
        const result = await saveEleve(
          src.nom || '', src.matricule || '', src.classe || '',
          src.etablissement || '', src.annee || '',
          src.total || 0, src.mo || 0, src.mention || '',
          src.matieres || {}
        );
        return jsonResponse(result, 201);
      }

      return jsonError('Not found', 404);
    } catch (err) {
      console.error('[API Error]', err);
      return jsonError(err.message || 'Server error', 500);
    }
  };

  console.log('[SAPHIR Pro] API intercepteur actif (localStorage)');
}

function waitForDB() {
  if (dbReady) return Promise.resolve();
  return new Promise(resolve => {
    const check = setInterval(() => {
      if (dbReady) { clearInterval(check); resolve(); }
    }, 50);
  });
}

function jsonResponse(data, status) {
  return new Response(JSON.stringify(data), {
    status: status || 200,
    headers: { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' }
  });
}

function jsonError(msg, status) {
  return new Response(JSON.stringify({ error: msg }), {
    status,
    headers: { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' }
  });
}

bootAPI().then(() => {
  if (typeof window.loadHistory === 'function') {
    setTimeout(() => window.loadHistory(), 50);
  }
}).catch(err => {
  console.error('[SAPHIR Pro] Échec init API:', err);
});
