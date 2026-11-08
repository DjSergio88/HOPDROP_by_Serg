#!/usr/bin/env python3
"""
HopDrop - send big files (up to 12 GB by default) from your phone to your PC.

Run on the PC:      python hopdrop.py
Then on the phone:  open the address it prints (phone and PC on the same Wi-Fi).

Features
  * Resumable chunked uploads: if Wi-Fi drops, the upload continues where it stopped
  * No dependencies (Python 3.7+ standard library only)
  * PIN protection so only you can upload
  * Files are streamed straight to disk, never held in RAM
"""
import argparse
import json
import os
import re
import secrets
import shutil
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

GB = 1024 ** 3
READ_BLOCK = 1024 * 1024

CONFIG = {"dir": "", "max_bytes": 12 * GB, "pin": None}
_locks = {}
_locks_guard = threading.Lock()
_finalize_lock = threading.Lock()


def name_lock(name):
    with _locks_guard:
        return _locks.setdefault(name, threading.Lock())


def clean_name(raw):
    name = os.path.basename((raw or "").replace("\\", "/"))
    name = re.sub(r'[<>:"|?*\x00-\x1f]', "_", name).strip(". ")
    return name[:200] or "file"


def unique_path(directory, name):
    base, ext = os.path.splitext(name)
    path = os.path.join(directory, name)
    i = 1
    while os.path.exists(path):
        path = os.path.join(directory, "%s (%d)%s" % (base, i, ext))
        i += 1
    return path


