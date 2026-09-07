"""Build the results dashboard (docs/dashboard.html) from the data bundle.

Reads docs/dashboard_data.json and injects it into a self-contained HTML page —
no external requests, so it renders as a Claude Artifact. Regenerating the data
(scripts/generate_dashboard_data.py) and re-running this rebuilds the page.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "docs" / "dashboard_data.json"
OUT = ROOT / "docs" / "dashboard.html"

TEMPLATE = r"""<title>Prop Simulator — Research Readout</title>
<style>
:root{
  --bg:#f5f6f8; --surface:#fff; --surface-2:#f0f2f5; --border:#e2e6ec;
  --text:#1e2733; --muted:#64717f; --accent:#2f6df0;
  --pass:#1f9d55; --warn:#c07610; --fail:#d33b3f; --grid:rgba(15,25,40,.07);
  --sans:system-ui,-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
  --mono:ui-monospace,"SF Mono","Cascadia Code",Menlo,Consolas,monospace;
}
@media (prefers-color-scheme:dark){:root{
  --bg:#0d131b; --surface:#151d28; --surface-2:#1b2530; --border:#26313f;
  --text:#d3dbe6; --muted:#8291a2; --accent:#5b95ff;
  --pass:#34c471; --warn:#e0a636; --fail:#ec5a5f; --grid:rgba(255,255,255,.06);
}}
:root[data-theme="light"]{
  --bg:#f5f6f8; --surface:#fff; --surface-2:#f0f2f5; --border:#e2e6ec;
  --text:#1e2733; --muted:#64717f; --accent:#2f6df0;
  --pass:#1f9d55; --warn:#c07610; --fail:#d33b3f; --grid:rgba(15,25,40,.07);
}
:root[data-theme="dark"]{
  --bg:#0d131b; --surface:#151d28; --surface-2:#1b2530; --border:#26313f;
  --text:#d3dbe6; --muted:#8291a2; --accent:#5b95ff;
  --pass:#34c471; --warn:#e0a636; --fail:#ec5a5f; --grid:rgba(255,255,255,.06);
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font-family:var(--sans);
  line-height:1.5;-webkit-font-smoothing:antialiased}
.wrap{max-width:1080px;margin:0 auto;padding:clamp(20px,4vw,44px)}
.eyebrow{font-family:var(--mono);font-size:11.5px;letter-spacing:.16em;
  text-transform:uppercase;color:var(--muted)}
h1{font-size:clamp(28px,5vw,44px);line-height:1.05;margin:.35em 0 .15em;
  letter-spacing:-.02em;font-weight:640;text-wrap:balance}
h2{font-size:19px;margin:0;letter-spacing:-.01em;font-weight:620}
.sub{color:var(--muted);max-width:60ch;margin:.2em 0 0}
.mono{font-family:var(--mono);font-variant-numeric:tabular-nums}
header{display:flex;justify-content:space-between;align-items:flex-start;gap:16px}
.toggle{font-family:var(--mono);font-size:11px;letter-spacing:.1em;text-transform:uppercase;
  background:var(--surface);color:var(--muted);border:1px solid var(--border);
  border-radius:7px;padding:8px 12px;cursor:pointer;white-space:nowrap}
.toggle:hover{color:var(--text);border-color:var(--accent)}
.toggle:focus-visible{outline:2px solid var(--accent);outline-offset:2px}

.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));
  gap:14px;margin:30px 0}
.tile{background:var(--surface);border:1px solid var(--border);border-radius:12px;
  padding:18px 18px 16px;position:relative;overflow:hidden}
.tile::before{content:"";position:absolute;left:0;top:0;bottom:0;width:3px;background:var(--c,var(--accent))}
.tile .k{font-family:var(--mono);font-size:11px;letter-spacing:.12em;text-transform:uppercase;color:var(--muted)}
.tile .v{font-family:var(--mono);font-variant-numeric:tabular-nums;font-size:30px;
  font-weight:600;margin:.18em 0 .05em;color:var(--c,var(--text));letter-spacing:-.02em}
.tile .n{font-size:12.5px;color:var(--muted)}

section{background:var(--surface);border:1px solid var(--border);border-radius:14px;
  padding:22px clamp(16px,3vw,26px);margin:16px 0}
.sechead{display:flex;justify-content:space-between;align-items:baseline;gap:12px;margin-bottom:4px}
.sechead .tag{font-family:var(--mono);font-size:11px;letter-spacing:.12em;
  text-transform:uppercase;color:var(--muted)}
