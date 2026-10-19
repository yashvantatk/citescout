"""CiteScout design system: CSS + small HTML components for Streamlit.

Design rules (after the Impeccable design guidelines): one editorial serif for display, one clean sans
for UI, tabular numbers, a warm paper background, colour only where it carries meaning
(teal = verified/cited, amber = paywalled, rose = flagged), no nested cards, no gradients-for-decoration,
short purposeful motion (fade-up on arrival, bars that fill), all of it off under prefers-reduced-motion.
"""
from __future__ import annotations

import html
from typing import Any

import streamlit as st

CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Instrument+Serif:ital@0;1&family=Geist:wght@400;500;600&family=Geist+Mono:wght@400;500&display=swap');

:root{
  --bg:#FBFAF7; --surface:#FFFFFF; --sunk:#F3F1EB; --line:#E6E2D8; --line-strong:#D6D1C4;
  --ink:#1B1F2A; --ink-2:#4A5060; --ink-3:#7A8090;
  --teal:#0F766E; --teal-soft:#E3F2EF; --amber:#B45309; --amber-soft:#FBF0DD;
  --rose:#BE3A34; --rose-soft:#FBE6E3; --indigo:#3B4BA8; --indigo-soft:#E8EBF8;
  --r:14px; --ease:cubic-bezier(.2,.7,.2,1);
}
html, body, [class*="css"], .stApp, .stMarkdown, button, input, textarea, select{
  font-family:'Geist', ui-sans-serif, system-ui, sans-serif !important;
}
.stApp{background:var(--bg); color:var(--ink);}
code, pre, .mono{font-family:'Geist Mono', ui-monospace, monospace !important;}
[data-testid="stHeader"]{background:transparent;}
#MainMenu, footer, [data-testid="stDecoration"]{visibility:hidden; height:0;}
.block-container{padding-top:4.2rem; max-width:1120px;}
h1,h2,h3{letter-spacing:-.02em; text-wrap:balance;}
p{text-wrap:pretty;}

/* ---------- motion ---------- */
@keyframes cs-up{from{opacity:0; transform:translateY(8px)} to{opacity:1; transform:none}}
@keyframes cs-fill{from{width:0}}
@keyframes cs-pulse{0%,100%{opacity:1; transform:scale(1)} 50%{opacity:.35; transform:scale(.8)}}
.anim{animation:cs-up .42s var(--ease) both;}
/* count-up numbers: pure CSS, the counter animates from 0 to --n */
@property --n{syntax:'\\3C integer\\3E '; initial-value:0; inherits:false;}
@property --i{syntax:'\\3C integer\\3E '; initial-value:0; inherits:false;}
@property --d{syntax:'\\3C integer\\3E '; initial-value:0; inherits:false;}
@keyframes cs-count{from{--n:0}}
.cnt{animation:cs-count 1.1s cubic-bezier(.16,1,.3,1) both; counter-reset:n var(--n); font-variant-numeric:tabular-nums;}
.cnt::before{content:counter(n);}
.cnt.dec{--i:calc((var(--n) - 5) / 10); --d:calc(var(--n) - var(--i) * 10); counter-reset:i var(--i) d var(--d);}
.cnt.dec::before{content:counter(i) "." counter(d);}
@media (prefers-reduced-motion: reduce){ .anim, .bar i, .dot.live, .cnt{animation:none !important;} }

/* ---------- hero ---------- */
.eyebrow{font:500 12px/1 'Geist Mono',monospace; letter-spacing:.08em; text-transform:uppercase; color:var(--teal);}
.hero h1{font-family:'Instrument Serif',serif !important; font-weight:400; font-size:clamp(38px,5vw,58px);
  line-height:1.02; letter-spacing:-.025em; margin:.35rem 0 .6rem; color:var(--ink);}
.hero h1 em{font-style:italic; color:var(--teal);}
.hero h1 .ai{font-family:'Geist Mono',monospace; font-size:.28em; font-weight:500; letter-spacing:.04em; vertical-align:super;
  margin-left:.25em; padding:.18em .45em; border-radius:8px; background:var(--teal-soft); color:var(--teal);}