PAGE = r"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>HopDrop</title>
<style>
:root{--bg:#0f1115;--card:#181b22;--line:#2a2f3a;--fg:#e8eaf0;--mut:#8b93a5;--acc:#4f8cff;--ok:#38c172;--err:#ff5d5d}
@media (prefers-color-scheme:light){:root{--bg:#f4f6fa;--card:#fff;--line:#dde2ec;--fg:#14171f;--mut:#667085}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.45 system-ui,-apple-system,Segoe UI,sans-serif;padding:16px}
main{max-width:560px;margin:0 auto}
h1{font-size:22px;margin:8px 0 2px}
p.sub{color:var(--mut);margin:0 0 16px}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px;margin-bottom:12px}
input[type=password],input[type=text],input[type=tel]{width:100%;padding:12px;border-radius:10px;border:1px solid var(--line);background:transparent;color:var(--fg);font-size:18px;letter-spacing:4px;text-align:center}
.drop{border:2px dashed var(--line);border-radius:14px;padding:28px 12px;text-align:center;cursor:pointer}
.drop.over{border-color:var(--acc)}
.drop b{display:block;font-size:18px}
.drop span{color:var(--mut);font-size:14px}
button{background:var(--acc);color:#fff;border:0;border-radius:10px;padding:12px 18px;font-size:16px;font-weight:600;width:100%;margin-top:10px}
button:disabled{opacity:.5}
.row{margin-top:12px}
.top{display:flex;justify-content:space-between;gap:8px;font-size:14px}
.name{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.mut{color:var(--mut);white-space:nowrap}
.bar{height:8px;background:var(--line);border-radius:6px;overflow:hidden;margin-top:6px}
.bar i{display:block;height:100%;width:0;background:var(--acc);transition:width .2s}
.ok .bar i{background:var(--ok)}.err .bar i{background:var(--err)}
.msg{font-size:13px;margin-top:4px;color:var(--mut)}
.err .msg{color:var(--err)}.ok .msg{color:var(--ok)}
</style></head><body><main>
<h1>HopDrop</h1>
<p class="sub">Send large files to your PC. Max __MAX__ per file. Keep this page open until it finishes.</p>

<div class="card" id="pinCard" style="__PINSTYLE__">
  <label for="pin" class="mut">PIN shown on your PC</label>
  <input id="pin" type="tel" inputmode="numeric" autocomplete="off" maxlength="8" placeholder="••••">
</div>

<div class="card">
  <div class="drop" id="drop"><b>Tap to choose files</b><span>or drop them here</span></div>
  <input id="file" type="file" multiple hidden>
  <button id="go" disabled>Upload</button>
</div>

<div id="list"></div>
</main>
<script>
const MAX=__MAXBYTES__, CHUNK=32*1024*1024;
const $=s=>document.querySelector(s);
let files=[], wake=null;
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
const fmt=n=>n>=1073741824?(n/1073741824).toFixed(2)+' GB':n>=1048576?(n/1048576).toFixed(1)+' MB':Math.max(1,Math.round(n/1024))+' KB';
try{$('#pin').value=sessionStorage.getItem('pin')||''}catch(e){}
const pin=()=>{const v=$('#pin').value.trim();try{sessionStorage.setItem('pin',v)}catch(e){}return v};

$('#drop').onclick=()=>$('#file').click();
$('#file').onchange=e=>add(e.target.files);
['dragover','dragenter'].forEach(t=>$('#drop').addEventListener(t,e=>{e.preventDefault();$('#drop').classList.add('over')}));
['dragleave','drop'].forEach(t=>$('#drop').addEventListener(t,e=>{e.preventDefault();$('#drop').classList.remove('over')}));
$('#drop').addEventListener('drop',e=>add(e.dataTransfer.files));

function add(fl){
  for(const f of fl){
    const row=document.createElement('div');row.className='card row';
    row.innerHTML='<div class="top"><span class="name"></span><span class="mut"></span></div><div class="bar"><i></i></div><div class="msg">Ready</div>';
    row.querySelector('.name').textContent=f.name;
    row.querySelector('.mut').textContent=fmt(f.size);
    $('#list').appendChild(row);
    files.push({f,row});
    if(f.size>MAX){setState(row,'err','Too large (max '+fmt(MAX)+')');files.pop()}
  }
  $('#go').disabled=!files.length;
}
function setState(row,cls,msg){row.classList.remove('ok','err');if(cls)row.classList.add(cls);row.querySelector('.msg').textContent=msg}
function setPct(row,p){row.querySelector('.bar i').style.width=p+'%'}

async function status(name){
  const r=await fetch('/status?name='+encodeURIComponent(name)+'&pin='+encodeURIComponent(pin()));
  if(r.status===401){const e=new Error('Wrong PIN');e.fatal=true;throw e}
  return (await r.json()).size;
}
function sendChunk(file,offset,end,final,onprog){
  return new Promise((res,rej)=>{
    const x=new XMLHttpRequest();
    x.open('POST','/chunk?name='+encodeURIComponent(file.name)+'&offset='+offset+'&total='+file.size+'&final='+(final?1:0)+'&pin='+encodeURIComponent(pin()));
    x.upload.onprogress=e=>onprog(e.loaded);
    x.onload=()=>{
      if(x.status===200)return res(JSON.parse(x.responseText));
      if(x.status===409)return res({resync:JSON.parse(x.responseText).size});
      const e=new Error(x.responseText?(JSON.parse(x.responseText).error||x.status):x.status);
      e.fatal=[401,413,507].includes(x.status);rej(e);
    };
    x.onerror=()=>rej(new Error('Connection lost'));
    x.ontimeout=()=>rej(new Error('Timed out'));
    x.send(file.slice(offset,end));
  });
}
async function uploadOne(f,row){
  let offset=0;
  try{offset=await status(f.name)}catch(e){if(e.fatal)throw e}
  if(offset>f.size)offset=0;
  const t0=Date.now(),start=offset;let retries=0,finished=false;
  while(!finished){
    const end=Math.min(offset+CHUNK,f.size),final=end>=f.size;
    try{
      setState(row,'','Uploading…');
      const r=await sendChunk(f,offset,end,final,loaded=>{
        const done=offset+loaded;
        setPct(row,f.size?done/f.size*100:100);
        const sp=(done-start)/Math.max(1,(Date.now()-t0)/1000);
        setState(row,'',fmt(done)+' of '+fmt(f.size)+' · '+fmt(sp)+'/s');
      });
      if(r.resync!==undefined){offset=r.resync;continue}
      retries=0;offset=end;
      if(final){finished=true;setPct(row,100);setState(row,'ok','Saved on PC as '+(r.saved||f.name))}
    }catch(e){
      if(e.fatal)throw e;
      if(++retries>40)throw e;
      setState(row,'','Connection problem, retrying ('+retries+')…');
      await sleep(Math.min(1000*retries,8000));
      try{offset=await status(f.name)}catch(_){}
    }
  }
}
$('#go').onclick=async()=>{
  $('#go').disabled=true;
  try{if('wakeLock' in navigator)wake=await navigator.wakeLock.request('screen')}catch(e){}
  const queue=files;files=[];
  for(const {f,row} of queue){
    try{await uploadOne(f,row)}catch(e){setState(row,'err',e.message||'Failed')}
  }
  try{wake&&wake.release()}catch(e){}
  $('#go').disabled=!files.length;
};
</script></body></html>
"""


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    timeout = 60

    def log_message(self, fmt, *args):
        pass

    # -- helpers ---------------------------------------------------------
    def send_json(self, code, obj, close=False):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        if close:
            self.send_header("Connection", "close")
            self.close_connection = True
        self.end_headers()
        self.wfile.write(body)

    def authed(self, q):
        pin = CONFIG["pin"]
        if not pin:
            return True
        return secrets.compare_digest(q.get("pin", [""])[0], pin)

    # -- routes ----------------------------------------------------------
    def do_GET(self):
        url = urlparse(self.path)
        q = parse_qs(url.query)
        if url.path == "/":
            html = (PAGE.replace("__MAXBYTES__", str(CONFIG["max_bytes"]))
                        .replace("__MAX__", "%g GB" % (CONFIG["max_bytes"] / GB))
                        .replace("__PINSTYLE__", "" if CONFIG["pin"] else "display:none"))
            body = html.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif url.path == "/status":
            if not self.authed(q):
                return self.send_json(401, {"error": "Wrong PIN"})
            name = clean_name(q.get("name", [""])[0])
            part = os.path.join(CONFIG["dir"], name + ".part")
            size = os.path.getsize(part) if os.path.exists(part) else 0
            self.send_json(200, {"size": size})
        else:
            self.send_json(404, {"error": "Not found"})

    def do_POST(self):
        url = urlparse(self.path)
        q = parse_qs(url.query)
        if url.path != "/chunk":
            return self.send_json(404, {"error": "Not found"}, close=True)
        if not self.authed(q):
            return self.send_json(401, {"error": "Wrong PIN"}, close=True)
        try:
            name = clean_name(q["name"][0])
            offset = int(q["offset"][0])
            total = int(q["total"][0])
            final = q.get("final", ["0"])[0] == "1"
            length = int(self.headers.get("Content-Length", "-1"))
            if min(offset, total) < 0 or length < 0 or offset + length > total:
                raise ValueError
        except (KeyError, ValueError):
            return self.send_json(400, {"error": "Bad request"}, close=True)
        if total > CONFIG["max_bytes"]:
            return self.send_json(413, {"error": "File exceeds %g GB limit" % (CONFIG["max_bytes"] / GB)}, close=True)

        directory = CONFIG["dir"]
        part = os.path.join(directory, name + ".part")
        lock = name_lock(name)
        if not lock.acquire(timeout=70):
            return self.send_json(503, {"error": "Busy"}, close=True)
        try:
            if offset == 0:
                if shutil.disk_usage(directory).free < total + 100 * 1024 * 1024:
                    return self.send_json(507, {"error": "Not enough free disk space on the PC"}, close=True)
                mode = "wb"
            else:
                size = os.path.getsize(part) if os.path.exists(part) else 0
                if size != offset:
                    return self.send_json(409, {"size": size}, close=True)
                mode = "r+b"
            try:
                with open(part, mode) as out:
                    out.seek(offset)
                    remaining = length
                    while remaining > 0:
                        block = self.rfile.read(min(READ_BLOCK, remaining))
                        if not block:
                            raise ConnectionError("client disconnected")
                        out.write(block)
                        remaining -= len(block)
            except (ConnectionError, OSError, socket.timeout):
                self.close_connection = True
                return
            saved = None
            if final:
                if os.path.getsize(part) != total:
                    return self.send_json(409, {"size": os.path.getsize(part)}, close=True)
                with _finalize_lock:
                    dest = unique_path(directory, name)
                    os.replace(part, dest)
                saved = os.path.basename(dest)
                print("  received: %s (%.2f GB)" % (saved, total / GB))
            self.send_json(200, {"ok": True, "saved": saved})
        finally:
            lock.release()


def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def main():
    ap = argparse.ArgumentParser(description="Receive big files from your phone.")
    ap.add_argument("--dir", default=os.path.join(os.path.expanduser("~"), "Downloads", "HopDrop"),
                    help="where received files are saved")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--max-gb", type=float, default=12, help="max size per file in GB (default 12)")
    ap.add_argument("--no-pin", action="store_true", help="disable the PIN (not recommended)")
    args = ap.parse_args()

    os.makedirs(args.dir, exist_ok=True)
    CONFIG["dir"] = os.path.abspath(args.dir)
    CONFIG["max_bytes"] = int(args.max_gb * GB)
    CONFIG["pin"] = None if args.no_pin else "%04d" % secrets.randbelow(10000)

    server = ThreadingHTTPServer(("0.0.0.0", args.port), Handler)
    server.daemon_threads = True
    print("\n  HopDrop is running")
    print("  Open on your phone:  http://%s:%d" % (lan_ip(), args.port))
    if CONFIG["pin"]:
        print("  PIN:                 %s" % CONFIG["pin"])
    print("  Saving to:           %s" % CONFIG["dir"])
    print("  Max file size:       %g GB\n  (Ctrl+C to stop)\n" % args.max_gb)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Stopped.")


if __name__ == "__main__":
    main()
