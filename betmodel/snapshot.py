"""Read-only snapshot of the latest run: one self-contained HTML page (same look as the app),
saved to snapshot/index.html and, if configured, published to GitHub Pages so it can be opened
from any phone with the PC switched off."""
from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request
from pathlib import Path

import pandas as pd
from htmltools import HTML, tags

from . import app as A, db, engine, slate, theme as T

OUT = Path(__file__).resolve().parent.parent / "snapshot" / "index.html"
# styles: theme.T.CSS (the same stylesheet as the app, including the phone-page app bar)

JS = """
function tab(id, b){ document.querySelectorAll('.sec').forEach(s=>s.classList.remove('on'));
  document.getElementById(id).classList.add('on'); document.querySelectorAll('.tabs button').forEach(x=>x.classList.remove('on'));
  b.classList.add('on'); document.body.classList.remove('show-detail'); window.scrollTo(0,0); }
function show(i){ document.querySelectorAll('.dsnap').forEach(e=>e.style.display='none');
  var d=document.getElementById('d'+i); if(d) d.style.display='block';
  document.querySelectorAll('.gcard').forEach(e=>e.classList.remove('active'));
  var c=document.getElementById('c'+i); if(c) c.classList.add('active');
  document.body.classList.add('show-detail'); var gd=document.getElementById('game_detail'); if(gd) gd.scrollTop=0; }
function openGame(i){ tab('games', document.getElementById('tabgames')); show(i); }
function hsport(s, b){ document.querySelectorAll('.hchips button').forEach(x=>x.classList.remove('on')); b.classList.add('on');
  document.getElementById('h_nfl').style.display = s==='nfl'?'':'none';
  document.getElementById('h_soccer').style.display = s==='soccer'?'':'none'; }
function league(l, b){ document.querySelectorAll('#games .chips button').forEach(x=>x.classList.remove('on')); b.classList.add('on');
  document.querySelectorAll('#game_list .gcard').forEach(c=>{ c.style.display = (l==='ALL'||c.dataset.comp===l)?'':'none'; });
  document.querySelectorAll('#game_list .daygroup').forEach(h=>{ var n=h.nextElementSibling, any=false;
    while(n && !n.classList.contains('daygroup')){ if(n.style.display!=='none') any=true; n=n.nextElementSibling; }
    h.style.display = any?'':'none'; }); }
"""