.hero .tagline{font-family:'Instrument Serif',serif; font-size:clamp(22px,2.6vw,30px); line-height:1.15; color:var(--ink-2); margin:-.1rem 0 .8rem;}
.hero .tagline em{color:var(--teal);}
.brand{font-weight:600; font-size:15px; letter-spacing:-.01em; color:var(--ink); display:flex; align-items:center; gap:8px;}
.brand span{font:500 11px/1 'Geist Mono',monospace; color:var(--teal);}
.brand .mark{font-family:'Instrument Serif',serif; font-size:17px; width:26px; height:26px; border-radius:8px; display:inline-grid;
  place-items:center; background:var(--ink); color:var(--bg);}
.hero p.lede{font-size:17px; color:var(--ink-2); max-width:640px; margin:0;}
.page-title{font-family:'Instrument Serif',serif !important; font-weight:400; font-size:40px; letter-spacing:-.02em;
  margin:.2rem 0 .3rem; line-height:1.05;}
.page-sub{color:var(--ink-2); font-size:15.5px; margin:0 0 1.2rem; max-width:680px;}

/* ---------- stepper ---------- */
.steps{display:grid; grid-template-columns:repeat(4,1fr); gap:10px; margin:1.6rem 0 1.4rem;}
.step{border-top:2px solid var(--line); padding-top:10px; transition:border-color .3s var(--ease);}
.step .n{font:500 11px/1 'Geist Mono',monospace; color:var(--ink-3);}
.step .t{font-weight:600; font-size:14px; margin-top:6px; color:var(--ink-3);}
.step .d{font-size:12.5px; color:var(--ink-3); margin-top:2px;}
.step.done{border-color:var(--teal);} .step.done .t{color:var(--ink);}
.step.now{border-color:var(--ink);} .step.now .t{color:var(--ink);} .step.now .n{color:var(--ink);}
@media (max-width:720px){.steps{grid-template-columns:repeat(2,1fr);}}

/* ---------- chips / badges ---------- */
.chips{display:flex; flex-wrap:wrap; gap:6px; align-items:center;}
.chip{display:inline-flex; align-items:center; gap:6px; font-size:12px; font-weight:500; line-height:1;
  padding:5px 9px; border-radius:999px; background:var(--sunk); color:var(--ink-2); white-space:nowrap;}
.chip.teal{background:var(--teal-soft); color:var(--teal);} .chip.amber{background:var(--amber-soft); color:var(--amber);}
.chip.rose{background:var(--rose-soft); color:var(--rose);} .chip.indigo{background:var(--indigo-soft); color:var(--indigo);}
.chip.ghost{background:transparent; border:1px solid var(--line); }
.dot{width:7px; height:7px; border-radius:50%; background:currentColor; display:inline-block;}
.dot.live{animation:cs-pulse 1.2s ease-in-out infinite;}

/* ---------- stat tiles ---------- */
.stats{display:grid; grid-template-columns:repeat(4,1fr); gap:12px; margin:.4rem 0 1rem;}
.stat{background:var(--surface); border:1px solid var(--line); border-radius:var(--r); padding:16px 18px;}
.stat .k{font-size:12.5px; color:var(--ink-3); font-weight:500;}
.stat .v{font:500 34px/1.1 'Geist',sans-serif; font-variant-numeric:tabular-nums; letter-spacing:-.02em; margin-top:6px;}
.stat .h{font-size:12px; color:var(--ink-3); margin-top:4px;}
.stat.teal .v{color:var(--teal);} .stat.amber .v{color:var(--amber);} .stat.rose .v{color:var(--rose);}
@media (max-width:720px){.stats{grid-template-columns:repeat(2,1fr);}}

/* ---------- paper card ---------- */
.paper{background:var(--surface); border:1px solid var(--line); border-radius:var(--r); padding:18px 20px;
  margin-bottom:10px; display:grid; grid-template-columns:34px 1fr 150px; gap:14px;
  transition:border-color .2s var(--ease), box-shadow .2s var(--ease), transform .2s var(--ease);}