.note{color:var(--muted);font-size:13.5px;margin:.5em 0 1.1em;max-width:70ch}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:16px}
@media(max-width:720px){.grid2{grid-template-columns:1fr}}

/* bar rows */
.bars{display:flex;flex-direction:column;gap:10px;margin-top:6px}
.bar{display:grid;grid-template-columns:118px 1fr 62px;align-items:center;gap:12px}
.bar .lbl{font-family:var(--mono);font-size:12.5px;color:var(--text)}
.bar .track{height:22px;background:var(--surface-2);border-radius:5px;overflow:hidden}
.bar .fill{height:100%;border-radius:5px;transition:width .6s cubic-bezier(.2,.7,.2,1)}
.bar .val{font-family:var(--mono);font-variant-numeric:tabular-nums;font-size:13px;text-align:right;color:var(--muted)}

table{width:100%;border-collapse:collapse;font-family:var(--mono);
  font-variant-numeric:tabular-nums;font-size:13px;margin-top:14px}
th,td{text-align:right;padding:7px 10px;border-bottom:1px solid var(--border)}
th:first-child,td:first-child{text-align:left}
th{font-size:10.5px;letter-spacing:.09em;text-transform:uppercase;color:var(--muted);font-weight:500}
tbody tr:last-child td{border-bottom:none}

.chip{display:inline-flex;align-items:center;gap:6px;font-family:var(--mono);
  font-size:11.5px;padding:4px 9px;border-radius:999px;border:1px solid var(--border);
  background:var(--surface-2);letter-spacing:.02em}
.chip.ok{color:var(--pass)} .chip.bad{color:var(--fail)}
.chip .dot{width:7px;height:7px;border-radius:50%;background:currentColor}
.chips{display:flex;flex-wrap:wrap;gap:8px;margin:2px 0 6px}

.verdict{display:flex;align-items:center;gap:12px;padding:14px 16px;border-radius:10px;
  background:var(--surface-2);border:1px solid var(--border);margin-top:8px}
.verdict .badge{font-family:var(--mono);font-size:12px;font-weight:600;letter-spacing:.06em;
  text-transform:uppercase;padding:5px 11px;border-radius:6px;white-space:nowrap}
.badge.no{background:color-mix(in srgb,var(--fail) 16%,transparent);color:var(--fail)}
.badge.yes{background:color-mix(in srgb,var(--pass) 16%,transparent);color:var(--pass)}
.verdict .txt{font-size:13.5px;color:var(--text)}

.statrow{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:12px;margin-top:14px}
.stat{background:var(--surface-2);border:1px solid var(--border);border-radius:9px;padding:12px 14px}
.stat .k{font-family:var(--mono);font-size:10px;letter-spacing:.1em;text-transform:uppercase;color:var(--muted)}
.stat .v{font-family:var(--mono);font-variant-numeric:tabular-nums;font-size:19px;font-weight:600;margin-top:3px}

svg{display:block;width:100%;height:auto}
.legend{display:flex;flex-wrap:wrap;gap:14px;margin-top:10px;font-family:var(--mono);font-size:11.5px;color:var(--muted)}
.legend span{display:inline-flex;align-items:center;gap:6px}
.legend i{width:14px;height:3px;border-radius:2px;display:inline-block}

.footer{margin-top:22px;padding:20px 22px;border:1px dashed var(--border);border-radius:14px;
  background:var(--surface);color:var(--muted);font-size:13px}
.footer b{color:var(--text);font-weight:600}
.footer .meta{font-family:var(--mono);font-size:11.5px;margin-top:10px;color:var(--muted);
  display:flex;flex-wrap:wrap;gap:6px 18px}
.pos{color:var(--pass)} .neg{color:var(--fail)} .amb{color:var(--warn)}
@media (prefers-reduced-motion:reduce){*{transition:none!important}}
</style>