LIVE_JS = r"""
(function () {
  var G = window.BM || [], LIVE = {}, lastFetch = 0, busy = false;
  var LABEL = {rec: 'RECOMMENDED', lean: 'LEAN', neg: 'LEAST-BAD'};
  function dayET(iso) { return new Date(iso).toLocaleDateString('en-CA', {timeZone: 'America/New_York'}).replace(/-/g, ''); }
  function dayUTC(iso) { return iso.slice(0, 10).replace(/-/g, ''); }
  function grade(b, hs, as) {
    if (!b) return null;
    if (b.m === '1X2') { var r = hs > as ? 'H' : (as > hs ? 'A' : 'D'); return r === b.s ? 'won' : 'lost'; }
    var v;
    if (b.m === 'TOTAL' || b.m === 'OU25') v = b.s === 'OVER' ? hs + as - b.l : b.l - (hs + as);
    else if (b.m === 'SPREAD') v = (b.s === 'H' ? hs - as : as - hs) + b.l;
    else if (b.m === 'ML') v = hs === as ? 0 : (((hs > as) === (b.s === 'H')) ? 1 : -1);
    else return null;
    return v > 0 ? 'won' : (v < 0 ? 'lost' : 'push');
  }
  function chip(g, extra) {
    var t = {won: 'HIT', lost: 'MISS', push: 'PUSH'}[g];
    return t ? '<span class="' + (extra || '') + 'res ' + g + '">' + t + '</span>' : '';
  }
  function scoreTxt(g, hs, as) { return g.sport === 'nfl' ? (g.away + ' ' + as + ' - ' + g.home + ' ' + hs) : (hs + '-' + as); }
  function setNum(el, v) {  // write a score; a changed number gets a quick bump
    if (!el) return;
    var s = String(v);
    if (el.textContent === s) return;
    var had = el.textContent !== '';
    el.textContent = s;
    if (had) { el.classList.remove('bump'); void el.offsetWidth; el.classList.add('bump'); }
  }
  function pending(g, now) {
    if (!g.eid || !g.lg || !g.ko) return false;
    var l = LIVE[g.eid], st = l ? l.state : g.st, k = new Date(g.ko).getTime();
    return st !== 'post' && k - now < 15 * 60000 && now - k < 36 * 3600000;
  }
  function apply(g) {
    var l = LIVE[g.eid]; if (!l) return;
    var hs = parseInt(l.hs, 10), as = parseInt(l.as, 10), has = !isNaN(hs) && !isNaN(as) && l.state !== 'pre';
    var card = document.getElementById('c' + g.i), det = document.getElementById('d' + g.i);
    var stTxt = l.state === 'in' ? 'LIVE  ' + (l.detail || '') : (l.state === 'post' ? 'Final' : null);
    [card, det].forEach(function (el) {
      if (!el || !stTxt) return;
      var s = el.querySelector('.gstat');
      if (s) { s.textContent = stTxt; s.classList.toggle('live', l.state === 'in'); }
    });
    if (card && has) {
      var h = card.querySelector('.sc[data-side=home]'), a = card.querySelector('.sc[data-side=away]');
      setNum(h, hs); setNum(a, as);
      if (l.state === 'post' && h && a) {  // dim the beaten side
        h.closest('.trow').classList.toggle('lose', hs < as);
        a.closest('.trow').classList.toggle('lose', as < hs);
      }
    }
    if (det && has) {
      var sc = det.querySelector('.hmid .score');
      if (sc) sc.textContent = sc.getAttribute('data-left') === 'away' ? (as + ' - ' + hs) : (hs + ' - ' + as);
    }
    var row = document.querySelector('tr[data-gi="' + g.i + '"] .nowcell');
    if (l.state === 'post' && has) {
      var r = grade(g.bet, hs, as);
      g.res = r; g.fh = hs; g.fa = as;
      if (card) {
        var b = card.querySelector('.bres'); if (b && r) b.outerHTML = chip(r, 'bres ');
        var pm = card.querySelector('.pmark');
        if (pm && g.pred) { var act = hs > as ? 'H' : (as > hs ? 'A' : 'D'); pm.textContent = act === g.pred ? ' ✓' : ' ✗';
          pm.className = 'pmark ' + (act === g.pred ? 'pok' : 'pbad'); }
      }
      if (row) row.innerHTML = chip(r) + '<small>' + scoreTxt(g, hs, as) + '</small>';
    } else if (l.state === 'in' && has && row) {
      row.innerHTML = '<span class="live">LIVE</span><small>' + scoreTxt(g, hs, as) + '</small>';
    }
  }
  function liveResults() {
    var box = document.getElementById('live_results'); if (!box) return;
    var done = G.filter(function (g) { return g.st !== 'post' && g.priced && g.res && g.bet; });
    if (!done.length) { box.innerHTML = ''; return; }
    var w = 0, l = 0, p = 0, u = 0, pw = 0, pl = 0, rows = '';
    done.forEach(function (g) {
      var r = g.res, ul = r === 'won' ? g.bet.o - 1 : (r === 'lost' ? -1 : 0);
      if (r === 'won') w++; else if (r === 'lost') l++; else p++;
      u += ul;
      var act = g.fh > g.fa ? 'H' : (g.fa > g.fh ? 'A' : 'D'), mk = '';
      if (g.pred) { if (act === g.pred) { pw++; mk = ' <span class="pok">✓</span>'; } else { pl++; mk = ' <span class="pbad">✗</span>'; } }
      rows += '<tr class="' + r + '"><td>' + g.match + '</td><td>' + scoreTxt(g, g.fh, g.fa) + '</td><td><span class="kchip ' +
        g.bet.kind + '">' + LABEL[g.bet.kind] + '</span> <b>' + g.bet.txt + '</b></td><td>' + chip(r) + '</td><td>' +
        (ul >= 0 ? '+' : '') + ul.toFixed(2) + 'u</td><td>' + g.predtxt + mk + '</td></tr>';
    });
    box.innerHTML = '<div class="rsec"><div class="shead"><h4>Finished since this page was published</h4><div class="srec">' +
      '<span class="big">' + w + '-' + l + (p ? '-' + p : '') + '</span><span class="' + (u >= 0 ? 'pos' : 'neg') + '">1u each: ' +
      (u >= 0 ? '+' : '') + u.toFixed(2) + 'u</span><span>predictions ' + pw + '-' + pl + '</span></div></div>' +
      '<div class="note">Live from ESPN. ' + (window.OS && OS.repo ? 'These join the records below at the next automatic ' +
      'update (within 30 minutes).' : 'These join the records below the next time the PC runs Update scores or Run model.') +
      '</div>' +
      '<div class="sect"><table class="bk rtable"><thead><tr><th>Game</th><th>Final</th><th>Best bet</th><th>Result</th>' +
      '<th>1u P/L</th><th>Prediction</th></tr></thead><tbody>' + rows + '</tbody></table></div></div>';
  }
  function stamp() {
    var s = document.getElementById('liveinfo');
    if (s) s.textContent = ' · live ' + new Date().toLocaleTimeString([], {hour: 'numeric', minute: '2-digit'});
  }
  function refresh(force) {
    var now = Date.now();
    if (busy || (!force && now - lastFetch < 60000)) return;
    var reqs = {};
    G.filter(function (g) { return pending(g, now); }).forEach(function (g) {
      [dayET(g.ko), dayUTC(g.ko)].forEach(function (dd) { reqs[g.lg + '|' + dd] = 1; });
    });
    var keys = Object.keys(reqs);
    lastFetch = now;
    if (!keys.length) { stamp(); return; }
    busy = true;
    Promise.all(keys.map(function (k) {
      var p = k.split('|');
      return fetch('https://site.api.espn.com/apis/site/v2/sports/' + p[0] + '/scoreboard?dates=' + p[1])
        .then(function (r) { return r.ok ? r.json() : {events: []}; }).catch(function () { return {events: []}; });
    })).then(function (all) {
      all.forEach(function (js) {
        (js.events || []).forEach(function (e) {
          var c = (e.competitions || [])[0]; if (!c) return;
          var h = null, a = null;
          (c.competitors || []).forEach(function (t) { if (t.homeAway === 'home') h = t; else if (t.homeAway === 'away') a = t; });
          var st = (e.status || {}).type || {};
          LIVE[e.id] = {state: st.state, detail: st.shortDetail || st.detail, hs: h && h.score, as: a && a.score};
        });
      });
      G.forEach(apply); liveResults(); stamp();
    }).finally(function () { busy = false; });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', function () { refresh(true); });
  else refresh(true);
  document.addEventListener('visibilitychange', function () { if (document.visibilityState === 'visible') refresh(false); });
  window.addEventListener('focus', function () { refresh(false); });
  setInterval(function () {
    if (document.visibilityState !== 'visible') return;
    var now = Date.now();
    var live = G.some(function (g) {
      var l = LIVE[g.eid], st = l ? l.state : g.st, k = g.ko ? new Date(g.ko).getTime() : 0;
      return st === 'in' || (st !== 'post' && k && now > k && now - k < 5 * 3600000);
    });
    if (live) refresh(true);
  }, 120000);
})();
"""

