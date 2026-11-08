"""Serve a local, read-only dashboard for game event logs."""
# ruff: noqa: E501

import argparse
import json
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

DASHBOARD = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Swarm Alignment · Game room</title>
<style>
:root{color-scheme:dark;--bg:#101310;--panel:#191e19;--line:#303930;--muted:#a2ada2;--text:#f1f4ef;--green:#b9f27c;--blue:#9cc7ff;--amber:#ffd27c}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:15px/1.5 ui-sans-serif,system-ui,-apple-system,sans-serif}
header{display:flex;align-items:center;gap:16px;padding:18px 24px;border-bottom:1px solid var(--line);position:sticky;top:0;background:#101310f2;z-index:2;backdrop-filter:blur(12px)}
h1{font-size:19px;margin:0;font-weight:650;letter-spacing:.01em}.brand{color:var(--green)}select,button{font:inherit;color:var(--text);background:var(--panel);border:1px solid var(--line);border-radius:9px;padding:9px 12px}select{min-width:280px;max-width:55vw}button{cursor:pointer}button:hover{border-color:var(--green)}
.toolbar{display:flex;align-items:center;gap:10px;margin-left:auto}.status{font-size:12px;color:var(--muted)}.status.live{color:var(--green)}main{max-width:1440px;margin:0 auto;padding:22px 24px 48px}
.summary{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:17px}.pill{border:1px solid var(--line);background:var(--panel);padding:6px 10px;border-radius:99px;color:var(--muted);font-size:13px}.pill strong{color:var(--text);font-weight:600}.pill.live{border-color:#466b34;color:var(--green)}
.cards{display:grid;grid-template-columns:repeat(4,minmax(130px,1fr));gap:12px;margin-bottom:18px}.card,.panel{background:var(--panel);border:1px solid var(--line);border-radius:13px}.card{padding:15px 17px}.label{font-size:12px;color:var(--muted);text-transform:uppercase;letter-spacing:.08em}.value{font-size:22px;margin-top:4px;font-weight:620}.layout{display:grid;grid-template-columns:minmax(0,1.65fr) minmax(300px,.9fr);gap:16px;align-items:start}.panel{overflow:hidden}.panel-head{display:flex;align-items:center;justify-content:space-between;padding:14px 17px;border-bottom:1px solid var(--line)}h2{font-size:15px;margin:0;font-weight:650}.panel-sub{color:var(--muted);font-size:12px;margin-top:2px}.messages{padding:4px 17px 14px;max-height:68vh;overflow:auto}.message{padding:13px 0;border-bottom:1px solid #293029}.message:last-child{border:0}.message-meta{display:flex;gap:9px;align-items:center;color:var(--muted);font-size:12px}.speaker{font-weight:650;color:var(--blue)}.speaker.leader{color:var(--amber)}.message-text{white-space:pre-wrap;overflow-wrap:anywhere;margin-top:5px}.phase{color:var(--muted)}
.right{display:grid;gap:16px}.score-table{width:100%;border-collapse:collapse}.score-table th,.score-table td{text-align:left;padding:9px 12px;border-bottom:1px solid var(--line)}.score-table th{font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);font-weight:550}.score-table td:last-child,.score-table th:last-child{text-align:right}.activity{padding:3px 15px 12px;max-height:35vh;overflow:auto}.activity-item{padding:9px 0;border-bottom:1px solid #293029;font-size:13px}.activity-item:last-child{border:0}.activity-meta{font-size:11px;color:var(--muted);margin-bottom:2px}.research{padding:12px 15px}.research details{border-bottom:1px solid var(--line);padding:9px 0}.research details:last-child{border:0}.research summary{cursor:pointer;font-size:13px}.research p{font-size:13px;color:var(--muted);margin:8px 0 0;white-space:pre-wrap;overflow-wrap:anywhere}.empty{color:var(--muted);padding:28px 12px;text-align:center}.note{font-size:12px;color:var(--muted);margin:12px 0 0}.toggle{display:flex;align-items:center;gap:7px;font-size:12px;color:var(--muted)}.toggle input{accent-color:var(--green)}
@media(max-width:850px){header{flex-wrap:wrap;padding:14px}.toolbar{margin-left:0}main{padding:15px}.cards{grid-template-columns:repeat(2,minmax(120px,1fr))}.layout{grid-template-columns:1fr}.messages{max-height:none}.right{grid-template-columns:1fr}}
</style>
</head>
<body>
<header><h1><span class="brand">Swarm</span> Alignment · Game room</h1>
<select id="games" aria-label="Choose a game"></select>
<div class="toolbar"><span id="status" class="status">Connecting…</span><button id="refresh">Refresh</button></div></header>
<main id="main"><div class="empty">Looking for game logs…</div></main>
<script>
const gamesEl=document.getElementById('games'),main=document.getElementById('main'),statusEl=document.getElementById('status');
let selected='',visibleOnly=true,lastGame=null;
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
async function api(url){const r=await fetch(url,{cache:'no-store'});if(!r.ok)throw Error(await r.text());return r.json()}
function renderGame(g){lastGame=g;if(!g){main.innerHTML='<div class="empty">No game logs yet. Start a run in another terminal and this page will update.</div>';return}
 const c=g.config||{},current=Number(g.current_round||0),cutoff=Math.max(1,current-Number(c.board_window||5)+1);
 const posts=(visibleOnly?g.board.filter(p=>Number(p.round)>=cutoff):g.board);
 const scores=g.scores||{},agents=c.agents||Object.keys(scores),scoreRows=agents.map(a=>`<tr><td>${esc(a)}</td><td>${esc(scores[a]?.score??0)}</td><td>${esc(scores[a]?.last_outcome??'—')}</td></tr>`).join('');
 const board=posts.length?posts.map(p=>`<article class="message"><div class="message-meta"><span class="speaker ${p.agent==='Leaderboard'?'leader':''}">${esc(p.agent)}</span><span class="phase">Round ${esc(p.round)}${p.phase?` · Phase ${esc(p.phase)}`:''}</span></div><div class="message-text">${esc(p.text)}</div></article>`).join(''):'<div class="empty">No messages in this window yet.</div>';
 const activity=g.activity.length?g.activity.slice(-35).reverse().map(a=>`<div class="activity-item"><div class="activity-meta">${esc(a.label)} · Round ${esc(a.round)}${a.phase?` · Phase ${esc(a.phase)}`:''}${a.agent?` · ${esc(a.agent)}`:''}</div><div>${esc(a.text)}</div></div>`).join(''):'<div class="empty">No private actions or answers logged yet.</div>';
 const probes=g.probes.length?g.probes.map(p=>`<details><summary>${esc(p.agent)} · suspects ${esc((p.suspected_teammates||[]).join(', ')||'nobody')}${p.confidence==null?'':` · ${esc(p.confidence)}%`}</summary><p>${esc((p.evidence||[]).join('\n')||'No evidence provided.')}</p></details>`).join(''):'<div class="note">Post-game probes appear here when complete.</div>';
 const status=g.complete?'Complete':'Running';statusEl.textContent=`${status} · updating every 2s`;statusEl.className='status '+(g.complete?'':'live');
 main.innerHTML=`<section class="summary"><span class="pill ${g.complete?'':'live'}"><strong>${status}</strong></span><span class="pill">${esc(c.env||'game')} · seed ${esc(c.seed??'?')}</span><span class="pill">leaderboard: <strong>${esc(c.lb||'?')}</strong></span><span class="pill">${esc(c.turn_order||'?')} turns</span><span class="pill">${esc(c.agents?.length||0)} agents · ${esc(c.rounds||'?')} rounds</span><span class="pill">${g.event_count} events</span></section>
 <section class="cards"><div class="card"><div class="label">Round</div><div class="value">${current||0}<span class="panel-sub"> / ${esc(c.rounds||'?')}</span></div></div><div class="card"><div class="label">Team score</div><div class="value">${esc(g.team_total??0)}</div></div><div class="card"><div class="label">Messages</div><div class="value">${g.board.length}</div></div><div class="card"><div class="label">Last update</div><div class="value" style="font-size:16px">${esc(g.updated||'—')}</div></div></section>
 <section class="layout"><div class="panel"><div class="panel-head"><div><h2>Shared message board</h2><div class="panel-sub">Public posts visible to agents</div></div><label class="toggle"><input id="visibleOnly" type="checkbox" ${visibleOnly?'checked':''}> Agent-visible window</label></div><div class="messages">${board}</div></div>
 <div class="right"><div class="panel"><div class="panel-head"><div><h2>Leaderboard</h2><div class="panel-sub">Latest round score</div></div></div><table class="score-table"><thead><tr><th>Agent</th><th>Score</th><th>Latest</th></tr></thead><tbody>${scoreRows||'<tr><td colspan="3">No scores yet</td></tr>'}</tbody></table></div>
 <div class="panel"><div class="panel-head"><div><h2>Research activity</h2><div class="panel-sub">Private lookups, answers, and tool errors—not board messages</div></div></div><div class="activity">${activity}</div></div>
 <div class="panel"><div class="panel-head"><div><h2>Private end-game probes</h2><div class="panel-sub">Research view; agents do not see one another’s probes</div></div></div><div class="research">${probes}</div></div>
 <details class="panel research"><summary>Experiment details</summary><p>Privileged agent: ${esc(c.privileged_agent||'not set')}</p><p>Board window: ${esc(c.board_window||'?')} rounds. Agents keep only their own private notes.</p></details></div></section>
 <p class="note">This dashboard reads local event logs. It does not expose model prompts or chain-of-thought. Public posts are shown separately from researcher-only tool activity.</p>`;
 document.getElementById('visibleOnly').onchange=e=>{visibleOnly=e.target.checked;renderGame(lastGame)};
}
async function refresh(){try{const list=await api('/api/games');const old=selected;gamesEl.innerHTML='';for(const g of list){const o=document.createElement('option');o.value=g.id;o.textContent=`${g.name} · ${g.complete?'complete':'running'} · round ${g.current_round||0}`;gamesEl.appendChild(o)}if(!list.length){selected='';renderGame(null);statusEl.textContent='Waiting for logs';return}selected=list.some(g=>g.id===old)?old:list[0].id;gamesEl.value=selected;renderGame(await api('/api/game?id='+encodeURIComponent(selected)))}catch(e){statusEl.textContent='Dashboard error';main.innerHTML=`<div class="empty">${esc(e.message)}</div>`}}
gamesEl.onchange=()=>{selected=gamesEl.value;refresh()};document.getElementById('refresh').onclick=refresh;refresh();setInterval(refresh,2000);
</script>
</body>
</html>"""


def _log_files(results: Path) -> dict[str, Path]:
    files = [*results.rglob("*.jsonl"), *results.rglob("*.jsonl.tmp")]
    return {path.relative_to(results).as_posix(): path for path in files if path.is_file()}


def _read_game(path: Path, game_id: str) -> dict[str, Any]:
    events: list[dict[str, Any]] = []
    for line in path.read_text().splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append(event)

    config = next((e for e in events if e.get("type") == "config"), {})
    scores: dict[str, dict[str, Any]] = {}
    board: list[dict[str, Any]] = []
    activity: list[dict[str, Any]] = []
    probes: list[dict[str, Any]] = []
    current_round = 0
    team_total = 0
    for event in events:
        kind = event.get("type")
        current_round = max(current_round, int(event.get("round", 0) or 0))
        if kind == "post":
            board.append(
                {
                    "round": event.get("round"),
                    "phase": event.get("phase"),
                    "agent": event.get("agent", "Unknown"),
                    "text": event.get("stored_text", event.get("text", "")),
                }
            )
        elif kind == "round_score":
            team_total = event.get("team_total", team_total)
            for agent, detail in event.get("per_agent", {}).items():
                scores[agent] = {
                    "score": event.get("cumulative_scores", {}).get(agent, 0),
                    "last_outcome": detail.get("outcome", "—"),
                }
        elif kind == "tool" and event.get("tool") == "lookup_directory":
            activity.append(
                {
                    "label": "Private lookup",
                    "round": event.get("round"),
                    "phase": event.get("phase"),
                    "agent": event.get("agent"),
                    "text": event.get("result", ""),
                }
            )
        elif kind == "answer":
            activity.append(
                {
                    "label": "Answer",
                    "round": event.get("round"),
                    "phase": event.get("phase"),
                    "agent": event.get("agent"),
                    "text": f"{event.get('question_id', '?')} → {event.get('answer', '?')}",
                }
            )
        elif kind == "invalid_tool":
            activity.append(
                {
                    "label": "Tool error",
                    "round": event.get("round"),
                    "phase": event.get("phase"),
                    "agent": event.get("agent"),
                    "text": f"Unavailable tool: {event.get('tool', '?')}",
                }
            )
        elif kind == "probe":
            probes.append(
                {
                    "agent": event.get("agent", "Unknown"),
                    "suspected_teammates": event.get("suspected_teammates", []),
                    "confidence": event.get("confidence"),
                    "evidence": event.get("evidence", []),
                }
            )

    complete = any(event.get("type") == "end" for event in events)
    modified = path.stat().st_mtime
    updated = datetime.fromtimestamp(modified).astimezone().strftime("%H:%M:%S")
    public_config = {
        key: config.get(key)
        for key in (
            "env",
            "seed",
            "lb",
            "rounds",
            "board_window",
            "turn_order",
            "agents",
            "privileged_agent",
        )
    }
    return {
        "id": game_id,
        "name": path.name.removesuffix(".jsonl.tmp").removesuffix(".jsonl"),
        "complete": complete,
        "config": public_config,
        "current_round": current_round,
        "team_total": team_total,
        "scores": scores,
        "board": board,
        "activity": activity,
        "probes": probes,
        "event_count": len(events),
        "updated": updated,
    }


class DashboardHandler(BaseHTTPRequestHandler):
    results_dir: Path

    def do_GET(self) -> None:
        request = urlparse(self.path)
        if request.path == "/":
            body = DASHBOARD.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if request.path == "/api/games":
            games = []
            for game_id, path in _log_files(self.results_dir).items():
                game = _read_game(path, game_id)
                games.append(
                    {
                        key: game[key]
                        for key in (
                            "id",
                            "name",
                            "complete",
                            "current_round",
                            "config",
                            "updated",
                        )
                    }
                    | {"modified": path.stat().st_mtime}
                )
            games.sort(key=lambda game: game["modified"], reverse=True)
            for game in games:
                game.pop("modified")
            self._send_json(games)
            return
        if request.path == "/api/game":
            game_id = parse_qs(request.query).get("id", [""])[0]
            path = _log_files(self.results_dir).get(game_id)
            if path is None:
                self._send_json({"error": "Game log not found"}, 404)
            else:
                self._send_json(_read_game(path, game_id))
            return
        self._send_json({"error": "Not found"}, 404)

    def _send_json(self, data: Any, status: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        return


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, default=Path("results"))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    args.results.mkdir(parents=True, exist_ok=True)
    handler = type(
        "BoundDashboardHandler",
        (DashboardHandler,),
        {"results_dir": args.results.resolve()},
    )
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(
        f"Dashboard: http://{args.host}:{args.port}  (logs: {args.results.resolve()})",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Dashboard stopped.", flush=True)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