<div class="wrap">
  <header>
    <div>
      <div class="eyebrow">Prop Simulator · Research Readout</div>
      <h1>A measuring instrument, not a bot</h1>
      <p class="sub">Live results from the tick-accurate prop-firm challenge simulator on
        <b id="instrument"></b>. The instrument is a driftless random walk, so every number
        here measures how the <em>rules and risk geometry</em> shape the odds — never a market edge.</p>
    </div>
    <button class="toggle" id="themeBtn" aria-label="Toggle colour theme">◐ Theme</button>
  </header>

  <div class="tiles" id="tiles"></div>

  <section>
    <div class="sechead"><h2>Strategy comparison</h2><span class="tag">P(pass) · discovery seeds</span></div>
    <p class="note" id="stratNote"></p>
    <div class="bars" id="stratBars"></div>
    <div style="overflow-x:auto"><table id="stratTable"></table></div>
  </section>

  <div class="grid2">
    <section>
      <div class="sechead"><h2>Risk is the lever</h2><span class="tag">P(pass) by risk %</span></div>
      <p class="note">On a zero-edge instrument the entry can't add edge — only risk-per-trade
        and frequency move the odds. The interior optimum near 1% is the whole game.</p>
      <div class="bars" id="riskBars"></div>
    </section>
    <section>
      <div class="sechead"><h2>Research loop</h2><span class="tag">champion / challenger</span></div>
      <p class="note">v2 was built from v1's findings (looser channel, break-even + partial + trailing)
        and tested out-of-sample against the incumbent.</p>
      <div class="verdict" id="promo"></div>
      <div class="statrow" id="tradeStats"></div>
    </section>
  </div>

  <section>
    <div class="sechead"><h2>Equity paths</h2><span class="tag">random entry · three outcomes</span></div>
    <p class="note">Three representative 30-day accounts: the squeeze between two failure modes —
      breaching the trailing drawdown, or expiring at the deadline — with the rare pass in green.</p>
    <div id="equity"></div>
    <div class="legend" id="equityLegend"></div>
  </section>

  <div class="grid2">
    <section>
      <div class="sechead"><h2>Career economics</h2><span class="tag">12-month campaign</span></div>
      <p class="note">The headline the project exists to produce: what a year of attempts nets after fees.</p>
      <div class="statrow" id="campaign"></div>
    </section>
    <section>
      <div class="sechead"><h2>M15 replay validation</h2><span class="tag">resolution check</span></div>
      <p class="note">Synthesising a year of ticks from M15 anchors — validated against the true path.</p>
      <div class="chips" id="m15chips"></div>
      <div class="statrow" id="m15stats"></div>
    </section>
  </div>

  <div class="footer">
    <b>This is a simulator. It never touches a real account, and places no real trades.</b>
    Volatility 75 is a published, audited driftless random walk at 75% annualised volatility — for a
    stop at distance <span class="mono">a</span> and target at <span class="mono">b</span>,
    P(target first) = <span class="mono">a/(a+b)</span>, so every reward-to-risk ratio is exactly
    zero-expectancy. The measured campaign is net-negative by construction; that is the finding, not a bug.
    <div class="meta" id="meta"></div>
  </div>
</div>

<script type="application/json" id="payload">__DATA__</script>
<script>
const DATA = JSON.parse(document.getElementById("payload").textContent);
const $=id=>document.getElementById(id);
const pct=x=>(x*100).toFixed(1)+"%";
const money=x=>(x<0?"−$":"$")+Math.abs(Math.round(x)).toLocaleString("en-US");
const css=v=>getComputedStyle(document.documentElement).getPropertyValue(v).trim();

$("instrument").textContent=DATA.config.instrument;

/* hero tiles */
const best=DATA.strategies.reduce((a,b)=>b.p_pass>a.p_pass?b:a);
const c=DATA.campaign||{};
const tiles=[
  {k:"Best P(pass)",v:pct(best.p_pass),n:best.name+", "+DATA.config.days+"d challenge",col:"--accent"},
  {k:"Expected 12-mo net",v:money(c.expected_net||0),n:"after fees, per career",col:(c.expected_net||0)>=0?"--pass":"--fail"},
  {k:"Reached funded",v:pct(c.p_reached_funded||0),n:"yet "+pct(c.p_profitable||0)+" clear their fees",col:"--warn"},
  {k:"Net-profitable",v:pct(c.p_profitable||0),n:"careers ending in the black",col:(c.p_profitable||0)>=0.5?"--pass":"--fail"},
];
$("tiles").innerHTML=tiles.map(t=>`<div class="tile" style="--c:var(${t.col})">
  <div class="k">${t.k}</div><div class="v">${t.v}</div><div class="n">${t.n}</div></div>`).join("");