# The Run button (starts the model on GitHub with a key saved on this phone, then reloads with the results), and
# picking up newer pages: every run writes status.json next to the page, so an open page notices a newer one.
CLOUD_JS = r"""
(function () {
  var C = window.OS || {}, KEY = 'oddsea-key', RUN = 'oddsea-run', VIEW = 'oddsea-view';
  var T = C.steps || {}, W = C.weights || {};
  var LABEL = {run: 'Running the model', scores: 'Updating scores', history: 'Rebuilding history'};
  var cur = null, timer = null, hiddenAt = 0, touched = false;
  function $(id) { return document.getElementById(id); }
  function get(k, s) { try { return (s || localStorage).getItem(k); } catch (e) { return null; } }
  function put(k, v, s) { try { var st = s || localStorage; if (v == null) st.removeItem(k); else st.setItem(k, v); } catch (e) {} }
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) {
    return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]; }); }

  // ---- keep what you were looking at across the page's own reloads
  function view() {
    var sec = document.querySelector('.sec.on'), d = null;
    document.querySelectorAll('.dsnap').forEach(function (e) { if (e.style.display !== 'none') d = e; });
    var g = d ? (window.BM || [])[parseInt(d.id.slice(1), 10)] : null;
    return {tab: sec ? sec.id : 'games', game: g ? g.home + '|' + g.away : null,
            detail: document.body.classList.contains('show-detail'), y: window.scrollY};
  }
  function reload(b) {
    put(VIEW, JSON.stringify(view()), sessionStorage);
    if (b) put('oddsea-tried', b, sessionStorage);
    location.replace(location.pathname + '?v=' + encodeURIComponent(b || Date.now()));
  }
  (function restore() {
    var v = null;
    try { v = JSON.parse(get(VIEW, sessionStorage) || 'null'); } catch (e) {}
    put(VIEW, null, sessionStorage);
    if (!v) return;
    var b = document.querySelector('.tabs button[onclick*="\'' + v.tab + '\'"]');
    if (b && v.tab !== 'games') tab(v.tab, b);
    var g = v.game && (window.BM || []).filter(function (x) { return x.home + '|' + x.away === v.game; })[0];
    if (g) { show(g.i); if (!v.detail) document.body.classList.remove('show-detail'); }
    if (v.y) setTimeout(function () { window.scrollTo(0, v.y); }, 40);
  })();

  // ---- a newer page published (a scheduled run, or a run started somewhere else)?
  function check(auto) {
    if (!C.build || location.protocol === 'file:') return;
    fetch('status.json?t=' + Date.now(), {cache: 'no-store'}).then(function (r) { return r.ok ? r.json() : null; })
      .then(function (s) {
        if (!s || !s.build || s.build === C.build || cur) return;
        if (auto && get('oddsea-tried', sessionStorage) !== s.build) return reload(s.build);  // never loops
        var n = $('osnew'); if (n) n.hidden = false;
      }).catch(function () {});
  }
  ['pointerdown', 'keydown'].forEach(function (ev) {
    window.addEventListener(ev, function () { touched = true; }, {once: true, passive: true}); });
  setTimeout(function () { check(!touched); }, 1500);   // opened an older copy: swap to the newest straight away
  document.addEventListener('visibilitychange', function () {
    if (document.visibilityState === 'hidden') { hiddenAt = Date.now(); return; }
    check(hiddenAt && Date.now() - hiddenAt > 60000);   // back after a minute or more: load the newest
  });
  setInterval(function () { if (document.visibilityState === 'visible') check(false); }, 180000);

  window.OSRun = {reload: function () { reload(); }, tap: function () {}, sheet: function () {},
                  hide: function () { var c = $('oscard'); if (c) c.hidden = true; }};
  if (!C.repo) return;

  // ---- Run on GitHub
  var API = 'https://api.github.com/repos/' + C.repo;
  function key() { return get(KEY) || ''; }
  function gh(method, path, body, k) {
    var h = {'Accept': 'application/vnd.github+json'}, t = k || key();
    if (t) h.Authorization = 'Bearer ' + t;
    if (body) h['Content-Type'] = 'application/json';
    return fetch(API + path, {method: method, headers: h, cache: 'no-store', body: body ? JSON.stringify(body) : undefined})
      .then(function (r) {
        if (r.status === 204) return null;
        return r.json().catch(function () { return null; }).then(function (j) {
          if (!r.ok) { var e = new Error((j && j.message) || ('GitHub said ' + r.status)); e.status = r.status; throw e; }
          return j;
        });
      });
  }
  function card(title, msg, frac, err) {
    var c = $('oscard'); if (!c) return;
    c.hidden = false; c.classList.toggle('err', !!err);
    $('ostitle').textContent = title; $('osmsg').innerHTML = msg;
    if (frac != null) $('osfill').style.width = Math.round(100 * Math.max(0.03, Math.min(1, frac))) + '%';
    if (err) $('ostime').textContent = '';
  }
  function busy(on) {
    var b = $('osrun'); if (!b) return;
    b.classList.toggle('busy', on); b.querySelector('span').textContent = on ? 'Running' : 'Run';
  }
  function tick() {
    if (!cur) return;
    var s = Math.max(0, Math.round((Date.now() - cur.t0) / 1000));
    $('ostime').textContent = Math.floor(s / 60) + ':' + ('0' + s % 60).slice(-2);
  }
  function keep() { put(RUN, cur ? JSON.stringify(cur) : null); }
  function stop() { cur = null; keep(); busy(false); clearInterval(timer); timer = null; }
  function fail(msg) { stop(); card('Couldn’t finish', msg, 1, true); }
  function explain(e) {
    if (!e || !e.status) return 'Couldn’t reach GitHub. Check the connection and tap Run again.';
    if (e.status === 401) return 'GitHub rejected the saved key (expired or deleted?). Tap Run to save a new one.';
    if (e.status === 403 || e.status === 404) return 'This key can’t start runs. It needs <b>Actions: Read and write</b> on ' +
      esc(C.repo) + '. Tap Run to save a different key.';
    return esc(e.message);
  }
  function start(kind, again) {
    if (!key()) return sheet();
    if (cur && !again) { var c = $('oscard'); if (c) c.hidden = false; return; }
    closeSheet();
    var before = 0;
    cur = {kind: kind, t0: again && cur ? cur.t0 : Date.now(), id: null, before: 0, again: !!again};
    keep(); busy(true); tick();
    if (!timer) timer = setInterval(tick, 1000);
    card(LABEL[kind] + ' on GitHub', again ? 'A scheduled update went first; starting again…' : 'Starting…', 0.02);
    gh('GET', '/actions/workflows/' + C.wf + '/runs?per_page=5').then(function (j) {
      ((j && j.workflow_runs) || []).forEach(function (r) { if (r.id > before) before = r.id; });
      return gh('POST', '/actions/workflows/' + C.wf + '/dispatches', {ref: C.branch || 'main', inputs: {kind: kind}});
    }).then(function () {
      cur.before = before; keep(); setTimeout(poll, 3000);
    }).catch(function (e) {
      if (e && (e.status === 401 || e.status === 403 || e.status === 404)) put(KEY, null);
      fail(explain(e));
    });
  }
  function poll() {
    if (!cur) return;
    var p = cur.id ? gh('GET', '/actions/runs/' + cur.id) :
      gh('GET', '/actions/workflows/' + C.wf + '/runs?event=workflow_dispatch&per_page=10').then(function (j) {
        var n = ((j && j.workflow_runs) || []).filter(function (r) { return r.id > cur.before; });
        n.sort(function (a, b) { return a.id - b.id; });
        return n[0] || null;
      });
    p.then(function (r) {
      if (!cur) return;
      if (!r) {
        if (Date.now() - cur.t0 > 150000) return fail('GitHub took the request but no run showed up. Look in the repo’s Actions tab.');
        return setTimeout(poll, 4000);
      }
      cur.id = r.id; cur.errs = 0; keep();
      if (r.status === 'completed') return done(r);
      return steps(r).then(function () { setTimeout(poll, 6000); });
    }).catch(function (e) {
      if (e && (e.status === 401 || e.status === 403)) return fail(explain(e));
      cur.errs = (cur.errs || 0) + 1;
      if (cur.errs > 20) return fail('Lost track of the run on GitHub. It may still finish; this page picks up ' +
                                     'the new results by itself when it does.');
      setTimeout(poll, 8000);   // a hiccup: keep following
    });
  }
  function steps(r) {
    var title = LABEL[cur.kind] + ' on GitHub';
    if (r.status !== 'in_progress') { card(title, 'Waiting for a GitHub computer…', 0.04); return Promise.resolve(); }
    return gh('GET', '/actions/runs/' + r.id + '/jobs').then(function (j) {
      var jobs = (j && j.jobs) || [], all = [], tot = jobs.length > 1 ? 0 : 1, got = 0, now = null;
      jobs.forEach(function (jb) { (jb.steps || []).forEach(function (s) { if (!/^(Post |Complete job)/.test(s.name)) all.push(s); }); });
      all.forEach(function (s) {
        var w = W[s.name] || 1; tot += w;
        if (s.status === 'completed') got += w; else if (s.status === 'in_progress' && !now) now = s.name;
      });
      var msg = now === 'Run the model' ? 'Fitting, pricing and simulating every game' : (T[now] || now || 'Working');
      card(title, esc(msg) + '…', all.length ? got / Math.max(tot, 1) : 0.06);
    }).catch(function () {});
  }
  function done(r) {
    if (r.conclusion === 'cancelled' && !cur.again) return start(cur.kind, true);  // a scheduled update took its place
    if (r.conclusion !== 'success') return fail('The run ended “' + esc(r.conclusion) + '”. <a href="' +
      esc(r.html_url) + '" target="_blank" rel="noopener">See what happened on GitHub</a>.');
    card('Done', 'Loading the new results…', 1);
    var n = 0;
    (function wait() {   // the page is live a moment after the run finishes
      fetch('status.json?t=' + Date.now(), {cache: 'no-store'}).then(function (x) { return x.ok ? x.json() : null; })
        .catch(function () { return null; }).then(function (s) {
          if ((s && s.build && s.build !== C.build) || ++n > 25) { stop(); return reload(s && s.build); }
          setTimeout(wait, 3000);
        });
    })();
  }
  function closeSheet() { ['ossheet', 'osbg'].forEach(function (id) { var e = $(id); if (e) e.remove(); }); }
  function sheet() {
    closeSheet();
    var k = key(), who = C.repo.split('/'), bg = document.createElement('div'), s = document.createElement('div');
    bg.id = 'osbg'; bg.className = 'osbg'; bg.onclick = closeSheet;
    s.id = 'ossheet'; s.className = 'ossheet'; s.setAttribute('role', 'dialog'); s.setAttribute('aria-modal', 'true');
    s.innerHTML = k ? (
      '<h4>Run OddSea on GitHub</h4>' +
      '<p class="note">A key is saved on this phone. <b>Run</b> starts a full model run (fresh DraftKings and Kalshi ' +
      'prices, about 4 minutes) and this page reloads when it’s done. It also runs by itself every 2 hours, and ' +
      'scores and results update every 30 minutes.</p>' +
      '<div class="osbtns"><button class="btn btn-primary" data-k="run">Run the model now</button>' +
      '<button class="btn btn-default" data-k="scores">Update scores now</button></div>' +
      '<div class="osbtns"><button class="btn btn-default" data-x="forget">Remove the key from this phone</button>' +
      '<button class="btn btn-default" data-x="close">Close</button></div>'
    ) : (
      '<h4>Run OddSea from this page</h4>' +
      '<p class="note">Tapping <b>Run</b> starts the model on GitHub (about 4 minutes) and reloads this page with the ' +
      'results. It needs a GitHub key saved on this phone, once:</p>' +
      '<ol><li>Open <a href="https://github.com/settings/personal-access-tokens/new" target="_blank" rel="noopener">' +
      'GitHub › new fine-grained key</a> (signed in as ' + esc(who[0]) + ').</li>' +
      '<li>Name it “OddSea phone”. <b>Repository access</b>: Only select repositories › <b>' + esc(who[1]) + '</b>.</li>' +
      '<li><b>Permissions</b> › Repository permissions › <b>Actions: Read and write</b>. Nothing else.</li>' +
      '<li>Generate it, copy it, paste it here.</li></ol>' +
      '<input id="oskey" type="password" autocomplete="off" autocapitalize="off" spellcheck="false" placeholder="github_pat_…">' +
      '<div id="oserr" class="oserr" role="alert"></div>' +
      '<div class="osbtns"><button class="btn btn-primary" data-x="save">Save the key and run</button>' +
      '<button class="btn btn-default" data-x="close">Not now</button></div>' +
      '<p class="note small">The key stays in this browser on this phone. It can only start and follow OddSea’s ' +
      'runs; remove it here or delete it on GitHub any time.</p>'
    );
    s.addEventListener('click', function (ev) {
      var t = ev.target.closest('button'); if (!t) return;
      if (t.dataset.k) return start(t.dataset.k);
      var x = t.dataset.x;
      if (x === 'close') return closeSheet();
      if (x === 'forget') { put(KEY, null); return closeSheet(); }
      if (x === 'save') {
        var v = ($('oskey').value || '').trim(), er = $('oserr');
        if (!/^(github_pat_|ghp_)[A-Za-z0-9_]{20,}$/.test(v)) {
          er.textContent = 'That doesn’t look like a GitHub key (they start with github_pat_).'; return; }
        t.disabled = true; er.textContent = 'Checking the key…';
        gh('GET', '/actions/workflows/' + C.wf, null, v).then(function () { put(KEY, v); start('run'); })
          .catch(function (e) {
            t.disabled = false;
            er.innerHTML = e && e.status === 401 ? 'GitHub rejected that key. Copy it again (all of it).' : explain(e);
          });
      }
    });
    document.body.appendChild(bg); document.body.appendChild(s);
    var i = $('oskey'); if (i) setTimeout(function () { i.focus(); }, 300);
  }
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape') closeSheet(); });
  window.OSRun.tap = function () { if (!key()) return sheet(); start('run'); };
  window.OSRun.sheet = sheet;
  try {   // a run started before this page loaded (or before the last reload): keep following it
    var s = JSON.parse(get(RUN) || 'null');
    if (s && Date.now() - s.t0 < 3600000) {
      cur = s; busy(true); tick(); timer = setInterval(tick, 1000);
      card(LABEL[s.kind] + ' on GitHub', 'Checking on the run…', 0.05); poll();
    } else put(RUN, null);
  } catch (e) {}
})();
"""
PLAY_SVG = '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M7 4.5v15a1 1 0 0 0 1.5.86l12.5-7.5a1 1 0 0 0 0-1.72L8.5 3.64A1 1 0 0 0 7 4.5z"/></svg>'