.paper:hover{border-color:var(--line-strong); box-shadow:0 6px 24px -14px rgba(27,31,42,.25); transform:translateY(-1px);}
.paper .rank{font:500 13px/1 'Geist Mono',monospace; color:var(--ink-3); padding-top:4px;}
.paper .ttl{font-weight:600; font-size:16px; line-height:1.35; color:var(--ink); text-decoration:none;}
.paper .ttl:hover{color:var(--teal);}
.paper .meta{font-size:13px; color:var(--ink-3); margin:4px 0 9px;}
.paper .why{font-size:14px; color:var(--ink-2); margin:10px 0 0; line-height:1.5;}
.paper .quote{font-size:13px; color:var(--ink-2); margin-top:8px; padding-left:10px; border-left:2px solid var(--teal-soft);}
.paper .quote b{font:500 10.5px/1 'Geist Mono',monospace; letter-spacing:.06em; text-transform:uppercase; color:var(--ink-3); display:block; margin-bottom:4px;}
.score .big{font:500 30px/1 'Geist',sans-serif; font-variant-numeric:tabular-nums; letter-spacing:-.02em;}
.score .big small{font-size:13px; color:var(--ink-3); font-weight:400;}
.score .row{display:grid; grid-template-columns:52px 1fr 24px; gap:6px; align-items:center; font-size:11.5px; color:var(--ink-3); margin-top:6px;}
.score .row span:last-child{text-align:right; font-variant-numeric:tabular-nums; color:var(--ink-2);}
.bar{height:5px; background:var(--sunk); border-radius:9px; overflow:hidden;}
.bar i{display:block; height:100%; width:var(--w); background:var(--teal); border-radius:9px; animation:cs-fill .9s var(--ease) both;}
.bar.amber i{background:var(--amber);} .bar.rose i{background:var(--rose);} .bar.ink i{background:var(--ink-2);}
@media (max-width:720px){.paper{grid-template-columns:1fr;} .paper .rank{display:none;}}

/* ---------- plain list row (paywalled / flagged) ---------- */
.rowcard{background:var(--surface); border:1px solid var(--line); border-radius:var(--r); padding:14px 18px; margin-bottom:8px;}
.rowcard .ttl{font-weight:600; font-size:15px; color:var(--ink); text-decoration:none;}
.rowcard .sub{font-size:13px; color:var(--ink-3); margin-top:5px;}

/* ---------- timeline ---------- */
.tl{position:relative; margin:.2rem 0 .2rem 4px; padding-left:22px; border-left:1px solid var(--line);}
.tl .it{position:relative; padding:0 0 14px;}
.tl .it::before{content:""; position:absolute; left:-27px; top:5px; width:9px; height:9px; border-radius:50%;
  background:var(--surface); border:2px solid var(--teal);}
.tl .it.err::before{border-color:var(--rose);} .tl .it.sys::before{border-color:var(--ink-3);}
.tl .it.live::before{background:var(--teal); animation:cs-pulse 1.2s ease-in-out infinite;}
.tl .tool{font:500 12.5px/1 'Geist Mono',monospace; color:var(--ink); background:var(--sunk); padding:3px 7px; border-radius:6px;}
.tl .who{font-size:11.5px; color:var(--ink-3); margin-left:6px;}
.tl .th{font-size:14px; color:var(--ink-2); margin:5px 0 6px;}
.tl .res{font-size:12px; color:var(--ink-3); font-variant-numeric:tabular-nums;}
.tl .res b{color:var(--ink-2); font-weight:500;}
.tl .warn{font-size:12.5px; color:var(--rose); margin-top:4px;}

/* ---------- coverage ---------- */
.fac{display:grid; grid-template-columns:1fr 120px 34px; gap:10px; align-items:center; padding:7px 0; border-bottom:1px dashed var(--line); font-size:14px;}
.fac:last-child{border-bottom:0;}
.fac .src{font-size:11.5px; color:var(--ink-3);}
.fac .num{text-align:right; font-variant-numeric:tabular-nums; color:var(--ink-2);}

/* ---------- flow diagram ---------- */
.flow{display:grid; grid-template-columns:repeat(6,1fr); gap:8px; margin:1rem 0 1.6rem;}
.node{background:var(--surface); border:1px solid var(--line); border-radius:12px; padding:12px; position:relative;}
.node .l{font:500 10.5px/1 'Geist Mono',monospace; letter-spacing:.06em; text-transform:uppercase; color:var(--ink-3);}
.node .t{font-weight:600; font-size:14px; margin-top:6px;} .node .d{font-size:12px; color:var(--ink-3); margin-top:3px; line-height:1.4;}
.node.ai{border-color:var(--indigo); } .node.ai .l{color:var(--indigo);}
.node.code{border-color:var(--teal);} .node.code .l{color:var(--teal);}
.node.human{border-color:var(--amber);} .node.human .l{color:var(--amber);}
@media (max-width:820px){.flow{grid-template-columns:repeat(2,1fr);}}