/* strategy bars + table */
const scol=s=>s.name==="random-entry"?"--accent":(s.p_pass>=best.p_pass?"--pass":"--muted");
const smax=Math.max(...DATA.strategies.map(s=>s.p_pass),0.3);
$("stratBars").innerHTML=DATA.strategies.map(s=>`<div class="bar">
  <span class="lbl">${s.name}</span>
  <div class="track"><div class="fill" style="width:${(s.p_pass/smax*100).toFixed(1)}%;background:var(${scol(s)})"></div></div>
  <span class="val">${pct(s.p_pass)}</span></div>`).join("");
$("stratNote").textContent="The structural seed does not beat random entry — its entries carry no information, exactly as the zero-expectancy result predicts. Selectivity only cuts trade count, which trades drawdown risk for deadline risk.";
$("stratTable").innerHTML=`<thead><tr><th>strategy</th><th>P(pass)</th><th>passed</th><th>breached</th><th>expired</th><th>trades</th></tr></thead>
<tbody>${DATA.strategies.map(s=>`<tr><td>${s.name}</td><td>${pct(s.p_pass)}</td><td>${s.n_pass}</td><td>${s.breached}</td><td>${s.expired}</td><td>${s.mean_trades}</td></tr>`).join("")}</tbody>`;

/* risk sweep */
const rmax=Math.max(...DATA.risk_sweep.map(r=>r.p_pass));
$("riskBars").innerHTML=DATA.risk_sweep.map(r=>`<div class="bar">
  <span class="lbl">${r.risk_pct.toFixed(1)}% risk</span>
  <div class="track"><div class="fill" style="width:${(r.p_pass/rmax*100).toFixed(1)}%;background:var(${r.p_pass>=rmax?"--pass":"--accent"})"></div></div>
  <span class="val">${pct(r.p_pass)}</span></div>`).join("");

/* promotion verdict */
const pr=DATA.champion_decision;
$("promo").innerHTML=`<span class="badge ${pr.promoted?"yes":"no"}">${pr.promoted?"Promoted":"Rejected"}</span>
  <span class="txt">v2 scored <b class="mono">${pct(pr.challenger_score)}</b> vs v1 <b class="mono">${pct(pr.champion_score)}</b>
  out-of-sample — ${pr.promoted?"clears":"below"} the ${pct(pr.margin_required)} promotion margin.
  The loop refuses to adopt a change that doesn't beat the incumbent.</span>`;
const ts=DATA.trade_summary||{};
$("tradeStats").innerHTML=[
  ["Win rate",pct(ts.win_rate||0)],["Expectancy",money(ts.expectancy||0)],
  ["Mean R",(ts.mean_r||0).toFixed(2)+"R"],["Trades",ts.n||0],
].map(([k,v])=>`<div class="stat"><div class="k">${k}</div><div class="v">${v}</div></div>`).join("");

/* equity line chart */
(function(){
  const cv=DATA.equity_curves; const order=["pass","breach","expire"];
  const colBy={pass:"--pass",breach:"--fail",expire:"--warn"};
  const series=order.filter(k=>cv[k]).map(k=>({key:k,col:colBy[k],pts:cv[k].points,outcome:cv[k].outcome}));
  if(!series.length){$("equity").textContent="no data";return;}
  const W=1000,H=340,pad={l:64,r:18,t:16,b:34};
  const allx=series.flatMap(s=>s.pts.map(p=>p.d)), ally=series.flatMap(s=>s.pts.map(p=>p.eq));
  const x0=0,x1=Math.max(...allx);
  let y0=Math.min(...ally,90000), y1=Math.max(...ally,108500);
  y0=Math.floor(y0/1000)*1000; y1=Math.ceil(y1/1000)*1000;
  const X=v=>pad.l+(v-x0)/(x1-x0)*(W-pad.l-pad.r);
  const Y=v=>pad.t+(1-(v-y0)/(y1-y0))*(H-pad.t-pad.b);
  const line=p=>p.map((q,i)=>(i?"L":"M")+X(q.d).toFixed(1)+" "+Y(q.eq).toFixed(1)).join(" ");
  let g="";
  for(let e=y0;e<=y1;e+=2500){g+=`<line x1="${pad.l}" y1="${Y(e)}" x2="${W-pad.r}" y2="${Y(e)}" stroke="var(--grid)"/>
    <text x="${pad.l-8}" y="${Y(e)+4}" text-anchor="end" font-family="var(--mono)" font-size="10" fill="var(--muted)">${(e/1000)}k</text>`;}
  // reference lines: 8% target and 100k start
  const ref=(v,lab,col)=>`<line x1="${pad.l}" y1="${Y(v)}" x2="${W-pad.r}" y2="${Y(v)}" stroke="${col}" stroke-dasharray="4 4" opacity=".7"/>
    <text x="${W-pad.r}" y="${Y(v)-5}" text-anchor="end" font-family="var(--mono)" font-size="10" fill="${col}">${lab}</text>`;
  const paths=series.map(s=>`<path d="${line(s.pts)}" fill="none" stroke="var(${s.col})" stroke-width="2"/>
    <circle cx="${X(s.pts[s.pts.length-1].d)}" cy="${Y(s.pts[s.pts.length-1].eq)}" r="3.4" fill="var(${s.col})"/>`).join("");
  const xlab=[0,Math.round(x1/2),Math.round(x1)].map(d=>`<text x="${X(d)}" y="${H-10}" text-anchor="middle" font-family="var(--mono)" font-size="10" fill="var(--muted)">${d}d</text>`).join("");
  $("equity").innerHTML=`<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Equity paths">
    ${g}${ref(108000,"+8% target","var(--pass)")}${ref(90000,"−10% floor","var(--fail)")}
    ${paths}${xlab}</svg>`;
  $("equityLegend").innerHTML=series.map(s=>`<span><i style="background:var(${s.col})"></i>${s.key} — ${s.outcome.replace(/_/g," ")}</span>`).join("");
})();

