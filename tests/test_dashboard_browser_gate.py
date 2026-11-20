"""Real headless Chromium smoke, isolated profiles and synthetic HTTP/file payloads.

No browser package/download is required. This gate runs when Edge/Chrome exists.
"""

import functools
import http.server
import os
import shutil
import subprocess
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import pytest
from template_renderer import DashboardRenderer


def _browser():
    candidates = [
        shutil.which(name)
        for name in ("msedge", "chromium", "chromium-browser", "google-chrome")
    ]
    candidates += [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    ]
    return next(
        (str(path) for path in candidates if path and Path(path).is_file()), None
    )


@contextmanager
def _server(directory, requests):
    class Handler(http.server.SimpleHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            try:
                if self.path.startswith("/api/"):
                    blob = (
                        b'{"success":true,"data":[],"excel_pending":0,"excel_failed":0}'
                    )
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(blob)))
                    self.send_header("Connection", "close")
                    self.end_headers()
                    self.wfile.write(blob)
                    self.wfile.flush()
                else:
                    super().do_GET()
            finally:
                self.close_connection = True

        def log_message(self, *_):
            pass

    server = http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0), functools.partial(Handler, directory=str(directory))
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


@pytest.mark.skipif(
    not _browser() or not shutil.which("node"),
    reason="Installed Chromium and Node are required; no download",
)
@pytest.mark.parametrize("mode", ["http", "file"])
def test_single_html_modal_full_body_and_no_remote_image_requests(tmp_path, mode):
    root = Path(__file__).resolve().parents[1]
    pixels, page_requests = [], []
    with (
        _server(tmp_path, pixels) as collector,
        _server(tmp_path, page_requests) as origin,
    ):
        image = f'<img src="{collector.replace("http:", "")}/pixel">'
        answer = "QA-END-TAIL"
        body = (
            "본문 " * 12000
            + "FULL-END-TAIL\n\n"
            + image
            + "\n\n<table><tr><td>TABLE-CELL</td></tr></table>\n\n"
            + answer
        )
        assert len(body) > 30000
        raw = {
            "documents": [
                {
                    "doc_id": "DOC-SYN",
                    "title": "synthetic",
                    "year": "2026",
                    "requester": "synthetic",
                    "full_markdown": body,
                    "has_tables": "Y",
                    "table_count": 1,
                }
            ],
            "qa_items": [
                {
                    "qa_id": "QA-SYN",
                    "doc_id": "DOC-SYN",
                    "question_title": "synthetic",
                    "q_num": 1,
                    "answer_markdown": answer,
                    "year": "2026",
                }
            ],
            "request_ledger": [],
        }
        raw["documents"].append(
            {
                "doc_id": "DOC-COMPARE",
                "title": "compare",
                "year": "2025",
                "requester": "synthetic",
                "full_markdown": body + "COMPARE-END-TAIL",
                "has_tables": "Y",
                "table_count": 1,
            }
        )
        renderer = DashboardRenderer()
        meta, chunks = renderer.encode_payload_parts(
            renderer.build_slim_payload(raw), threshold=1000, chunk_chars=1500
        )
        html = renderer.render(
            renderer.load_template(),
            (root / "scripts/marked.min.js").read_text(encoding="utf-8"),
            meta,
            {"department_name": "synthetic"},
            chunks,
        )
        assert renderer.verify_offline_dashboard_html(html) == (True, "")
        probe = r"""
<script>
window.__auditErrors=[];
window.addEventListener('error',e=>window.__auditErrors.push(e.message));
(async()=>{
 try {
  for(let i=0;i<100 && (!DOCUMENTS.length || !DOCUMENTS[0].full_markdown);i++) await new Promise(r=>setTimeout(r,50));
  await openDocModal(DOCUMENTS[0]);
  const raw=document.getElementById('modal-text-content').textContent;
  const formatted=document.getElementById('modal-view-formatted').textContent;
  const qa=document.getElementById('modal-view-qa').textContent;
  if(!raw.includes('FULL-END-TAIL') || !formatted.includes('TABLE-CELL') || !qa.includes('QA-END-TAIL')) throw Error('body or table missing');
  if(!document.querySelector('#modal-view-formatted table') || typeof marked.parse!=='function') throw Error('Markdown library did not render a real table');
  if(document.querySelector('#modal-view-formatted img[src^="//"]')) throw Error('remote image survived');
  const originalCopy=window.copyText;let copied='';window.copyText=text=>{copied=text;};
  document.getElementById('btn-copy-full').click();window.copyText=originalCopy;
  if(!copied.includes('FULL-END-TAIL'))throw Error('full-body copy is truncated');
  openComparePicker();document.getElementById('compare-select-b').value='DOC-COMPARE';await updateCompareRightPane();
  if(!document.getElementById('compare-pane-right').textContent.includes('COMPARE-END-TAIL'))throw Error('compare is truncated');
  closeCompareModal();
  if(window.__auditErrors.length) throw Error(window.__auditErrors.join(';'));
  document.body.setAttribute('data-audit','passed');
 } catch(e) {document.body.setAttribute('data-audit','failed:'+e.message);}
})();
</script>
"""
        page = tmp_path / "synthetic.html"
        # Print helpers contain their own HTML closing body inside JS strings.
        # Inject at the final document boundary only.
        head, tail = html.rsplit("</body>", 1)
        page.write_text(head + probe + "</body>" + tail, encoding="utf-8")
        url = origin + "/synthetic.html" if mode == "http" else page.as_uri()
        profile = tmp_path / ("browser-profile-" + mode)
        # CDP waits on real page state. Virtual-time dump-dom can stall while
        # dashboard API polling is active, and inherited Windows pipes may stay
        # open in browser helpers after the launcher exits.
        driver = tmp_path / "browser-driver.js"
        driver.write_text(
            r"""
const fs=require('fs');
const delay=ms=>new Promise(r=>setTimeout(r,ms));
async function connect(url) {
 const socket=new WebSocket(url),pending=new Map(),events=[],handlers=[];let id=0;
 socket.onmessage=e=>{const m=JSON.parse(e.data);if(m.id && pending.has(m.id)){const p=pending.get(m.id);pending.delete(m.id);m.error?p.reject(Error(JSON.stringify(m.error))):p.resolve(m.result);}else {if(m.method==='Runtime.exceptionThrown')events.push({error:m.params.exceptionDetails});for(const handler of handlers)handler(m);}};
 await new Promise((resolve,reject)=>{socket.onopen=resolve;socket.onerror=reject;});
 return {socket,events,onEvent:fn=>handlers.push(fn),send:(method,params={})=>new Promise((resolve,reject)=>{const n=++id;pending.set(n,{resolve,reject});socket.send(JSON.stringify({id:n,method,params}));})};
}
(async()=>{
 const [port,path]=fs.readFileSync(process.argv[2],'utf8').trim().split(/\r?\n/);
 const browser=await connect('ws://127.0.0.1:'+port+path);
 let page;
 try {
  const pages=await (await fetch('http://127.0.0.1:'+port+'/json/list')).json();
  page=await connect(pages.find(p=>p.type==='page').webSocketDebuggerUrl);
  await page.send('Page.enable');await page.send('Runtime.enable');await page.send('Network.enable');
  if(process.argv[3].startsWith('http:')) {
   // Only the generated document is mocked. Real API requests and the other
   // localhost image origin still use sockets, so remote src regressions fail.
   page.onEvent(m=>{if(m.method==='Fetch.requestPaused') {
    page.send('Fetch.fulfillRequest',{requestId:m.params.requestId,responseCode:200,
     responseHeaders:[{name:'Content-Type',value:'text/html; charset=utf-8'}],
     body:fs.readFileSync(process.argv[4]).toString('base64')}).catch(e=>page.events.push({error:String(e)}));
   }});
   await page.send('Fetch.enable',{patterns:[{urlPattern:process.argv[3],requestStage:'Request'}]});
  }
  const navigation=await page.send('Page.navigate',{url:process.argv[3]});
  if(navigation.errorText)throw Error(navigation.errorText);
  let state='';
  for(let i=0;i<160;i++) {
   const value=await page.send('Runtime.evaluate',{expression:'document.body ? document.body.dataset.audit || "" : ""',returnByValue:true});
   state=value.result.value;
   if(state)break;
   await delay(50);
  }
  if(state!=='passed') {
   const detail=await page.send('Runtime.evaluate',{expression:'JSON.stringify({loc:location.href,ready:document.readyState,docs:typeof DOCUMENTS!=="undefined"?DOCUMENTS.length:-1,loading:document.getElementById("app-loading-text")?.textContent,bodyError:typeof BODY_LOAD_ERROR!=="undefined"?BODY_LOAD_ERROR:""})',returnByValue:true});
   throw Error('Browser gate: '+(state || 'page not ready')+' '+detail.result.value+' '+JSON.stringify(page.events));
  }
  if(page.events.some(e=>e.error))throw Error(JSON.stringify(page.events));
  console.log('BROWSER_GATE_PASS');
 } finally {
  if(page)page.socket.close();
  await browser.send('Browser.close');browser.socket.close();
 }
})().catch(e=>{console.error(e);process.exitCode=1;});
""",
            encoding="utf-8",
        )
        proc = subprocess.Popen(
            [
                _browser(),
                "--headless=new",
                "--disable-gpu",
                "--disable-background-networking",
                "--disable-sync",
                "--no-proxy-server",
                "--no-first-run",
                "--no-default-browser-check",
                "--user-data-dir=" + str(profile),
                "--remote-debugging-port=0",
                "about:blank",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        try:
            deadline = time.monotonic() + 10
            active_port = profile / "DevToolsActivePort"
            while not active_port.exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            assert active_port.exists(), (
                "Browser did not open its isolated debugging port"
            )
            run = subprocess.run(
                [shutil.which("node"), str(driver), str(active_port), url, str(page)],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=20,
                check=False,
            )
            assert run.returncode == 0, run.stderr + repr(page_requests)
            assert "BROWSER_GATE_PASS" in run.stdout
            proc.wait(timeout=10)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=5)
        assert not pixels, (
            f"The document attempted an automatic request to another origin: {pixels!r}"
        )