/* ---------- tables + charts (HTML, so they follow dark mode) ---------- */
.tbl{max-height:420px; overflow:auto; border:1px solid var(--line); border-radius:var(--r); background:var(--surface);}
.tbl table{width:100%; border-collapse:collapse; font-size:13px;}
.tbl th{position:sticky; top:0; background:var(--sunk); text-align:left; font-weight:500; color:var(--ink-3); padding:9px 12px; border-bottom:1px solid var(--line); white-space:nowrap;}
.tbl td{padding:8px 12px; border-bottom:1px solid var(--line); color:var(--ink-2); vertical-align:top; font-variant-numeric:tabular-nums;}
.tbl tr:last-child td{border-bottom:0;} .tbl tr:hover td{background:var(--sunk);}
.tbl td.mono{font-family:'Geist Mono',monospace; color:var(--ink);}
.tbl a{color:var(--teal);}
.yb{display:flex; align-items:flex-end; gap:6px; height:150px; padding-top:18px;}
.yb .c{flex:1; display:flex; flex-direction:column; align-items:center; justify-content:flex-end; height:100%; gap:5px; min-width:0;}
.yb .col{width:100%; max-width:34px; height:var(--h); background:var(--teal); border-radius:6px 6px 2px 2px;
  transform-origin:bottom; animation:cs-grow .8s var(--ease) both;}
.yb .v{font-size:11.5px; color:var(--ink-2);} .yb .y{font:500 10.5px/1 'Geist Mono',monospace; color:var(--ink-3);}
@keyframes cs-grow{from{transform:scaleY(0)}}
.hb{display:grid; grid-template-columns:minmax(120px,220px) 1fr 54px; gap:10px; align-items:center; font-size:13px; padding:5px 0;}
.hb .l{color:var(--ink-2); font-family:'Geist Mono',monospace; font-size:12px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;}
.hb .num{text-align:right; color:var(--ink-2);}
.hb .bar{height:8px;}
@media (prefers-reduced-motion: reduce){ .yb .col{animation:none !important;} }

.callout{background:var(--sunk); border-radius:var(--r); padding:14px 18px; font-size:14.5px; color:var(--ink-2);}
.callout b{color:var(--ink);}
.section{font:500 12px/1 'Geist Mono',monospace; letter-spacing:.08em; text-transform:uppercase; color:var(--ink-3); margin:1.6rem 0 .7rem;}

/* ---------- streamlit widgets ---------- */
[data-testid="stVerticalBlockBorderWrapper"]{border-radius:var(--r) !important;}
.stButton > button, .stDownloadButton > button{border-radius:10px; font-weight:500; transition:transform .15s var(--ease), box-shadow .2s var(--ease);}
.stButton > button:hover, .stDownloadButton > button:hover{transform:translateY(-1px);}
.stButton > button[kind="primary"]{box-shadow:0 6px 18px -10px rgba(15,118,110,.8);}
.stTabs [data-baseweb="tab-list"]{gap:4px; border-bottom:1px solid var(--line);}
.stTabs [data-baseweb="tab"]{font-weight:500;}
[data-testid="stExpander"] details{border-radius:var(--r) !important; border-color:var(--line) !important; background:var(--surface);}
.stTextInput input, .stTextArea textarea{border-radius:10px !important;}
</style>
"""


DARK = """
<style>
:root{
  --bg:#0F1216; --surface:#161A20; --sunk:#1D222A; --line:#272D36; --line-strong:#38404B;
  --ink:#ECEEF1; --ink-2:#B7BDC7; --ink-3:#848C99;
  --teal:#3CC4B4; --teal-soft:rgba(60,196,180,.14); --amber:#F2B45A; --amber-soft:rgba(242,180,90,.14);
  --rose:#F07A72; --rose-soft:rgba(240,122,114,.14); --indigo:#97A3F2; --indigo-soft:rgba(151,163,242,.15);
}
.stApp, [data-testid="stAppViewContainer"], [data-testid="stHeader"], [data-testid="stBottom"] > div{background:var(--bg) !important; color:var(--ink);}
.stApp p, .stApp li, .stApp label, .stApp h1, .stApp h2, .stApp h3, .stApp h4, .stApp strong,
[data-testid="stMarkdownContainer"], [data-testid="stWidgetLabel"] p, [data-testid="stCaptionContainer"],
.stApp [data-testid="stCheckbox"] p, [data-testid="stToggle"] p{color:var(--ink) !important;}
[data-testid="stCaptionContainer"], [data-testid="stCaptionContainer"] p{color:var(--ink-3) !important;}
.stApp code{background:var(--sunk); color:var(--teal);}
/* inputs: one dark field, one subtle border, no white outlines */
[data-testid="stTextInputRootElement"], [data-testid="stTextAreaRootElement"], [data-testid="stNumberInputContainer"],
[data-baseweb="select"] > div{background:var(--sunk) !important; border:1px solid var(--line) !important; box-shadow:none !important;}
[data-testid="stTextInputRootElement"]:focus-within, [data-testid="stTextAreaRootElement"]:focus-within,
[data-testid="stNumberInputContainer"]:focus-within{border-color:var(--teal) !important;}
[data-testid="stTextInputRootElement"] *, [data-testid="stTextAreaRootElement"] *, [data-testid="stNumberInputContainer"] *{
  background:transparent !important; border-color:transparent !important; color:var(--ink) !important;}
