"""Look and feel shared by the PC app and the phone page: one stylesheet with light and dark colours,
type (Inter), motion and the logo. To rename the app, change APP_NAME."""
from __future__ import annotations

from htmltools import HTML, tags

APP_NAME = "OddSea"

# whale fluke over a wave, drawn white on the gradient tile
LOGO_SVG = (
    '<svg viewBox="0 0 24 24" fill="none" aria-hidden="true">'
    '<path fill="#fff" d="M10.7 17.3C10.9 14.9 10.9 12.9 10.5 11.5C9.3 10 6.9 9.2 4.7 8.4C3.2 7.8 2.3 6.7 1.9 5.1'
    'C4 5.9 6.7 6 9 6.7C10.4 7.2 11.4 8.1 12 9.2C12.6 8.1 13.6 7.2 15 6.7C17.3 6 20 5.9 22.1 5.1'
    'C21.7 6.7 20.8 7.8 19.3 8.4C17.1 9.2 14.7 10 13.5 11.5C13.1 12.9 13.1 14.9 13.3 17.3Z"/>'
    '<path stroke="#fff" stroke-opacity=".75" stroke-width="1.6" stroke-linecap="round" '
    'd="M3 20.2c1.5 0 1.5-.9 3-.9s1.5.9 3 .9 1.5-.9 3-.9 1.5.9 3 .9 1.5-.9 3-.9 1.5.9 3 .9"/></svg>')

ICONS = (
    '<svg class="i-moon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
    'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/></svg>'
    '<svg class="i-sun" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
    'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="4"/>'
    '<path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>')

FONTS = ('<link rel="preconnect" href="https://fonts.googleapis.com">'
         '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
         '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap">')


def brand():
    return tags.span(tags.span(HTML(LOGO_SVG), class_="brand-mark"), tags.span(APP_NAME, class_="brand-name"),
                     class_="brand")


def theme_button():
    return tags.button(HTML(ICONS), type="button", class_="theme-btn", title="Light / dark mode",
                       onclick="bmToggleTheme()", **{"aria-label": "Switch between light and dark mode"})


# Runs in <head>: picks light/dark before anything is drawn (your saved choice, else the device setting).
PAGE_JS = """
(function () {
  var KEY = 'bm-theme', root = document.documentElement;
  function sys() { return window.matchMedia && matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'; }
  function saved() { try { return localStorage.getItem(KEY); } catch (e) { return null; } }
  function apply(t) { root.setAttribute('data-theme', t); root.setAttribute('data-bs-theme', t); }
  apply(saved() || sys());
  try { matchMedia('(prefers-color-scheme: dark)').addEventListener('change', function () { if (!saved()) apply(sys()); }); } catch (e) {}
  window.bmToggleTheme = function () {
    var t = root.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
    root.classList.add('theme-anim'); apply(t);
    try { localStorage.setItem(KEY, t); } catch (e) {}
    setTimeout(function () { root.classList.remove('theme-anim'); }, 450);
  };
  // highlight the tapped game card straight away (the server fills in the game page)
  window.bmActive = function (i) {
    document.querySelectorAll('.gcard.active').forEach(function (c) { c.classList.remove('active'); });
    var c = document.getElementById('c' + i); if (c) c.classList.add('active');
  };
  // a new tab starts at the top (the app's tabs; the phone page does this in tab())
  document.addEventListener('shown.bs.tab', function () {
    if (window.scrollY > 0) window.scrollTo({top: 0, behavior: 'auto'});
  });
})();
"""