def live_data(res: engine.RunResult) -> list[dict]:
    """What the phone page needs to keep scores and results current by itself (ESPN ids, bets, predictions)."""
    import datetime as _dt
    from . import espn as ESP, picks as PK
    ran = pd.Timestamp(res.ran_at)
    ran = (ran.tz_localize(_dt.datetime.now().astimezone().tzinfo) if ran.tzinfo is None else ran).tz_convert("UTC").tz_localize(None)
    out = []
    for i, d in enumerate(res.games):
        e = d.get("espn") or {}
        gb = None if d.get("started") else PK.game_bet(d)
        bet = None
        if gb:
            src, b = gb
            bet = {"m": b["market"], "s": A.bet_side(d, b), "l": None if b.get("line") is None else float(b["line"]),
                   "o": float(b["odds"]), "kind": {"auto": "rec", "lean": "lean", "leastbad": "neg"}[src],
                   "txt": f"{engine.describe_bet({**b, 'home': d['home'], 'away': d['away']})} {A.amer(b['odds'])}"}
        pr = A.game_prediction(d)
        ko = A._kick_utc(d)
        out.append({"i": i, "eid": e.get("espn_id"), "lg": ESP.LEAGUES.get(d["comp"]),
                    "ko": (ko.isoformat() + "Z") if ko is not None else None, "st": e.get("state") or "pre",
                    "sport": d["sport"], "home": d["home"], "away": d["away"],
                    "match": f"{d['away']} @ {d['home']}" if d["sport"] == "nfl" else f"{d['home']} v {d['away']}",
                    "bet": bet, "pred": pr["result"] if pr else None, "predtxt": pr["text"] if pr else "",
                    "priced": bool(ko is not None and ko > ran)})
    return out