/* campaign */
$("campaign").innerHTML=[
  ["Expected net",money(c.expected_net||0),(c.expected_net||0)>=0?"pos":"neg"],
  ["Median",money(c.median_net||0),(c.median_net||0)>=0?"pos":"neg"],
  ["p10 … p90",money(c.net_p10||0)+" … "+money(c.net_p90||0),""],
  ["Mean fees",money(c.mean_fees||0),"neg"],
  ["Mean payouts",money(c.mean_payouts||0),"pos"],
  ["Attempts/yr",(c.mean_attempts||0).toFixed(1),""],
].map(([k,v,cl])=>`<div class="stat"><div class="k">${k}</div><div class="v ${cl}">${v}</div></div>`).join("");

/* m15 */
const m=DATA.m15_validation||{}; const checks=(m.stats&&m.stats.checks)||{};
$("m15chips").innerHTML=Object.entries(checks).map(([k,v])=>`<span class="chip ${v?"ok":"bad"}"><span class="dot"></span>${k} ${v?"✓":"✗"}</span>`).join("")
  +(m.p_pass?`<span class="chip ${m.p_pass_preserved?"ok":"bad"}"><span class="dot"></span>P(pass) ${m.p_pass_preserved?"preserved":"biased"} · p=${(m.p_pass.p_value).toFixed(2)}</span>`:"");
if(m.stats&&m.stats.candidate){const cd=m.stats.candidate;
  $("m15stats").innerHTML=[
    ["σ / tick",(cd.sigma).toExponential(2)],["autocorr(1)",(cd.autocorr1).toFixed(3)],
    ["up-tick",(cd.uptick_frac*100).toFixed(1)+"%"],["kurtosis",(cd.kurtosis>=0?"+":"")+cd.kurtosis.toFixed(2)],
  ].map(([k,v])=>`<div class="stat"><div class="k">${k}</div><div class="v">${v}</div></div>`).join("");}

/* meta + theme */
$("meta").innerHTML=[`generated ${DATA.generated_at}`,
  `${DATA.config.n} seeds · ${DATA.config.days}d · ${DATA.config.tick_seconds}s ticks · ${DATA.config.ruleset}`,
  `rebuild: scripts/generate_dashboard_data.py → build_dashboard.py`].map(s=>`<span>${s}</span>`).join("");

const btn=$("themeBtn");
btn.onclick=()=>{const cur=document.documentElement.getAttribute("data-theme");
  const next=cur==="dark"?"light":(cur==="light"?"dark":(matchMedia("(prefers-color-scheme:dark)").matches?"light":"dark"));
  document.documentElement.setAttribute("data-theme",next);
  // recolour the SVG reference labels which were baked with computed colours
  };
</script>
"""


def main() -> int:
    data = json.loads(DATA.read_text())
    payload = json.dumps(data, separators=(",", ":")).replace("</", "<\\/")
    OUT.write_text(TEMPLATE.replace("__DATA__", payload))
    print(f"wrote {OUT} ({OUT.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