.stTextInput input, .stTextArea textarea, .stNumberInput input, [data-baseweb="select"] *{color:var(--ink) !important; caret-color:var(--teal);}
.stTextInput input::placeholder, .stTextArea textarea::placeholder{color:var(--ink-3) !important;}
[data-testid="stNumberInputStepDown"], [data-testid="stNumberInputStepUp"]{color:var(--ink-2) !important;}
[data-testid="stNumberInputStepDown"]:hover, [data-testid="stNumberInputStepUp"]:hover{background:var(--line) !important;}
[data-testid="stNumberInputStepDown"] *, [data-testid="stNumberInputStepUp"] *{fill:var(--ink-2) !important; color:var(--ink-2) !important;}
/* help (?) icons */
[data-testid="stTooltipIcon"], [data-testid="stTooltipHoverTarget"], [data-testid="stTooltipIcon"] *{background:transparent !important; color:var(--ink-3) !important;}
[data-testid="stTooltipIcon"] svg{stroke:var(--ink-3) !important;}
[data-testid="stTooltipContent"]{background:var(--sunk) !important; color:var(--ink) !important;}
/* bordered containers */
[data-testid="stVerticalBlock"], [data-testid="stLayoutWrapper"]{border-color:var(--line) !important;}
/* toggle / checkbox: no highlight box */
[data-testid="stCheckbox"], [data-testid="stCheckbox"] label{background:transparent !important;}
[data-testid="stExpander"] details{background:var(--surface) !important; border-color:var(--line) !important;}
[data-testid="stExpander"] summary, [data-testid="stExpander"] summary *{color:var(--ink) !important;}
[data-testid="stExpander"] summary:hover{background:var(--sunk);}
.stTabs [data-baseweb="tab-list"]{border-color:var(--line);} .stTabs [data-baseweb="tab"] p{color:var(--ink-3) !important;}
.stTabs [aria-selected="true"] p{color:var(--ink) !important;}
.stTabs [data-baseweb="tab-border"]{background:var(--line);}
.stButton > button[kind="secondary"], .stDownloadButton > button[kind="secondary"], [data-testid="stPopoverButton"],
[data-testid="stButtonGroup"] button, button[kind^="pills"], button[kind^="segmented_control"]{
  background:var(--surface) !important; color:var(--ink-2) !important; border-color:var(--line) !important;}
[data-testid="stButtonGroup"] button *, button[kind^="pills"] *, button[kind^="segmented_control"] *,
.stButton > button[kind="secondary"] *, [data-testid="stPopoverButton"] *{color:inherit !important;}
[data-testid="stButtonGroup"] button:hover, .stButton > button[kind="secondary"]:hover{border-color:var(--line-strong) !important; color:var(--ink) !important;}
button[kind="pillsActive"], button[kind="segmented_controlActive"], [data-testid="stButtonGroup"] button[aria-checked="true"]{
  background:var(--teal-soft) !important; color:var(--teal) !important; border-color:var(--teal) !important;}