def _build_id() -> str:
    import datetime as _dt
    b = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{b}-{os.environ['GITHUB_RUN_ID']}" if os.environ.get("GITHUB_RUN_ID") else b


def status_data(res: engine.RunResult, build: str) -> dict:
    """status.json beside the page: lets an open page notice a newer one, and tells the scheduled score updates
    whether any game is live or waiting for a result."""
    import datetime as _dt
    return {"build": build, "built": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "ran_at": str(res.ran_at), "scores_at": str(getattr(res, "scores_at", None) or ""),
            "games": [{"ko": g["ko"], "st": g["st"], "match": g["match"]} for g in live_data(res)]}


def build_html(res: engine.RunResult, build: str | None = None) -> str:
    from . import cloud
    build = build or _build_id()
    rp = cloud.repo() if (cloud.in_actions() or cloud.enabled()) else None  # a Run button only where runs can start
    unit = slate.user_config().get("unit", 10)
    games = res.games
    # game list grouped by day
    items, last = [], None
    for i, d in enumerate(games):
        dk = A.day_key(d)
        if dk != last:
            items.append(tags.div(pd.Timestamp(dk).strftime("%A, %B %d").replace(" 0", " "), class_="daygroup"))
            last = dk
        items.append(A.game_card(i, d, i == 0, onclick=f"show({i})"))
    details = []
    for i, d in enumerate(games):
        try:
            body = A.nfl_detail(d, unit) if d["sport"] == "nfl" else A.soccer_detail(d, unit)
        except Exception as e:
            body = tags.div(f"Could not draw this game: {e}", class_="empty")
        details.append(tags.div(body, id=f"d{i}", class_="dsnap", style="" if i == 0 else "display:none"))

    comps = sorted({d["comp"] for d in games}, key=lambda c: list(A.COMP_NAMES).index(c) if c in A.COMP_NAMES else 9)
    chips = tags.div(tags.button("All", class_="on", onclick="league('ALL', this)"),
                     *[tags.button(A.COMP_NAMES.get(c, c), onclick=f"league('{c}', this)") for c in comps], class_="chips")

    # recommended bets
    idx = {engine.game_id(d): i for i, d in enumerate(games)}
    cards = []
    for p in res.recommended:
        units = p.get("units") or p["stake"] / unit
        match = f"{p['away']} @ {p['home']}" if p["sport"] == "nfl" else f"{p['home']} v {p['away']}"
        gi = idx.get(f"{p['sport']}|{p['home']}|{p['away']}")
        gd = games[gi] if gi is not None else None
        wtxt = A.why_text(gd, p, "rec") if gd else ""
        cards.append(tags.div(
            tags.div(f"{A.COMP_NAMES.get(p['comp'], p['comp'])} - {pd.Timestamp(p['game_date']):%a %d %b}", class_="gtop"),
            tags.div(match, style="margin-top:6px;font-weight:600"),
            tags.div(tags.b("Prediction: "), A.pred_text(gd), class_="note", style="margin-top:4px") if gd else None,
            tags.div(engine.describe_bet(p), class_="bet"),
            tags.div(f"{A.amer(p['odds'])} at {A.BOOKS.get(p['book'], p['book'])}"),
            tags.div(f"{units:g}u  -  ${units * unit:,.2f}", class_="stake"),
            tags.div(tags.b("Why: "), wtxt, class_="why") if wtxt else None,
            tags.div(HTML(f"Edge <b>{p['edge']:+.1%}</b> - fair price {A.amer(1 / p['model_prob'])}; only bet at "
                          f"{A.amer(p['odds'])} or better" + ("<br><u>Open game page</u>" if gi is not None else "")),
                     class_="note"),
            class_=f"bm-card {p['sport']}", onclick=f"openGame({gi})" if gi is not None else None))
    bets = tags.div(*cards, class_="bm-cards") if cards else tags.div(
        "No bets cleared the minimum edge on this run. The model's best bet on every game is below.", class_="empty")
    bets = tags.div(bets, A.all_bets_table(games, lambda i, d: f"openGame({i})", unit, res.ran_at))

    # tracker (recommended / hand-logged bets; leans shown separately below)
    from . import picks as PK
    allp = db.picks()
    d = PK.main_picks(allp)
    s = d[d["status"].isin(["won", "lost", "push"])]
    rec = f"{(s.status == 'won').sum()}-{(s.status == 'lost').sum()}-{(s.status == 'push').sum()}" if len(s) else "-"
    prof = f"${s.pnl.sum():+,.2f} ({s.pnl.sum() / unit:+.1f}u)" if len(s) else "-"
    roi = f"{s.pnl.sum() / s.stake.sum():+.1%}" if len(s) and s.stake.sum() else "-"
    c = s.loc[s["market"].isin(["1X2", "OU25", "ML"]), "clv"].dropna()
    kpis = tags.div(*[tags.div(tags.div(k, class_="k"), tags.div(v, class_="v"), class_="kpi") for k, v in
                      [("Record", rec), ("Profit", prof), ("ROI", roi), ("Avg CLV", f"{c.mean():+.1%}" if len(c) else "-")]],
                    class_="kpis")
    rows = []
    for x in d.sort_values("id", ascending=False).head(40).to_dict("records"):
        rows.append(tags.tr(tags.td(pd.Timestamp(x["game_date"]).strftime("%d %b")),
                            tags.td(f"{x['away']} @ {x['home']}" if x["sport"] == "nfl" else f"{x['home']} v {x['away']}"),
                            tags.td(engine.describe_bet(x)), tags.td(A.amer(x["odds"])),
                            tags.td(f"{x['stake'] / unit:g}u"), tags.td(x["status"]),
                            tags.td(f"${x['pnl']:+,.2f}" if pd.notna(x["pnl"]) else "")))
    lk = []
    for src, title in (("lean", "Leans (edge > 0, under the minimum)"), ("leastbad", "Least-bad (most likely outcome)")):
        sm = PK.summary(allp[allp["source"] == src]) if len(allp) else {"n": 0, "open": 0}
        v = (f"{sm['record']} · {sm['pnl'] / unit:+.1f}u · ROI {sm['roi']:+.1%}" if sm.get("record")
             else f"{sm['open']} open, none graded")
        lk.append(tags.div(tags.div(title, class_="k"), tags.div(v, class_="v", style="font-size:1rem"), class_="kpi"))
    lrows = []
    for x in PK.lean_picks(allp).sort_values("id", ascending=False).head(40).to_dict("records") if len(allp) else []:
        lrows.append(tags.tr(tags.td(pd.Timestamp(x["game_date"]).strftime("%d %b")),
                             tags.td(tags.span("LEAN" if x["source"] == "lean" else "LEAST-BAD",
                                               class_=f"kchip {'lean' if x['source'] == 'lean' else 'neg'}")),
                             tags.td(f"{x['away']} @ {x['home']}" if x["sport"] == "nfl" else f"{x['home']} v {x['away']}"),
                             tags.td(engine.describe_bet(x)), tags.td(A.amer(x["odds"])),
                             tags.td(f"{x['edge']:+.1%}" if pd.notna(x["edge"]) else ""), tags.td(x["status"]),
                             tags.td(f"${x['pnl']:+,.2f}" if pd.notna(x["pnl"]) else "")))
    leans_sec = tags.div(
        tags.h5("The model's best bet on every other game", style="margin-top:22px"),
        tags.div("Tracked at a notional 0.5u each; not included in the record above.", class_="note"),
        tags.div(*lk, class_="kpis"),
        tags.table(tags.thead(tags.tr(*[tags.th(h) for h in ["Date", "", "Match", "Bet", "Odds", "Edge", "Status", "P/L"]])),
                   tags.tbody(*lrows), class_="bk") if lrows else None)
    try:
        from . import pricelog
        price_sec = pricelog.view()
    except Exception:
        price_sec = None
    tracker = tags.div(kpis, tags.table(
        tags.thead(tags.tr(*[tags.th(h) for h in ["Date", "Match", "Bet", "Odds", "Units", "Status", "P/L"]])),
        tags.tbody(*rows), class_="bk") if rows else tags.div("No picks yet.", class_="empty"), leans_sec, price_sec)

    # results: model vs books vs actual
    from . import results as R
    results_sec = R.view(unit, limit=80)

    # model history: every past game predicted vs what happened
    from . import history as H
    try:
        hf = H.frame()
    except Exception:
        hf = pd.DataFrame()
    if len(hf):
        hist_sec = tags.div(
            tags.div(H.INTRO, class_="hintro"),
            tags.div(tags.button("NFL", class_="on", onclick="hsport('nfl', this)"),
                     tags.button("Soccer", onclick="hsport('soccer', this)"), class_="chips hchips"),
            tags.div(H.view(hf[hf["sport"] == "nfl"], "nfl", "all seasons", 60), id="h_nfl"),
            tags.div(H.view(hf[hf["sport"] == "soccer"], "soccer", "all seasons", 60), id="h_soccer", style="display:none"))
    else:
        hist_sec = tags.div("History not built yet. On the PC: Model history tab > Rebuild history.", class_="empty")

    ran = res.ran_at.strftime("%a %d %b, %I:%M %p").replace(", 0", ", ")
    sc = getattr(res, "scores_at", None)
    sc_txt = sc.strftime("%a %I:%M %p").replace(" 0", " ") if sc is not None else "as of the run"
    nrec = len(res.recommended)
    run_btn = tags.button(HTML(PLAY_SVG), tags.span("Run"), id="osrun", type="button", class_="run-btn",
                          onclick="OSRun.tap()", title="Run the model on GitHub now (about 4 minutes)") if rp else None
    if rp:
        stale = tags.div(tags.div(
            "Prices and injuries are as of the model run, which happens by itself every 2 hours. Scores update live "
            "from ESPN while this page is open; results and the tracker update every 30 minutes. Tap Run for fresh "
            "prices now. ", tags.button("Run options", type="button", class_="oslink", onclick="OSRun.sheet()")),
            class_="stale")
    else:
        stale = tags.div("Snapshot: prices and injuries are as of the model run and may have moved. Scores update live "
                         "from ESPN whenever you open or come back to this page. On the PC: Run model for fresh prices.",
                         class_="stale")
    os_cfg = {"repo": rp, "wf": cloud.WORKFLOW, "branch": os.environ.get("GITHUB_REF_NAME") or "main",
              "build": build, "steps": cloud.STEP_TEXT, "weights": cloud.STEP_WEIGHT}
    run_ui = [tags.div(tags.div(tags.b("Running the model on GitHub", id="ostitle"), tags.span(id="ostime", class_="ostime"),
                                tags.button("×", type="button", class_="osx", onclick="OSRun.hide()",
                                            **{"aria-label": "Hide"}), class_="osrow"),
                       tags.div(id="osmsg", class_="osmsg"), tags.div(tags.i(id="osfill"), class_="osprog"),
                       id="oscard", class_="oscard", hidden="", role="status", **{"aria-live": "polite"}),
              tags.button("New results are ready · ", tags.b("Refresh"), id="osnew", type="button", class_="osnew",
                          hidden="", onclick="OSRun.reload()")]
    page = tags.html(
        tags.head(tags.meta(charset="utf-8"),
                  tags.meta(name="viewport", content="width=device-width, initial-scale=1, viewport-fit=cover"),
                  tags.meta(name="theme-color", content="#080d1b"),
                  tags.title(f"{T.APP_NAME} - latest run"),
                  tags.script(HTML(T.PAGE_JS)),
                  HTML(T.FONTS),
                  tags.link(rel="stylesheet", href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css"),
                  tags.style(HTML(T.CSS))),
        tags.body(
            tags.header(
                tags.div(T.brand(),
                         tags.div(tags.div(f"Model run {ran}"),
                                  tags.div(f"Scores {sc_txt}", tags.span(id="liveinfo")), class_="runinfo"),
                         run_btn, T.theme_button(), class_="topbar"),
                tags.nav(tags.button("Games", id="tabgames", class_="on", onclick="tab('games', this)"),
                         tags.button("Bets to take", tags.span(str(nrec), class_="count") if nrec else None,
                                     onclick="tab('bets', this)"),
                         tags.button("Results", onclick="tab('results', this)"),
                         tags.button("Model history", onclick="tab('history', this)"),
                         tags.button("Pick tracker", onclick="tab('tracker', this)"), class_="tabs"),
                class_="appbar"),
            tags.div(
                stale,
                tags.div(chips, tags.div(tags.div(*items, id="game_list"), tags.div(*details, id="game_detail"),
                                         class_="layout"), id="games", class_="sec on"),
                tags.div(bets, id="bets", class_="sec"),
                tags.div(tags.div(id="live_results"), results_sec, id="results", class_="sec"),
                tags.div(hist_sec, id="history", class_="sec"),
                tags.div(tracker, id="tracker", class_="sec"),
                class_="wrap"),
            *run_ui,
            tags.script(HTML(JS)),
            tags.script(HTML("window.BM = " + json.dumps(live_data(res)).replace("</", "<\\/") + ";\n"
                             "window.OS = " + json.dumps(os_cfg).replace("</", "<\\/") + ";")),
            tags.script(HTML(LIVE_JS)),
            tags.script(HTML(CLOUD_JS)),
            class_="snap"))
    return "<!doctype html>\n" + str(page)


def save(res: engine.RunResult) -> Path:
    """snapshot/index.html plus status.json (same build id in both)."""
    build = _build_id()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(build_html(res, build), encoding="utf-8")
    (OUT.parent / "status.json").write_text(json.dumps(status_data(res, build)), encoding="utf-8")
    return OUT


def publish_github(html: str, token: str, repo: str, path: str = "index.html") -> str:
    """Upload the page to a GitHub repo (GitHub Pages serves it). Returns the public page URL."""
    api = f"https://api.github.com/repos/{repo}/contents/{path}"
    h = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "User-Agent": "betmodel",
         "X-GitHub-Api-Version": "2022-11-28"}
    sha = None
    try:
        with urllib.request.urlopen(urllib.request.Request(api, headers=h), timeout=20) as r:
            sha = json.loads(r.read()).get("sha")
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise RuntimeError(f"GitHub said {e.code} reading the repo - check the token and repo name") from None
    body = {"message": f"{T.APP_NAME} snapshot {pd.Timestamp.now():%Y-%m-%d %H:%M}",
            "content": base64.b64encode(html.encode("utf-8")).decode()}
    if sha:
        body["sha"] = sha
    req = urllib.request.Request(api, data=json.dumps(body).encode(), headers={**h, "Content-Type": "application/json"},
                                 method="PUT")
    try:
        urllib.request.urlopen(req, timeout=30).read()
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"GitHub said {e.code} uploading - the token needs 'Contents: Read and write' "
                           "on this repo") from None
    owner, name = repo.split("/", 1)
    return f"https://{owner.lower()}.github.io/{name}/"