CSS = r"""
/* ================================================================== tokens */
:root {
  color-scheme: light;
  --font: 'Inter', system-ui, -apple-system, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
  --bg: #f3f5fa; --glow-1: rgba(37,99,235,.08); --glow-2: rgba(14,165,233,.07);
  --surface: #ffffff; --surface-2: #f6f8fc; --surface-3: #edf1f7;
  --ink: #0b1324; --ink-2: #334155; --muted: #64748b;
  --line: #e3e8f0; --line-2: #cfd7e3;
  --brand: #2563eb; --brand-2: #0ea5e9; --brand-soft: rgba(37,99,235,.09); --ring: rgba(37,99,235,.24);
  --on: #2563eb;
  --good: #16a34a; --good-ink: #166534; --good-soft: #ecfaf1; --good-line: #bdebcd; --good-row: #f1fbf5; --good-row-h: #e4f7eb;
  --bad: #dc2626; --bad-ink: #991b1b; --bad-soft: #fdf1f1; --bad-line: #f6c9c9; --bad-row: #fdf3f3; --bad-row-h: #fae5e5;
  --hit: #15803d; --miss: #c02626;
  --warn: #f59e0b; --warn-ink: #854d0e; --warn-soft: #fff8e6; --warn-line: #f6e0a0;
  --orange-ink: #9a3412; --orange-soft: #fff0e3;
  --lean: #4f46e5; --lean-ink: #3730a3; --lean-soft: #eff0ff; --lean-line: #cdd2fb;
  --live: #e11d48;
  --c-home: #2563eb; --c-draw: #64748b; --c-away: #b45309;      /* validated pair + neutral draw */
  --c-model: #2563eb; --c-books: #d97706;                        /* validated pair (charts) */
  --hero-1: #0b1631; --hero-2: #143577; --hero-ink: #f8fafc; --hero-muted: #a3b3cf; --hero-sub: #cdd8ec;
  --shadow-1: 0 1px 2px rgba(15,23,42,.05), 0 1px 3px rgba(15,23,42,.04);
  --shadow-2: 0 10px 24px -12px rgba(15,23,42,.22), 0 2px 6px rgba(15,23,42,.05);
  --shadow-3: 0 24px 48px -20px rgba(15,23,42,.38);
  --ease: cubic-bezier(.2,.8,.2,1);
  --sticky-top: 84px;
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --bg: #090e1a; --glow-1: rgba(59,130,246,.13); --glow-2: rgba(14,165,233,.08);
  --surface: #111a2c; --surface-2: #16213a; --surface-3: #1d2944;
  --ink: #e7edf7; --ink-2: #b8c4d8; --muted: #8494ad;
  --line: #213050; --line-2: #2e3f63;
  --brand: #5b9bff; --brand-2: #38bdf8; --brand-soft: rgba(91,155,255,.14); --ring: rgba(91,155,255,.34);
  --good: #22c55e; --good-ink: #86efac; --good-soft: rgba(34,197,94,.12); --good-line: rgba(34,197,94,.32);
  --good-row: rgba(34,197,94,.08); --good-row-h: rgba(34,197,94,.14);
  --bad: #f05252; --bad-ink: #fca5a5; --bad-soft: rgba(240,82,82,.12); --bad-line: rgba(240,82,82,.32);
  --bad-row: rgba(240,82,82,.08); --bad-row-h: rgba(240,82,82,.14);
  --warn-ink: #fcd34d; --warn-soft: rgba(245,158,11,.10); --warn-line: rgba(245,158,11,.28);
  --orange-ink: #fdba74; --orange-soft: rgba(249,115,22,.13);
  --lean: #818cf8; --lean-ink: #c7d2fe; --lean-soft: rgba(129,140,248,.13); --lean-line: rgba(129,140,248,.32);
  --live: #fb7185;
  --c-model: #3b82f6; --c-books: #d97706;                        /* dark steps, validated on #111a2c */
  --hero-1: #0f1d3b; --hero-2: #183c80;
  --shadow-1: 0 1px 2px rgba(0,0,0,.35);
  --shadow-2: 0 12px 28px -14px rgba(0,0,0,.7);
  --shadow-3: 0 26px 52px -18px rgba(0,0,0,.8);
}
:root, :root[data-bs-theme] {
  --bs-body-font-family: var(--font); --bs-body-bg: var(--bg); --bs-body-color: var(--ink);
  --bs-emphasis-color: var(--ink); --bs-secondary-color: var(--muted); --bs-border-color: var(--line);
  --bs-tertiary-bg: var(--surface-2); --bs-secondary-bg: var(--surface-3); --bs-heading-color: var(--ink);
}
:root:root { --shiny-spinner-color: var(--brand); --shiny-pulse-height: 3px;
  --shiny-pulse-background: linear-gradient(90deg, rgba(56,189,248,0), #38bdf8, #2563eb, #10b981, rgba(16,185,129,0)); }

/* ================================================================== base */
html { scroll-behavior: smooth; -webkit-text-size-adjust: 100%; }
body {
  font-family: var(--font); color: var(--ink); background-color: var(--bg);
  background-image: radial-gradient(1100px 560px at 0% -12%, var(--glow-1), transparent 60%),
                    radial-gradient(900px 520px at 100% -8%, var(--glow-2), transparent 55%);
  background-repeat: no-repeat; background-attachment: fixed;
  -webkit-font-smoothing: antialiased; -moz-osx-font-smoothing: grayscale; letter-spacing: -0.006em;
}
h1, h2, h3, h4, h5, h6 { color: var(--ink); letter-spacing: -0.02em; }
::selection { background: var(--brand-soft); }
:focus-visible { outline: 2px solid var(--brand); outline-offset: 2px; }
* { scrollbar-width: thin; scrollbar-color: var(--line-2) transparent; }
::-webkit-scrollbar { width: 10px; height: 10px; }
::-webkit-scrollbar-thumb { background: var(--line-2); border-radius: 999px; border: 3px solid transparent; background-clip: content-box; }
::-webkit-scrollbar-track { background: transparent; }
.muted { color: var(--muted) !important; }
html.theme-anim *, html.theme-anim *::before, html.theme-anim *::after {
  transition: background-color .35s var(--ease), border-color .35s var(--ease), color .35s var(--ease),
              fill .35s var(--ease), stroke .35s var(--ease), box-shadow .35s var(--ease) !important; }

/* ================================================================== motion */
@keyframes bm-in { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: none; } }
@keyframes bm-slide { from { opacity: 0; transform: translateX(36px); } to { opacity: 1; transform: none; } }
@keyframes bm-reveal { from { clip-path: inset(0 100% 0 0); } to { clip-path: inset(0 0 0 0); } }
@keyframes bm-grow { from { transform: scaleX(0); } to { transform: scaleX(1); } }
@keyframes bm-pulse { 0% { box-shadow: 0 0 0 0 rgba(225,29,72,.55); } 70% { box-shadow: 0 0 0 7px rgba(225,29,72,0); }
                      100% { box-shadow: 0 0 0 0 rgba(225,29,72,0); } }
@keyframes bm-bump { 0% { transform: scale(1); } 35% { transform: scale(1.35); color: var(--brand); } 100% { transform: scale(1); } }
@keyframes bm-toast { from { opacity: 0; transform: translateY(16px) scale(.98); } to { opacity: 1; transform: none; } }
@keyframes bm-spin { to { transform: rotate(360deg); } }
/* fill-mode backwards, not both: a transform left in place would trap the phone's full-screen game page */
.tab-content > .tab-pane.active, .sec.on { animation: bm-in .36s var(--ease) backwards; }
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { animation-duration: .01ms !important; animation-iteration-count: 1 !important;
                           transition-duration: .01ms !important; scroll-behavior: auto !important; } }

/* ================================================================== PC app: navbar */
.navbar {
  position: sticky; top: 0; z-index: 1030; min-height: 64px; padding: 10px 0;
  background: rgba(8,13,27,.84) !important; border-bottom: 1px solid rgba(148,163,184,.12);
  backdrop-filter: saturate(170%) blur(16px); -webkit-backdrop-filter: saturate(170%) blur(16px);
  box-shadow: 0 12px 30px -20px rgba(0,0,0,.7);
}
.navbar > .container-fluid { max-width: 1480px; gap: 6px; }
.navbar .navbar-brand { display: flex; align-items: center; padding: 0; margin-right: 18px; color: #fff !important; }
.brand { display: inline-flex; align-items: center; gap: 10px; }
.brand-mark { width: 34px; height: 34px; border-radius: 10px; display: grid; place-items: center; flex: none;
  background: linear-gradient(140deg, #38bdf8 0%, #2563eb 55%, #1e3a8a 100%);
  box-shadow: 0 6px 16px -4px rgba(37,99,235,.6), inset 0 1px 0 rgba(255,255,255,.3); }
.brand-mark svg { width: 23px; height: 23px; display: block; }
.brand-name { font-weight: 800; font-size: 1.14rem; letter-spacing: -.03em; color: #fff; }
.navbar .navbar-nav { align-items: center; gap: 2px; }
.navbar .nav-link { color: #a9b6cc !important; font-weight: 600; font-size: .92rem; padding: 7px 14px !important;
  border-radius: 999px; border: 0 !important; transition: color .2s var(--ease), background-color .2s var(--ease); }
.navbar .nav-link:hover { color: #fff !important; background: rgba(255,255,255,.07); }
.navbar .nav-link.active { color: #fff !important; background: rgba(255,255,255,.14); box-shadow: inset 0 0 0 1px rgba(255,255,255,.09); }
.navbar .nav-link::after { display: none !important; }
.navbar-toggler { border: 1px solid rgba(255,255,255,.22) !important; border-radius: 10px; }
.navbar-toggler-icon { filter: invert(1); }
@media (max-width: 991px) {
  .navbar .navbar-collapse { padding: 8px 0 4px; }
  .navbar .navbar-nav { align-items: stretch; }
  .navbar .btn-run, .navbar .btn-upd { display: none !important; }
}

/* buttons */
.btn-run, .btn-run-m {
  display: inline-flex; align-items: center; justify-content: center; gap: 8px;
  background: linear-gradient(180deg, #34d399, #10b981) !important; color: #022c22 !important; border: 0 !important;
  font-weight: 700; letter-spacing: -.01em; border-radius: 999px; padding: 8px 18px;
  box-shadow: 0 8px 20px -8px rgba(16,185,129,.75), inset 0 1px 0 rgba(255,255,255,.35);
  transition: transform .15s var(--ease), box-shadow .2s var(--ease), filter .2s; }
.btn-run:hover, .btn-run-m:hover { filter: brightness(1.06);
  box-shadow: 0 12px 26px -8px rgba(16,185,129,.85), inset 0 1px 0 rgba(255,255,255,.35); }
.btn-upd { display: inline-flex; align-items: center; gap: 8px; margin-right: 6px; border-radius: 999px; padding: 8px 16px;
  background: rgba(255,255,255,.07) !important; border: 1px solid rgba(255,255,255,.16) !important; color: #e6edf7 !important;
  font-weight: 600; transition: background-color .2s, transform .15s var(--ease); }
.btn-upd:hover { background: rgba(255,255,255,.14) !important; }
.btn-upd-m { display: inline-flex; align-items: center; justify-content: center; gap: 8px; border-radius: 999px;
  background: var(--surface) !important; border: 1px solid var(--line-2) !important; color: var(--ink) !important;
  font-weight: 650; box-shadow: var(--shadow-1); transition: transform .15s var(--ease), border-color .2s; }
.btn-upd-m:hover { border-color: var(--brand) !important; }
.btn-run:active, .btn-run-m:active, .btn-upd:active, .btn-upd-m:active { transform: scale(.96); }
.btn-run:disabled, .btn-run-m:disabled, .btn-upd:disabled, .btn-upd-m:disabled { opacity: .72; }
.btn-run:disabled::before, .btn-run-m:disabled::before, .btn-upd:disabled::before, .btn-upd-m:disabled::before {
  content: ""; width: 13px; height: 13px; border-radius: 50%; border: 2px solid currentColor; border-right-color: transparent;
  animation: bm-spin .7s linear infinite; }
.btn-primary, .btn-default, .btn-secondary, .btn-outline-secondary {
  border-radius: 999px; font-weight: 650; padding: 8px 18px; transition: transform .15s var(--ease), box-shadow .2s, background-color .2s; }
.btn-default { background: var(--surface); border: 1px solid var(--line-2); color: var(--ink); box-shadow: var(--shadow-1); }
.btn-default:hover { border-color: var(--brand); color: var(--ink); background: var(--surface); }
.btn.btn-primary { background: var(--on); border-color: var(--on); color: #fff; box-shadow: 0 8px 18px -10px var(--on); }
.btn.btn-primary:hover { background: #1d4ed8; border-color: #1d4ed8; color: #fff; }
.btn:active { transform: scale(.97); }
.run-m-wrap { display: flex; gap: 10px; margin: 0 0 12px; }
.run-m-wrap .btn { flex: 1; padding: 10px 12px; font-size: .92rem; }
@media (min-width: 992px) { .run-m-wrap { display: none; } }

.theme-btn { width: 38px; height: 38px; padding: 0; border-radius: 999px; display: inline-grid; place-items: center; flex: none;
  border: 1px solid rgba(255,255,255,.16); background: rgba(255,255,255,.07); color: #e6edf7; cursor: pointer;
  transition: background-color .2s, transform .25s var(--ease); }
.theme-btn:hover { background: rgba(255,255,255,.14); }
.theme-btn:active { transform: scale(.9) rotate(-20deg); }
.theme-btn svg { width: 18px; height: 18px; }
.theme-btn .i-sun { display: none; }
:root[data-theme="dark"] .theme-btn .i-sun { display: block; }
:root[data-theme="dark"] .theme-btn .i-moon { display: none; }

/* page frame */
.bslib-page-navbar > .container-fluid, body > .container-fluid { max-width: 1480px; margin: 0 auto; padding: 18px 20px 56px; }
.statusline { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin: 2px 0 16px; }
.schip { display: inline-flex; align-items: center; gap: 6px; min-height: 30px; padding: 4px 12px; border-radius: 999px;
  background: var(--surface); border: 1px solid var(--line); color: var(--muted); font-size: .8rem; font-weight: 500;
  box-shadow: var(--shadow-1); }
.schip b { color: var(--ink); font-weight: 700; }
.schip .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--good); box-shadow: 0 0 0 3px var(--good-soft); flex: none; }
.schip .dot.old { background: var(--warn); box-shadow: 0 0 0 3px var(--warn-soft); }
.schip.hl { background: var(--good-soft); border-color: var(--good-line); color: var(--good-ink); }
.schip.hl b { color: var(--good-ink); }
.pagehint, .hintro { color: var(--muted); font-size: .88rem; margin: 2px 2px 12px; line-height: 1.55; max-width: 920px; }

/* chips: leagues, sports */
#league .shiny-options-group, #h_sport .shiny-options-group { display: flex; flex-wrap: wrap; gap: 8px; }
#league label.radio-inline, #h_sport label.radio-inline { padding: 0; margin: 0; cursor: pointer; }
#league input[type=radio], #h_sport input[type=radio] { position: absolute; opacity: 0; pointer-events: none; }
#league label.radio-inline span, #h_sport label.radio-inline span, .chips button {
  display: inline-flex; align-items: center; height: 34px; padding: 0 15px; border-radius: 999px; cursor: pointer;
  border: 1px solid var(--line); background: var(--surface); color: var(--ink-2); font-weight: 600; font-size: .87rem;
  box-shadow: var(--shadow-1);
  transition: background-color .2s var(--ease), color .2s var(--ease), border-color .2s, transform .15s var(--ease), box-shadow .2s; }
#league label.radio-inline span:hover, #h_sport label.radio-inline span:hover, .chips button:hover {
  border-color: var(--line-2); color: var(--ink); transform: translateY(-1px); }
#league input[type=radio]:checked + span, #h_sport input[type=radio]:checked + span, .chips button.on {
  background: var(--on); border-color: var(--on); color: #fff; box-shadow: 0 6px 16px -8px var(--on); }
#league input[type=radio]:focus-visible + span, #h_sport input[type=radio]:focus-visible + span {
  outline: 2px solid var(--brand); outline-offset: 2px; }
#league { margin-bottom: 4px; }
.hfilt { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; margin: 6px 0; }
.hfilt .form-group, .hfilt .shiny-input-container { margin: 0; width: auto; }
.hfilt > div, #h_filters > div { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
#h_status { color: var(--muted); font-size: .84rem; margin: 6px 2px; }

/* ================================================================== game list */
.daygroup { display: flex; align-items: center; gap: 12px; font-size: .72rem; font-weight: 700; letter-spacing: .09em;
  text-transform: uppercase; color: var(--muted); margin: 22px 4px 10px; }
.daygroup::after { content: ""; flex: 1; height: 1px; background: var(--line); }
.daygroup:first-child { margin-top: 8px; }
.gcard { position: relative; background: var(--surface); border: 1px solid var(--line); border-radius: 18px;
  padding: 13px 16px 12px; margin-bottom: 10px; box-shadow: var(--shadow-1); cursor: pointer;
  transition: transform .22s var(--ease), box-shadow .22s var(--ease), border-color .22s; }
.gcard:hover { transform: translateY(-2px); box-shadow: var(--shadow-2); border-color: var(--line-2); }
.gcard.active { border-color: var(--brand); box-shadow: 0 0 0 3px var(--ring), var(--shadow-2); }
.gcard:active { transform: translateY(0) scale(.995); }
.gtop { display: flex; justify-content: space-between; align-items: center; gap: 8px; font-size: .78rem; color: var(--muted); }
.gtop > span:first-child { display: inline-flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.compchip { background: var(--surface-3); color: var(--ink-2); border-radius: 6px; padding: 2px 8px; font-weight: 700;
  font-size: .68rem; letter-spacing: .03em; }
.gstat { font-weight: 600; }
.live { color: var(--live) !important; font-weight: 800; display: inline-flex; align-items: center; gap: 6px; }
.live::before { content: ""; width: 7px; height: 7px; border-radius: 50%; background: var(--live); flex: none;
  animation: bm-pulse 1.6s ease-out infinite; }
.trow { display: flex; align-items: center; gap: 12px; margin-top: 9px; }
.trow img, .trow .nologo { width: 30px; height: 30px; object-fit: contain; flex: none; }
.trow .nm { font-weight: 650; flex: 1; min-width: 0; font-size: .97rem; color: var(--ink); white-space: nowrap;
  overflow: hidden; text-overflow: ellipsis; }
.trow .rec { color: var(--muted); font-size: .76rem; font-variant-numeric: tabular-nums; }
.trow .sc { display: inline-block; font-weight: 800; font-size: 1.16rem; min-width: 26px; text-align: right;
  font-variant-numeric: tabular-nums; }
.trow.lose .nm, .trow.lose .sc { color: var(--muted); font-weight: 600; }
.trow.lose img { opacity: .5; filter: grayscale(.5); }
.nologo { display: inline-grid; place-items: center; border-radius: 50%; background: var(--surface-3); color: var(--muted);
  font-size: .58rem; font-weight: 800; letter-spacing: .02em; }
.bump { animation: bm-bump .7s var(--ease); }
.gline { margin-top: 10px; padding-top: 9px; border-top: 1px solid var(--line); font-size: .81rem; color: var(--ink-2);
  display: flex; justify-content: space-between; gap: 4px 12px; flex-wrap: wrap; }
.gline b { color: var(--ink); font-weight: 650; }
.gline > span:last-child { color: var(--muted); }
.bline { margin-top: 9px; display: flex; align-items: center; gap: 6px 8px; flex-wrap: wrap; font-size: .83rem;
  color: var(--ink-2); background: var(--surface-2); border: 1px solid var(--line); border-radius: 12px; padding: 7px 10px; }
.bline .bb { font-weight: 700; color: var(--ink); }
.bline .bm { color: var(--muted); }
.bline .bres { margin-left: auto; }
.vbadge { background: var(--good-soft); color: var(--good-ink); border: 1px solid var(--good-line); border-radius: 999px;
  padding: 1px 9px; font-weight: 800; font-size: .68rem; letter-spacing: .03em; white-space: nowrap; }
.kchip { display: inline-flex; align-items: center; border-radius: 999px; padding: 2px 9px; font-weight: 800; font-size: .64rem;
  letter-spacing: .06em; white-space: nowrap; border: 1px solid transparent; line-height: 1.5; }
.kchip.rec { background: var(--good-soft); color: var(--good-ink); border-color: var(--good-line); }
.kchip.lean { background: var(--lean-soft); color: var(--lean-ink); border-color: var(--lean-line); }
.kchip.neg { background: var(--surface-3); color: var(--ink-2); border-color: var(--line); }
.pmark { margin-left: 2px; }

/* ================================================================== game page */
.detail { background: var(--surface); border: 1px solid var(--line); border-radius: 22px; padding: 22px 22px 18px;
  box-shadow: var(--shadow-2); animation: bm-in .32s var(--ease) backwards; }
@media (min-width: 992px) {
  .detail { position: sticky; top: var(--sticky-top); max-height: calc(100vh - var(--sticky-top) - 16px);
    overflow-y: auto; overscroll-behavior: contain; }
  .mobile-only { display: none !important; }
}
.backlink { display: inline-flex; align-items: center; gap: 6px; color: var(--brand); font-weight: 700; cursor: pointer;
  font-size: .98rem; margin-bottom: 14px; padding: 7px 14px 7px 10px; border-radius: 999px; background: var(--brand-soft); }
@media (max-width: 991px) {
  #game_detail { display: none; }
  body.show-detail #game_detail { display: block; position: fixed; inset: 0; z-index: 1040; overflow-y: auto;
    overscroll-behavior: contain; background: var(--bg); padding: 10px 10px 40px; animation: bm-slide .32s var(--ease) backwards; }
  .gcard.active { border-color: var(--line); box-shadow: var(--shadow-1); }  /* no side-by-side page on phones */
  body.show-detail { overflow: hidden; }
  body.show-detail .backlink { position: sticky; top: 0; z-index: 5; background: var(--surface); border: 1px solid var(--line);
    box-shadow: var(--shadow-2); }
}
.dhead { display: grid; grid-template-columns: 1fr auto 1fr; align-items: center; gap: 12px; }
.hteam { text-align: center; min-width: 0; }
.hteam img, .hteam .nologo { width: 70px; height: 70px; object-fit: contain; filter: drop-shadow(0 6px 12px rgba(15,23,42,.16)); }
.hteam .nologo { font-size: .9rem; filter: none; }
.hteam .big { font-weight: 800; font-size: 1.12rem; margin-top: 6px; letter-spacing: -.015em; }
.hteam .rec { color: var(--muted); font-size: .82rem; }
.hmid { text-align: center; min-width: 120px; }
.hmid .score { font-size: 2.35rem; font-weight: 800; letter-spacing: -.03em; line-height: 1.1; font-variant-numeric: tabular-nums; }
.hmid .when { color: var(--muted); font-size: .8rem; margin-top: 3px; }
.headline { position: relative; overflow: hidden; margin: 18px 0 14px; padding: 16px 18px; border-radius: 18px;
  background: linear-gradient(135deg, var(--hero-1), var(--hero-2)); color: var(--hero-ink);
  box-shadow: 0 16px 34px -18px rgba(15,35,90,.7); }
.headline::after { content: ""; position: absolute; right: -60px; bottom: -90px; width: 260px; height: 260px;
  background: radial-gradient(closest-side, rgba(56,189,248,.28), transparent); pointer-events: none; }
.headline > * { position: relative; z-index: 1; }
.headline .row2 { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 14px 18px; }
.headline .k { color: var(--hero-muted); font-size: .68rem; font-weight: 700; text-transform: uppercase; letter-spacing: .08em; }
.headline .v { font-size: 1.2rem; font-weight: 800; margin-top: 3px; letter-spacing: -.015em; font-variant-numeric: tabular-nums; }
.headline .vp { display: inline-block; margin-left: 7px; padding: 1px 8px; border-radius: 999px; vertical-align: 3px;
  background: rgba(255,255,255,.12); color: var(--hero-sub); font-size: .7rem; font-weight: 700; letter-spacing: 0; }
.headline .sub { color: var(--hero-sub); margin-top: 10px; font-size: .87rem; line-height: 1.5; }
.verdict { background: rgba(255,255,255,.08); border: 1px solid rgba(255,255,255,.12); border-radius: 12px;
  padding: 9px 12px; margin-top: 12px; font-size: .87rem; }
.reco { position: relative; overflow: hidden; margin-top: 14px; padding: 14px 16px 12px 20px; border-radius: 16px;
  background: var(--good-soft); border: 1px solid var(--good-line); }
.reco::before { content: ""; position: absolute; left: 0; top: 0; bottom: 0; width: 4px; background: var(--good); }
.reco .b { font-size: 1.3rem; font-weight: 800; letter-spacing: -.015em; margin: 3px 0 4px; color: var(--ink); }
.reco.lean-box.lean { background: var(--lean-soft); border-color: var(--lean-line); }
.reco.lean-box.lean::before { background: var(--lean); }
.reco.lean-box.neg { background: var(--surface-2); border-color: var(--line); }
.reco.lean-box.neg::before { background: var(--line-2); }
.sect { margin-top: 22px; overflow-x: auto; }
.sect h6, .hcard h6 { font-weight: 700; text-transform: uppercase; letter-spacing: .08em; font-size: .7rem; color: var(--muted);
  margin-bottom: 10px; }
.barlabel { font-size: .75rem; color: var(--muted); font-weight: 600; margin: 10px 0 5px; }
.bar { display: flex; gap: 3px; height: 30px; border-radius: 10px; overflow: hidden; font-size: .76rem; font-weight: 700;
  color: #fff; animation: bm-reveal .8s var(--ease) both; }
.bar div { display: flex; align-items: center; justify-content: center; white-space: nowrap; overflow: hidden; min-width: 0; }
.mbar { display: flex; align-items: center; gap: 10px; font-size: .84rem; margin: 6px 0; font-variant-numeric: tabular-nums; }
.mbar .lbl { width: 120px; color: var(--ink-2); flex: none; }
.mbar .fill { height: 12px; border-radius: 999px; background: linear-gradient(90deg, var(--brand-2), var(--brand));
  transform-origin: left center; animation: bm-grow .8s var(--ease) both; }
table.bk { width: 100%; border-collapse: separate; border-spacing: 0; font-size: .86rem; font-variant-numeric: tabular-nums; }
table.bk th { text-align: left; color: var(--muted); font-weight: 700; font-size: .68rem; text-transform: uppercase;
  letter-spacing: .07em; background: var(--surface-2); border-bottom: 1px solid var(--line); padding: 8px 10px; white-space: nowrap; }
table.bk th:first-child { border-top-left-radius: 10px; }
table.bk th:last-child { border-top-right-radius: 10px; }
table.bk td { border-bottom: 1px solid var(--line); padding: 9px 10px; vertical-align: top; color: var(--ink); background: transparent;
  transition: background-color .15s; }
table.bk tbody tr:last-child td { border-bottom: 0; }
table.bk tbody tr:hover td { background: var(--surface-2); }
table.bk td small { color: var(--muted); display: block; font-size: .76rem; margin-top: 1px; }
.ev-pos { color: var(--good-ink); font-weight: 800; }
.ev-neg { color: var(--muted); }
.inj { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.inj > div { background: var(--surface-2); border: 1px solid var(--line); border-radius: 14px; padding: 12px 14px; min-width: 0; }
.injrow { font-size: .83rem; padding: 5px 0; border-bottom: 1px solid var(--line); color: var(--ink-2); }
.inj > div > .injrow:last-child { border-bottom: 0; }
.chip { display: inline-block; border-radius: 6px; padding: 0 7px; font-size: .64rem; font-weight: 800; margin-right: 6px;
  letter-spacing: .04em; text-transform: uppercase; vertical-align: 1px; }
.chip.Out, .chip.out { background: var(--bad-soft); color: var(--bad-ink); }
.chip.Doubtful { background: var(--orange-soft); color: var(--orange-ink); }
.chip.Questionable, .chip.DNP, .chip.Limited, .chip.doubt { background: var(--warn-soft); color: var(--warn-ink); }
.note { color: var(--muted); font-size: .8rem; margin-top: 12px; line-height: 1.5; }
.why { margin-top: 8px; font-size: .86rem; color: var(--ink-2); line-height: 1.5; }
.scores { display: flex; flex-wrap: wrap; gap: 6px; }
.scores span { background: var(--surface-2); border: 1px solid var(--line); border-radius: 999px; padding: 4px 10px;
  font-size: .82rem; font-variant-numeric: tabular-nums; }
.empty { background: var(--surface); border: 1px dashed var(--line-2); border-radius: 20px; padding: 38px 24px;
  text-align: center; color: var(--muted); }
.empty h4 { color: var(--ink); font-weight: 800; }

.panel { background: var(--surface); border: 1px solid var(--line); border-radius: 20px; padding: 16px 18px 14px;
  box-shadow: var(--shadow-1); }

/* ================================================================== bets */
.bm-cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 14px; margin-bottom: 6px; }
.bm-card { position: relative; overflow: hidden; background: var(--surface); border: 1px solid var(--line); border-radius: 20px;
  padding: 18px 18px 16px; box-shadow: var(--shadow-1); cursor: pointer;
  transition: transform .22s var(--ease), box-shadow .22s var(--ease), border-color .22s; }
.bm-card::before { content: ""; position: absolute; inset: 0 0 auto 0; height: 4px; background: linear-gradient(90deg, var(--brand-2), var(--brand)); }
.bm-card.soccer::before { background: linear-gradient(90deg, #34d399, #059669); }
.bm-card:hover { transform: translateY(-3px); box-shadow: var(--shadow-3); border-color: var(--line-2); }
.bm-card .bet { font-size: 1.42rem; font-weight: 800; margin: 6px 0 2px; letter-spacing: -.02em; }
.bm-card .stake { display: inline-flex; margin-top: 10px; background: var(--ink); color: var(--surface); border-radius: 999px;
  padding: 4px 12px; font-weight: 700; font-size: .84rem; font-variant-numeric: tabular-nums; }
.badge-new { background: #fde68a; color: #78350f; border-radius: 999px; padding: 1px 9px; font-weight: 800; font-size: .68rem; letter-spacing: .04em; }
.badge-old { background: var(--surface-3); color: var(--ink-2); border-radius: 999px; padding: 1px 9px; font-size: .68rem; font-weight: 600; }
table.allbets tr { cursor: pointer; }
table.allbets td .kchip { white-space: nowrap; }
table.allbets td small.whys { display: block; color: var(--muted); max-width: 440px; white-space: normal; line-height: 1.45; margin-top: 3px; }
.nowcell { white-space: nowrap; }

/* ================================================================== results */
.rhead h4 { font-weight: 800; letter-spacing: -.02em; margin: 4px 0 4px; }
.rkpis { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; margin: 14px 0 8px; }
.rkpi, .kpi { background: var(--surface); border: 1px solid var(--line); border-radius: 16px; padding: 14px 16px; box-shadow: var(--shadow-1); }
.rkpi .k, .kpi .k { color: var(--muted); font-size: .68rem; font-weight: 700; text-transform: uppercase; letter-spacing: .08em; }
.rkpi .v, .kpi .v { font-size: 1.42rem; font-weight: 800; letter-spacing: -.02em; margin-top: 4px; font-variant-numeric: tabular-nums; }
.rkpi .s, .kpi .s { color: var(--muted); font-size: .8rem; margin-top: 2px; }
.rkpi.pos .v { color: var(--good-ink); } .rkpi.neg .v { color: var(--bad-ink); }
.rsec { background: var(--surface); border: 1px solid var(--line); border-radius: 20px; padding: 16px 18px; margin-top: 16px; box-shadow: var(--shadow-1); }
.shead { display: flex; justify-content: space-between; align-items: baseline; flex-wrap: wrap; gap: 8px;
  border-bottom: 1px solid var(--line); padding-bottom: 10px; }
.shead h4 { font-weight: 800; margin: 0; letter-spacing: -.02em; }
.srec { display: flex; gap: 14px; align-items: baseline; flex-wrap: wrap; color: var(--ink-2); font-size: .88rem; }
.srec .big { font-size: 1.35rem; font-weight: 800; color: var(--ink); font-variant-numeric: tabular-nums; }
.srec .pos { color: var(--good-ink); font-weight: 800; } .srec .neg { color: var(--bad-ink); font-weight: 800; }
.kinds { display: flex; gap: 10px 18px; flex-wrap: wrap; margin: 12px 0 4px; font-size: .86rem; }
.kindrow { display: inline-flex; align-items: center; gap: 6px; }
.kindrow .ku { color: var(--muted); }
table.rtable tr.won td { background: var(--good-row); }
table.rtable tr.lost td { background: var(--bad-row); }
table.rtable tbody tr.won:hover td { background: var(--good-row-h); }
table.rtable tbody tr.lost:hover td { background: var(--bad-row-h); }
table.rtable tr.won td:first-child { box-shadow: inset 3px 0 0 var(--good); }
table.rtable tr.lost td:first-child { box-shadow: inset 3px 0 0 var(--bad); }
table.rtable tr.push td:first-child { box-shadow: inset 3px 0 0 var(--line-2); }
table.rtable td small { display: block; color: var(--muted); }
.res { display: inline-block; font-weight: 800; font-size: .68rem; letter-spacing: .05em; border-radius: 999px; padding: 2px 9px; line-height: 1.5; }
.res.won { background: var(--hit); color: #fff; } .res.lost { background: var(--miss); color: #fff; }
.res.push { background: var(--surface-3); color: var(--ink-2); }
.pok { color: var(--good-ink); font-weight: 700; } .pbad { color: var(--bad-ink); font-weight: 700; }

/* ================================================================== model history */
.hkpis { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; margin: 14px 0; }
@media (max-width: 800px) { .hkpis { grid-template-columns: repeat(2, 1fr); } }
@media (max-width: 480px) { .hkpis { grid-template-columns: 1fr; } }
.hkpis .kpi .v { font-size: 1.18rem; }
.hcharts { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
@media (max-width: 900px) { .hcharts { grid-template-columns: 1fr; } }
.hcard { background: var(--surface); border: 1px solid var(--line); border-radius: 18px; padding: 16px; box-shadow: var(--shadow-1); margin-bottom: 14px; }
.hcard table.bk small { display: block; color: var(--muted); }
.lgd { display: flex; gap: 14px; font-size: .8rem; color: var(--ink-2); margin-bottom: 4px; }
.lgd i { display: inline-block; width: 10px; height: 10px; border-radius: 50%; margin-right: 5px; }
.lgd i.m { background: var(--c-model) !important; } .lgd i.b { background: var(--c-books) !important; }
.hcard svg .gl { stroke: var(--line); } .hcard svg .ax { fill: var(--muted); } .hcard svg .axl { fill: var(--ink-2); }
.hcard svg .ref, .hcard svg .zero { stroke: var(--line-2); }
.hcard svg .s-model { stroke: var(--c-model); } .hcard svg .f-model { fill: var(--c-model); }
.hcard svg .s-books { stroke: var(--c-books); } .hcard svg .f-books { fill: var(--c-books); }
.hcard svg .halo { stroke: var(--surface); }

/* ================================================================== tracker + settings (bslib / Bootstrap pieces) */
.bslib-value-box, .card { --bs-card-bg: var(--surface); --bs-card-border-color: var(--line); --bs-card-cap-bg: transparent;
  --bs-card-border-radius: 18px; --bs-card-inner-border-radius: 17px; background: var(--surface); border: 1px solid var(--line);
  border-radius: 18px; box-shadow: var(--shadow-1); color: var(--ink); }
.bslib-value-box .value-box-title { color: var(--muted); font-size: .7rem !important; font-weight: 700; text-transform: uppercase; letter-spacing: .08em; }
.bslib-value-box .value-box-value { font-size: 1.5rem !important; font-weight: 800; letter-spacing: -.02em; font-variant-numeric: tabular-nums; color: var(--ink); }
.card-header { font-weight: 700; border-bottom: 1px solid var(--line); background: transparent; }
.form-control, .form-select { border-radius: 10px; border-color: var(--line-2); background-color: var(--surface); color: var(--ink);
  transition: border-color .15s, box-shadow .15s; }
.form-control:focus, .form-select:focus { border-color: var(--brand); box-shadow: 0 0 0 3px var(--ring); background-color: var(--surface); color: var(--ink); }
.form-check-input:checked { background-color: var(--on); border-color: var(--on); }
.control-label, .form-label { font-weight: 600; font-size: .86rem; color: var(--ink-2); }
.shiny-data-grid { border: 1px solid var(--line); border-radius: 14px; overflow: hidden; background: var(--surface); }
.shiny-data-grid > table, .shiny-data-grid table { color: var(--ink); }
.shiny-data-grid thead th { background: var(--surface-2) !important; color: var(--muted) !important; font-size: .72rem;
  text-transform: uppercase; letter-spacing: .05em; }
.shiny-data-grid tbody td { border-color: var(--line) !important; }

/* notifications + progress */
#shiny-notification-panel { width: 380px; max-width: calc(100vw - 24px); bottom: 16px; right: 16px; }
.shiny-notification { background: var(--surface) !important; color: var(--ink) !important; border: 1px solid var(--line) !important;
  border-radius: 16px !important; box-shadow: var(--shadow-3) !important; padding: 14px 16px !important; opacity: 1 !important;
  font-size: .9rem; animation: bm-toast .38s var(--ease) both; }
.shiny-notification-error { border-color: var(--bad-line) !important; }
.shiny-notification-error::before { content: ""; position: absolute; left: 0; top: 12px; bottom: 12px; width: 3px; border-radius: 3px; background: var(--bad); }
.shiny-notification .progress { height: 8px; border-radius: 999px; background: var(--surface-3); margin: 4px 0 8px; overflow: hidden; }
.shiny-notification .progress-bar { background: linear-gradient(90deg, #38bdf8, #2563eb) !important; border-radius: 999px;
  transition: width .45s var(--ease); }
.shiny-notification .progress-message { font-weight: 600; }
.shiny-notification-close { color: var(--muted) !important; }

/* ================================================================== phone page (static snapshot) */
.appbar { position: sticky; top: 0; z-index: 1030; background: rgba(8,13,27,.86); border-bottom: 1px solid rgba(148,163,184,.12);
  backdrop-filter: saturate(170%) blur(16px); -webkit-backdrop-filter: saturate(170%) blur(16px);
  box-shadow: 0 12px 30px -20px rgba(0,0,0,.7); }
.topbar { max-width: 1280px; margin: 0 auto; padding: 10px 14px 2px; display: flex; align-items: center;
  gap: 6px 12px; flex-wrap: wrap; }
.topbar .runinfo { margin-left: auto; color: #a9b6cc; font-size: .76rem; text-align: right; line-height: 1.4; }
@media (max-width: 600px) {
  .topbar .theme-btn { margin-left: auto; }
  .topbar .runinfo { order: 3; flex-basis: 100%; margin-left: 0; text-align: left; font-size: .72rem; }
  .topbar .runinfo div { display: inline; }
  .topbar .runinfo div + div::before { content: " · "; }
}
.tabs { max-width: 1280px; margin: 0 auto; display: flex; gap: 4px; padding: 6px 28px 10px 10px; overflow-x: auto;
  scrollbar-width: none; -webkit-overflow-scrolling: touch;
  -webkit-mask-image: linear-gradient(90deg, #000 calc(100% - 28px), transparent);
  mask-image: linear-gradient(90deg, #000 calc(100% - 28px), transparent); }
.tabs::-webkit-scrollbar { display: none; }
.tabs button { flex: none; border: 0; background: transparent; color: #a9b6cc; font-weight: 600; font-size: .9rem; padding: 7px 14px;
  border-radius: 999px; cursor: pointer; white-space: nowrap; transition: color .2s var(--ease), background-color .2s var(--ease); }
.tabs button:hover { color: #fff; background: rgba(255,255,255,.07); }
.tabs button.on { color: #fff; background: rgba(255,255,255,.14); box-shadow: inset 0 0 0 1px rgba(255,255,255,.09); }
.tabs .count { display: inline-grid; place-items: center; min-width: 20px; height: 20px; margin-left: 6px; padding: 0 6px;
  border-radius: 999px; background: #10b981; color: #022c22; font-size: .72rem; font-weight: 800; }
.wrap { max-width: 1280px; margin: 0 auto; padding: 0 14px 48px; }
.chips { display: flex; flex-wrap: wrap; gap: 8px; margin: 14px 0 6px; }
.layout { display: grid; grid-template-columns: 5fr 7fr; gap: 20px; align-items: stretch; }
.layout > * { min-width: 0; }
@media (min-width: 992px) { .snap #game_detail .dsnap { height: 100%; } }
@media (max-width: 991px) { .layout { grid-template-columns: 1fr; } }
.sec { display: none; }
.sec.on { display: block; }
.stale { display: flex; gap: 10px; align-items: flex-start; background: var(--warn-soft); border: 1px solid var(--warn-line);
  color: var(--warn-ink); border-radius: 14px; padding: 10px 14px; font-size: .82rem; margin-top: 14px; line-height: 1.5; }
.kpis { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin: 14px 0; }
@media (max-width: 600px) { .kpis { grid-template-columns: repeat(2, 1fr); } }
body.snap { --sticky-top: 122px; }

/* phone page: Run on GitHub (button, progress card, key sheet, "new results" pill) */
.run-btn { display: inline-flex; align-items: center; gap: 7px; height: 38px; padding: 0 15px 0 12px; border: 0;
  border-radius: 999px; flex: none; cursor: pointer; font: inherit; font-weight: 750; font-size: .9rem; letter-spacing: -.01em;
  color: #022c22; background: linear-gradient(180deg, #34d399, #10b981);
  box-shadow: 0 8px 20px -8px rgba(16,185,129,.75), inset 0 1px 0 rgba(255,255,255,.35);
  transition: transform .15s var(--ease), filter .2s; }
.run-btn:hover { filter: brightness(1.06); }
.run-btn:active { transform: scale(.95); }
.run-btn svg { width: 13px; height: 13px; display: block; }
.run-btn.busy svg { display: none; }
.run-btn.busy::before { content: ""; width: 13px; height: 13px; border-radius: 50%; border: 2px solid currentColor;
  border-right-color: transparent; animation: bm-spin .7s linear infinite; }
@media (max-width: 600px) { .topbar .run-btn { margin-left: auto; } .topbar .run-btn ~ .theme-btn { margin-left: 0; } }
.oscard { position: fixed; left: 50%; bottom: calc(14px + env(safe-area-inset-bottom)); z-index: 1060; transform: translateX(-50%);
  width: min(520px, calc(100% - 24px)); padding: 12px 14px 13px; border-radius: 16px; color: #e6edf7;
  background: rgba(11,19,36,.95); border: 1px solid rgba(148,163,184,.22); box-shadow: var(--shadow-3);
  backdrop-filter: blur(14px); -webkit-backdrop-filter: blur(14px); animation: bm-pop .32s var(--ease) backwards; }
.oscard[hidden], .osnew[hidden] { display: none; }
.osrow { display: flex; align-items: center; gap: 10px; }
.osrow b { font-weight: 700; font-size: .92rem; color: #fff; }
.ostime { margin-left: auto; color: #a9b6cc; font-size: .8rem; font-variant-numeric: tabular-nums; }
.osx { border: 0; background: transparent; color: #a9b6cc; font-size: 1.3rem; line-height: 1; padding: 0 2px; cursor: pointer; }
.osx:hover { color: #fff; }
.osmsg { color: #c7d3e6; font-size: .84rem; margin-top: 3px; }
.osmsg a { color: #93c5fd; }
.osprog { height: 5px; border-radius: 999px; background: rgba(255,255,255,.12); overflow: hidden; margin-top: 10px; }
.osprog i { display: block; height: 100%; width: 3%; border-radius: 999px; background: linear-gradient(90deg, #38bdf8, #10b981);
  transition: width .8s var(--ease); }
.oscard.err .osprog i { background: var(--bad); }
.oscard.err .osrow b { color: #fecaca; }
.osnew { position: fixed; left: 50%; bottom: calc(14px + env(safe-area-inset-bottom)); z-index: 1055; transform: translateX(-50%);
  border: 0; border-radius: 999px; padding: 10px 18px; font: inherit; font-size: .88rem; color: #fff; cursor: pointer;
  white-space: nowrap; background: var(--brand); box-shadow: 0 12px 28px -10px rgba(37,99,235,.8);
  animation: bm-pop .32s var(--ease) backwards; }
.osbg { position: fixed; inset: 0; z-index: 1070; background: rgba(2,6,23,.55); animation: bm-fade .2s ease backwards; }
.ossheet { position: fixed; left: 0; right: 0; bottom: 0; z-index: 1071; max-width: 560px; max-height: 88vh; overflow-y: auto;
  margin: 0 auto; padding: 20px 18px calc(18px + env(safe-area-inset-bottom)); border-radius: 22px 22px 0 0;
  background: var(--surface); color: var(--ink); box-shadow: var(--shadow-3); animation: bm-up .3s var(--ease) backwards; }
.ossheet h4 { font-size: 1.08rem; font-weight: 750; margin: 0 0 8px; }
.ossheet ol { margin: 10px 0 14px; padding-left: 20px; font-size: .88rem; line-height: 1.6; color: var(--ink-2); }
.ossheet a { color: var(--brand); }
.ossheet input { width: 100%; padding: 11px 13px; border-radius: 12px; border: 1px solid var(--line-2); background: var(--surface-2);
  color: var(--ink); font: inherit; font-size: .9rem; }
.ossheet input:focus { outline: 2px solid var(--brand); outline-offset: 1px; }
.osbtns { display: flex; gap: 10px; margin-top: 12px; flex-wrap: wrap; }
.osbtns .btn { flex: 1 1 180px; }
.oserr { min-height: 1.2em; margin-top: 6px; font-size: .82rem; color: var(--bad-ink); }
.ossheet .small { font-size: .76rem; margin-top: 12px; }
.stale .oslink { background: none; border: 0; padding: 0; font: inherit; font-weight: 650; color: inherit;
  text-decoration: underline; cursor: pointer; }
@keyframes bm-pop { from { opacity: 0; transform: translate(-50%, 14px) scale(.98); } to { opacity: 1; transform: translate(-50%, 0); } }
@keyframes bm-up { from { transform: translateY(100%); } to { transform: none; } }
@keyframes bm-fade { from { opacity: 0; } to { opacity: 1; } }

/* ================================================================== small screens */
@media (max-width: 600px) {
  .bslib-page-navbar > .container-fluid, body > .container-fluid { padding: 12px 10px 48px; }
  .detail { padding: 16px 14px; border-radius: 18px; }
  .hteam img, .hteam .nologo { width: 50px; height: 50px; }
  .hteam .big { font-size: .98rem; }
  .hmid { min-width: 84px; } .hmid .score { font-size: 1.7rem; }
  .headline { padding: 14px; } .headline .v { font-size: 1.05rem; } .headline .row2 { grid-template-columns: 1fr 1fr; gap: 12px; }
  table.bk { font-size: .8rem; } table.bk td, table.bk th { padding: 7px 6px; }
  .mbar .lbl { width: 92px; }
  .inj { grid-template-columns: 1fr; }
  .gcard { padding: 12px 13px 11px; }
  .bm-cards { grid-template-columns: 1fr; }
  .rkpis { grid-template-columns: 1fr 1fr; gap: 10px; }
  .rkpi, .kpi { padding: 12px 13px; border-radius: 14px; }
  .rkpi .v, .kpi .v { font-size: 1.12rem; }
  .rkpi .s, .kpi .s { font-size: .74rem; }
  .rsec, .panel { padding: 14px 12px; border-radius: 16px; }
  .shead h4 { font-size: 1.2rem; }
  /* wide tables scroll sideways inside their card instead of squeezing every cell */
  table.rtable { min-width: 680px; } table.allbets { min-width: 780px; } .hcard table.bk { min-width: 640px; }
  table.rtable td:nth-child(-n+3), table.allbets td:nth-child(-n+2), .hcard table.bk td:nth-child(-n+2) { white-space: nowrap; }
}
"""