.stButton > button[kind="primary"], .stDownloadButton > button[kind="primary"]{background:var(--teal) !important; border-color:var(--teal) !important;}
.stButton > button[kind="primary"] *, .stDownloadButton > button[kind="primary"] *{color:#04211D !important; font-weight:600;}
.stButton > button[kind="primary"]:hover{filter:brightness(1.08);}
.stButton > button:disabled{opacity:.45;}
[data-testid="stTopNavLink"] span, [data-testid="stNavSectionHeader"], header a span, header nav *{color:var(--ink-2) !important;}
[data-testid="stTopNavLink"][aria-current="page"], [data-testid="stTopNavLink"]:hover{background:var(--sunk) !important;}
[data-testid="stTopNavLink"][aria-current="page"] span{color:var(--ink) !important;}
[data-testid="stStatusWidget"], [data-testid="stToolbar"] *{color:var(--ink-2) !important;}
[data-testid="stPopoverBody"], [data-baseweb="popover"] > div, [data-baseweb="menu"], [role="listbox"]{background:var(--surface) !important; color:var(--ink) !important;}
[role="option"]{color:var(--ink) !important;} [role="option"]:hover{background:var(--sunk) !important;}
[data-testid="stToast"]{background:var(--surface) !important; color:var(--ink) !important;}
[data-testid="stAlert"] > div{background:var(--sunk) !important; color:var(--ink) !important;}
[data-testid="stJson"]{background:var(--sunk) !important; border-radius:10px;}
.paper:hover{box-shadow:0 8px 28px -16px rgba(0,0,0,.9);}
.brand .mark{background:var(--teal); color:#06201D;}
</style>
"""


def inject() -> None:
    st.html(CSS)


def inject_dark() -> None:
    st.html(DARK)


def num(v: Any) -> str:
    """Count-up markup for a number shown as text ('25', '76%', '207.0s', '6/6', '8.7'); falls back to plain text."""
    import re
    s = str(v)
    m = re.match(r"^([^0-9-]*)(-?[0-9][0-9,]*)(\.([0-9]+))?(.*)$", s)
    if not m:
        return e(s)
    pre, whole, _, frac, post = m.groups()
    whole = int(whole.replace(",", ""))
    if whole < 0:
        return e(s)
    if frac:  # one decimal place animates; more decimals are rounded to one
        n = round(float(f"{whole}.{frac}") * 10)
        return f'{e(pre)}<span class="cnt dec" style="--n:{n}"></span>{e(post)}'
    return f'{e(pre)}<span class="cnt" style="--n:{whole}"></span>{e(post)}'


def table(rows: list[dict], links: set[str] | None = None, mono: set[str] | None = None) -> None:
    links, mono = links or set(), mono or set()
    if not rows:
        st.caption("Nothing to show.")
        return
    cols = list(rows[0].keys())
    head = "".join(f"<th>{e(c)}</th>" for c in cols)

    def cell(c, v):
        if c in links and v:
            return f'<td><a href="{e(v)}" target="_blank">open ↗</a></td>'
        return f'<td class="{"mono" if c in mono else ""}">{e(v)}</td>'
    body = "".join("<tr>" + "".join(cell(c, r.get(c, "")) for c in cols) + "</tr>" for r in rows)
    md(f'<div class="tbl"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>')


def year_bars(years: dict) -> str:
    items = sorted((int(y), int(c)) for y, c in years.items())
    top = max([c for _, c in items] + [1])
    cols = "".join(
        f'<div class="c"><span class="v">{c}</span><div class="col" style="--h:{max(4, 100 * c / top):.0f}%;'
        f'animation-delay:{i * 50}ms"></div><span class="y">{y}</span></div>' for i, (y, c) in enumerate(items))
    return f'<div class="yb">{cols}</div>'


def hbars(rows: list[tuple[str, float]], unit: str = "") -> str:
    top = max([v for _, v in rows] + [1e-9])
    return "".join(
        f'<div class="hb"><span class="l" title="{e(k)}">{e(k)}</span><div class="bar"><i style="--w:{100 * v / top:.0f}%;'
        f'animation-delay:{i * 40}ms"></i></div><span class="num">{num(f"{v:.1f}")}{e(unit)}</span></div>'
        for i, (k, v) in enumerate(rows))


def e(x: Any) -> str:
    return html.escape("" if x is None else str(x))


def md(s: str) -> None:
    st.markdown(s, unsafe_allow_html=True)


def chip(text: str, tone: str = "", dot: bool = False, live: bool = False) -> str:
    d = f'<span class="dot{" live" if live else ""}"></span>' if dot else ""
    return f'<span class="chip {tone}">{d}{e(text)}</span>'


def stepper(active: int) -> None:
    labels = [("Ask", "question · years · keywords"), ("Agent scouts", "searches, merges, verifies"),
              ("Review", "ranked, annotated, gaps shown"), ("Approve", "legal PDFs + BibTeX")]
    out = []
    for i, (t, d) in enumerate(labels, 1):
        cls = "done" if i < active else "now" if i == active else ""
        out.append(f'<div class="step {cls}"><div class="n">0{i}</div><div class="t">{t}</div><div class="d">{d}</div></div>')
    md(f'<div class="steps">{"".join(out)}</div>')


def stats(tiles: list[tuple[str, Any, str, str]], anim: bool = True) -> None:
    """tiles: (label, value, hint, tone)"""
    cells = "".join(
        f'<div class="stat {tone} {"anim" if anim else ""}" style="animation-delay:{i * 60}ms">'
        f'<div class="k">{e(k)}</div><div class="v">{num(v) if anim else e(v)}</div><div class="h">{e(h)}</div></div>'
        for i, (k, v, h, tone) in enumerate(tiles))
    md(f'<div class="stats">{cells}</div>')


def _bar(label: str, val: float | None, tone: str = "") -> str:
    v = 0.0 if val is None else float(val)
    return (f'<div class="row"><span>{e(label)}</span><div class="bar {tone}"><i style="--w:{max(0, min(10, v)) * 10:.0f}%">'
            f'</i></div><span>{v:.0f}</span></div>')


def _authors(p) -> str:
    a = [x.split()[-1] for x in (p.authors or []) if x.strip()]
    if not a:
        return ""
    return a[0] + (" et al." if len(a) > 2 else f" & {a[1]}" if len(a) == 2 else "")


def paper_card(i: int, p, anim: bool) -> None:
    v, r = p.verification or {}, p.relevance or {}
    doi = p.best_doi
    link = f"https://doi.org/{doi}" if doi else (p.url or "#")
    badges = [chip("DOI verified", "teal", dot=True) if v.get("citable") else chip("unverified", "rose")]
    oa = (p.oa_status or "").lower()
    if oa:
        badges.append(chip(f"open access · {oa}", "teal" if oa != "closed" else "amber"))
    if p.merged_from:
        badges.append(chip(f"merged {len(p.merged_from) + 1} versions", "indigo"))
    prov = p.provenance or ""
    if prov.startswith("snowball"):
        badges.append(chip("found by snowballing", "indigo"))
    integ = (v.get("integrity") or {}).get("status")
    if integ and integ not in ("ok", "clean", "none"):
        badges.append(chip(f"integrity: {integ}", "amber"))
    if r.get("quote_check") == "rejected":
        badges.append(chip("AI quote rejected · lexical score", "amber"))
    meta = " · ".join(x for x in [_authors(p), str(p.year or ""), p.venue or "arXiv",
                                  f"{p.citation_count:,} citations" if p.citation_count else ""] if x)
    why = f'<p class="why">{e(r.get("why_cite"))}</p>' if r.get("why_cite") else ""
    quote = (f'<div class="quote"><b>From the abstract</b>“{e(r.get("evidence_quote"))}”</div>'
             if r.get("evidence_quote") else "")
    score = r.get("score")
    sc = (f'<div class="score"><div class="big">{num(f"{float(score):.1f}") if anim else f"{float(score):.1f}"}<small> / 10</small></div>'
          + _bar("Topic", r.get("topic_match")) + _bar("Method", r.get("method_match"))
          + _bar("Impact", r.get("citation_impact"), "ink") + _bar("Recency", r.get("recency"), "ink") + "</div>"
          if score is not None else "<div></div>")
    delay = min(i, 12) * 35
    md(f'<div class="paper {"anim" if anim else ""}" style="animation-delay:{delay}ms">'
       f'<div class="rank">{i:02d}</div>'
       f'<div><a class="ttl" href="{e(link)}" target="_blank">{e(p.title)}</a>'
       f'<div class="meta">{e(meta)}</div><div class="chips">{"".join(badges)}</div>{why}{quote}</div>{sc}</div>')


def paywalled_card(p, anim: bool, i: int = 0) -> None:
    pub = getattr(p, "publisher", {}) or {}
    doi = p.best_doi
    chips_ = [chip("link only · no legal OA copy", "amber")]
    sub = ""
    if pub.get("verdict"):
        tone = "amber" if pub["verdict"] == "paywall_confirmed" else "rose" if pub["verdict"] == "blocked" else "teal"
        chips_.append(chip(f"publisher page: {pub['verdict'].replace('_', ' ')}", tone, dot=True))
        chips_.append(chip(f"via {pub.get('via', 'fetch')}", "ghost"))
        bits = []
        if pub.get("paywall_markers"):
            bits.append(f'Page says “{e(pub["paywall_markers"][0])}”')
        if pub.get("replay_url"):
            bits.append(f'<a href="{e(pub["replay_url"])}" target="_blank">Watch the browser session ↗</a>')
        sub = " · ".join(bits)
    md(f'<div class="rowcard {"anim" if anim else ""}" style="animation-delay:{min(i, 10) * 35}ms">'
       f'<a class="ttl" href="https://doi.org/{e(doi)}" target="_blank">{e(p.title)}</a>'
       f'<div class="sub">{e(" · ".join(x for x in [_authors(p), str(p.year or ""), p.venue] if x))}</div>'
       f'<div class="chips" style="margin-top:8px">{"".join(chips_)}</div>'
       + (f'<div class="sub">{sub}</div>' if sub else "") + "</div>")


def flagged_card(p, anim: bool, i: int = 0) -> None:
    v = p.verification or {}
    reason = v.get("reason") or "failed verification"
    tag = "retracted" if v.get("retracted") else "DOI mismatch" if v.get("title_score") is not None else "unverified"
    md(f'<div class="rowcard {"anim" if anim else ""}" style="animation-delay:{min(i, 10) * 35}ms">'
       f'<span class="ttl">{e(p.title)}</span>'
       f'<div class="chips" style="margin-top:8px">{chip(tag, "rose", dot=True)}'
       + (chip(f"doi {p.best_doi}", "ghost") if p.best_doi else "") + "</div>"
       f'<div class="sub">{e(reason)}</div></div>')


TOOL_LABEL = {"search_arxiv": "Search arXiv", "search_s2": "Search Semantic Scholar", "verify_pool": "Verify candidates",
              "snowball": "Follow citations", "inspect_publisher": "Check publisher page",
              "coverage_gaps": "Check coverage", "finish": "Finish", "fallback": "Switch planner"}


def _step_html(s: dict, live: bool) -> str:
    res = s.get("result") or {}
    t = res.get("tally") or {}
    cls = "err" if res.get("error") else "sys" if s.get("planner") == "system" else ""
    tally = (f'<div class="res"><b>{t.get("cited", 0)}</b> cited · <b>{t.get("paywalled", 0)}</b> paywalled · '
             f'<b>{t.get("flagged", 0)}</b> flagged · {t.get("pending", 0)} pending · {s.get("ms", 0) / 1000:.1f}s</div>'
             if t else f'<div class="res">{s.get("ms", 0) / 1000:.1f}s</div>')
    extra = ""
    if s.get("tool") == "coverage_gaps" and res.get("gaps"):
        extra = f'<div class="res">gaps: {e(", ".join(res["gaps"][:4]))}</div>'
    if s.get("tool") == "inspect_publisher" and res.get("verdict"):
        extra = f'<div class="res">{e(res.get("publisher_host", ""))} → <b>{e(res["verdict"])}</b></div>'
    warn = f'<div class="warn">{e(res["error"])}</div>' if res.get("error") else ""
    return (f'<div class="it {cls} {"live anim" if live else ""}"><span class="tool">{e(s.get("tool"))}</span>'
            f'<span class="who">{e(TOOL_LABEL.get(s.get("tool", ""), ""))} · {e(s.get("planner"))}</span>'
            f'<div class="th">{e(s.get("thought"))}</div>{tally}{extra}{warn}</div>')


def timeline(steps: list[dict], live_last: bool = False) -> str:
    items = [_step_html(s, live_last and i == len(steps) - 1) for i, s in enumerate(steps)]
    return f'<div class="tl">{"".join(items)}</div>'


def facet_rows(facets: list[dict], threshold: int, n: int) -> str:
    out = []
    top = max([f["papers"] for f in facets] + [1])
    for f in facets:
        gap = f["status"] == "gap"
        out.append(f'<div class="fac"><div>{e(f["facet"])}<div class="src">{e(f.get("source", ""))}'
                   f'{" · gap" if gap else ""}</div></div>'
                   f'<div class="bar {"rose" if gap else ""}"><i style="--w:{100 * f["papers"] / top:.0f}%"></i></div>'
                   f'<div class="num">{num(f["papers"])}</div></div>')
    return "".join(out)