def save_and_publish(res: engine.RunResult, min_gap_min: float = 0) -> str:
    """Always saves snapshot/index.html; publishes too when a GitHub token + repo are configured.
    min_gap_min > 0 (automatic updates): skip the upload if the page was published less than that long ago."""
    if min_gap_min and not os.environ.get("BETMODEL_NO_PUBLISH") \
            and db.cache_get("snapshot:published", min_gap_min / 60) is not None:
        # automatic update inside the gap: skip building the page too (keeps the app smooth while it runs)
        return "Snapshot not rebuilt; the phone page uploads again within 15 minutes (it shows live scores itself meanwhile)"
    path = save(res)
    if os.environ.get("BETMODEL_NO_PUBLISH"):
        return f"Snapshot saved to {path.parent.name}/{path.name} (publishing is off for this copy)"
    from . import cloud
    if cloud.enabled():  # the runs on GitHub publish the page; this PC only keeps a local copy
        return f"Snapshot saved to {path.parent.name}/{path.name} (the phone page is published by the runs on GitHub)"
    c = slate.user_config()
    tok, repo = c.get("github_token"), c.get("github_repo")
    if not (tok and repo):
        return f"Snapshot saved to {path.parent.name}/{path.name} (add a GitHub token with keys.bat to view it from anywhere)"
    if min_gap_min and db.cache_get("snapshot:published", min_gap_min / 60) is not None:
        return "Snapshot saved; the phone page uploads again within 15 minutes (it shows live scores itself meanwhile)"
    try:
        url = publish_github(path.read_text(encoding="utf-8"), tok, repo)
        db.cache_put("snapshot:url", url)
        db.cache_put("snapshot:published", str(pd.Timestamp.now()))
        return f"Snapshot published: {url} (takes about a minute to update)"
    except Exception as e:
        return f"Snapshot saved locally; publishing failed: {e}"
