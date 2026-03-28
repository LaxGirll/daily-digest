#!/usr/bin/env python3
"""
publish_digest.py
Encrypts the daily digest with AES-256-CBC and outputs a JSON payload
ready for the GitHub API (to be pushed via browser fetch).

Usage: python3 publish_digest.py "digest text"
       cat digest.txt | python3 publish_digest.py
Output: JSON to stdout with {api_url, token, commit_message, content_b64, site_url}
"""

import sys, os, json, base64, hashlib, subprocess, tempfile
from pathlib import Path
from datetime import datetime

# ── Config ─────────────────────────────────────────────────────────────────────
CONFIG_PATH = Path(__file__).parent / ".digest-config"
config = {}
if CONFIG_PATH.exists():
    for line in CONFIG_PATH.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            config[k.strip()] = v.strip()

GITHUB_TOKEN = config.get("GITHUB_TOKEN", "")
GITHUB_USER  = config.get("GITHUB_USER", "LaxGirll")
GITHUB_REPO  = config.get("GITHUB_REPO", "daily-digest")
PASSWORD     = os.environ.get("DIGEST_PASSWORD") or config.get("DIGEST_PASSWORD", "")

# ── Digest content ─────────────────────────────────────────────────────────────
if len(sys.argv) > 1:
    digest_text = " ".join(sys.argv[1:])
else:
    digest_text = sys.stdin.read()

date_str = datetime.now().strftime("%A, %B %-d, %Y")

# ── Build inner HTML ───────────────────────────────────────────────────────────
CSS = (
    "*{box-sizing:border-box;margin:0;padding:0;}"
    "body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;"
    "background:#f0f2f5;color:#1a1a2e;padding:16px;line-height:1.6;}"
    ".wrap{max-width:680px;margin:0 auto;}"
    "header{background:linear-gradient(135deg,#1a1a2e,#16213e);color:white;"
    "border-radius:12px;padding:20px 24px;margin-bottom:20px;}"
    "header h1{font-size:1.3rem;font-weight:700;}"
    "header .dsub{font-size:.85rem;opacity:.7;margin-top:4px;}"
    ".card{background:white;border-radius:12px;padding:20px 24px;"
    "box-shadow:0 1px 4px rgba(0,0,0,.07);margin-bottom:16px;}"
    ".sec-title{font-size:.7rem;font-weight:700;text-transform:uppercase;"
    "letter-spacing:.1em;color:#888;margin-bottom:14px;padding-bottom:10px;"
    "border-bottom:1px solid #f0f0f0;}"
    ".email-sub{font-size:.82rem;color:#888;margin-bottom:10px;}"
    ".count-row{display:flex;justify-content:space-between;align-items:center;"
    "padding:7px 0;border-bottom:1px solid #f7f7f7;}"
    ".count-row:last-child{border-bottom:none;}"
    ".cat-name{font-size:.88rem;}"
    ".badge{background:#f0f4ff;color:#3b4fd8;border-radius:20px;padding:3px 12px;"
    "font-size:.78rem;font-weight:600;white-space:nowrap;}"
    ".badge.hot{background:#fff1f0;color:#cf1322;}"
    ".attn{background:#fffbeb;border-left:3px solid #f59e0b;border-radius:0 6px 6px 0;"
    "padding:10px 14px;margin-bottom:8px;font-size:.88rem;line-height:1.5;}"
    ".attn:last-child{margin-bottom:0;}"
    ".nl{padding-bottom:18px;margin-bottom:18px;border-bottom:1px solid #f0f0f0;}"
    ".nl:last-child{padding-bottom:0;margin-bottom:0;border-bottom:none;}"
    ".nl-head{margin-bottom:6px;}"
    ".nl-source{font-weight:700;font-size:.9rem;color:#1a1a2e;}"
    ".nl-title{color:#666;font-size:.87rem;}"
    ".nl-body{font-size:.87rem;color:#444;line-height:1.6;margin-bottom:10px;}"
    ".pm{background:#eef2ff;border-left:3px solid #4f46e5;border-radius:0 6px 6px 0;"
    "padding:10px 14px;font-size:.84rem;color:#312e81;line-height:1.6;}"
    ".pm strong{display:block;margin-bottom:4px;font-size:.72rem;text-transform:uppercase;"
    "letter-spacing:.08em;color:#4f46e5;}"
    ".pre-body{white-space:pre-wrap;font-size:.88rem;color:#444;line-height:1.7;}"
)

# Raw string so backslashes pass through to JavaScript unchanged
JS_TMPL = r"""var D=PAYLOAD_JSON;
function esc(s){return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}
function rIntro(sec){
  var lines=sec.split('\n'),inC=false,rows='',esub='';
  lines.forEach(function(l){
    var t=l.trim();if(!t)return;
    if(/^DAILY DIGEST/.test(t))return;
    if(/^\d+ emails? scanned/i.test(t)){esub=t;return;}
    if(/^EMAIL COUNT BY CATEGORY/i.test(t)){inC=true;return;}
    if(inC){
      var m=t.match(/^(.+?):\s+(\d+)\s+(\S+)(.*?)(?:\s+<-\s+(.+))?$/);
      if(m){
        var h=m[5]?' hot':'',s=m[5]?' \u2605':'';
        rows+='<div class="count-row"><span class="cat-name">'+esc(m[1])+'</span>'
          +'<span class="badge'+h+'">'+esc(m[2])+' '+esc(m[3])+s+'</span></div>';
      }
    }
  });
  if(!rows)return'';
  return'<div class="card"><div class="sec-title">Email counts</div>'
    +(esub?'<div class="email-sub">'+esc(esub)+'</div>':'')+rows+'</div>';
}
function rAttn(body){
  var items='';
  body.split('\n').forEach(function(l){
    var t=l.trim();if(!t)return;
    items+='<div class="attn">'+esc(t.startsWith('- ')?t.slice(2):t)+'</div>';
  });
  return'<div class="card"><div class="sec-title">&#9889; Needs attention today</div>'+items+'</div>';
}
function rNL(title,body){
  var eL=body.split('\n'),entries=[],cur=[];
  eL.forEach(function(l){
    if(l.includes(' -- "')&&/^[A-Z]/.test(l.trim())){
      if(cur.length)entries.push(cur.join('\n'));cur=[l];
    }else cur.push(l);
  });
  if(cur.length)entries.push(cur.join('\n'));
  var cards=entries.map(function(e){
    var el=e.trim();if(!el)return'';
    var el2=el.split('\n'),hm=el2[0].match(/^(.+?)\s+--\s+"(.+)"$/);
    if(!hm)return'<div class="nl"><div class="pre-body">'+esc(el)+'</div></div>';
    var bL=[],pL=[],inP=false;
    el2.slice(1).forEach(function(l){
      if(l.startsWith('PM angle:')){inP=true;pL.push(l.slice(9).trim());}
      else if(inP)pL.push(l);else bL.push(l);
    });
    var c='<div class="nl"><div class="nl-head"><span class="nl-source">'+esc(hm[1])+'</span>'
      +' <span class="nl-title">\u201c'+esc(hm[2])+'\u201d</span></div>';
    var bt=bL.join('\n').trim();if(bt)c+='<div class="nl-body">'+esc(bt)+'</div>';
    var pt=pL.join(' ').trim();
    if(pt)c+='<div class="pm"><strong>PM angle</strong>'+esc(pt)+'</div>';
    return c+'</div>';
  }).join('');
  return'<div class="card"><div class="sec-title">'+esc(title)+'</div>'+cards+'</div>';
}
function rGen(title,body){
  var b=esc(body).split('\n').map(function(l){
    return l.startsWith('- ')?'\u2022 '+l.slice(2):l;
  }).join('\n');
  return'<div class="card"><div class="sec-title">'+esc(title)+'</div>'
    +'<div class="pre-body">'+b+'</div></div>';
}
function renderDigest(raw){
  var secs=raw.split('\n---\n'),html='';
  secs.forEach(function(sec,idx){
    var s=sec.trim();if(!s)return;
    if(idx===0){html+=rIntro(s);return;}
    var lines=s.split('\n'),title=lines[0].trim(),body=lines.slice(1).join('\n').trim();
    if(/NEEDS ATTENTION/i.test(title))html+=rAttn(body);
    else if(/NEWSLETTER|WORK INTEL/i.test(title))html+=rNL(title,body);
    else html+=rGen(title,body);
  });
  return html;
}
document.getElementById('digest').innerHTML=renderDigest(D);"""

js = JS_TMPL.replace('PAYLOAD_JSON', json.dumps(digest_text))

inner_html = (
    "<!DOCTYPE html><html lang='en'><head>"
    "<meta charset='UTF-8'>"
    "<meta name='viewport' content='width=device-width, initial-scale=1.0'>"
    "<title>Daily Digest - " + date_str + "</title>"
    "<style>" + CSS + "</style>"
    "</head><body>"
    "<div class='wrap'>"
    "<header><h1>&#9728;&#65039; Daily Digest</h1>"
    "<div class='dsub'>" + date_str + "</div></header>"
    "<div id='digest'></div>"
    "</div>"
    "<script>" + js + "</script>"
    "</body></html>"
)

# ── AES-256-CBC Encryption ─────────────────────────────────────────────────────
salt = os.urandom(32)
iv   = os.urandom(16)
key  = hashlib.pbkdf2_hmac('sha256', PASSWORD.encode(), salt, 100000, dklen=32)

with tempfile.NamedTemporaryFile(suffix='.txt', delete=False, mode='w', encoding='utf-8') as f:
    f.write(inner_html)
    plain_path = f.name
with tempfile.NamedTemporaryFile(suffix='.enc', delete=False) as f:
    enc_path = f.name

r = subprocess.run(
    ['openssl','enc','-aes-256-cbc','-nosalt',
     '-K', key.hex(), '-iv', iv.hex(),
     '-in', plain_path, '-out', enc_path],
    capture_output=True
)
if r.returncode != 0:
    sys.stderr.write("openssl failed: " + r.stderr.decode() + "\n")
    sys.exit(1)

with open(enc_path,'rb') as f:
    ciphertext = f.read()
os.unlink(plain_path)
os.unlink(enc_path)

payload_b64 = base64.b64encode(salt + iv + ciphertext).decode()

# ── Build self-decrypting HTML wrapper ─────────────────────────────────────────
js_unlock = (
    "async function unlock(){"
    "const pw=document.getElementById('pw').value;if(!pw)return;"
    "const btn=document.querySelector('button');btn.textContent='Unlocking...';"
    "try{"
    "const raw=Uint8Array.from(atob(PAYLOAD),c=>c.charCodeAt(0));"
    "const salt=raw.slice(0,32),iv=raw.slice(32,48),ct=raw.slice(48);"
    "const km=await crypto.subtle.importKey('raw',new TextEncoder().encode(pw),'PBKDF2',false,['deriveKey']);"
    "const k=await crypto.subtle.deriveKey({name:'PBKDF2',salt,iterations:100000,hash:'SHA-256'},km,{name:'AES-CBC',length:256},false,['decrypt']);"
    "const dec=await crypto.subtle.decrypt({name:'AES-CBC',iv},k,ct);"
    "const html=new TextDecoder().decode(dec);"
    "document.open();document.write(html);document.close();"
    "}catch(e){"
    "document.getElementById('err').style.display='block';"
    "document.getElementById('pw').value='';"
    "document.getElementById('pw').focus();"
    "btn.textContent='Unlock \u2192';}}"
)

wrapper = (
    "<!DOCTYPE html><html lang='en'><head>"
    "<meta charset='UTF-8'>"
    "<meta name='viewport' content='width=device-width, initial-scale=1.0'>"
    "<title>Daily Digest - " + date_str + "</title>"
    "<style>"
    "*{box-sizing:border-box;margin:0;padding:0;}"
    "body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;"
    "background:linear-gradient(135deg,#1a1a2e 0%,#16213e 100%);"
    "min-height:100vh;display:flex;align-items:center;justify-content:center;}"
    ".box{background:white;border-radius:20px;padding:40px 32px;"
    "width:100%;max-width:360px;text-align:center;"
    "box-shadow:0 20px 60px rgba(0,0,0,0.4);}"
    ".icon{font-size:3rem;margin-bottom:12px;}"
    ".badge{display:inline-block;background:#f0f4ff;color:#3b4fd8;"
    "padding:4px 12px;border-radius:20px;font-size:0.75rem;"
    "font-weight:600;margin-bottom:16px;}"
    "h1{font-size:1.3rem;font-weight:700;color:#1a1a2e;margin-bottom:6px;}"
    ".sub{font-size:0.85rem;color:#888;margin-bottom:28px;}"
    "input{width:100%;padding:14px 16px;border:2px solid #e5e7eb;"
    "border-radius:10px;font-size:1rem;outline:none;margin-bottom:12px;"
    "transition:border-color 0.2s;}"
    "input:focus{border-color:#1a1a2e;}"
    "button{width:100%;padding:14px;background:#1a1a2e;color:white;"
    "border:none;border-radius:10px;font-size:1rem;font-weight:600;cursor:pointer;}"
    "button:hover{background:#16213e;}"
    ".err{color:#dc2626;font-size:0.85rem;margin-top:10px;display:none;}"
    "</style></head><body>"
    "<div class='box'>"
    "<div class='icon'>&#9728;&#65039;</div>"
    "<div class='badge'>" + date_str + "</div>"
    "<h1>Daily Digest</h1>"
    "<p class='sub'>Enter your password to read today's digest</p>"
    "<input type='password' id='pw' placeholder='Password' autofocus "
    "onkeydown=\"if(event.key==='Enter')unlock()\">"
    "<button onclick='unlock()'>Unlock &#x2192;</button>"
    "<div class='err' id='err'>Wrong password &mdash; try again</div>"
    "</div>"
    "<script>const PAYLOAD='" + payload_b64 + "';" + js_unlock + "</script>"
    "</body></html>"
)

# ── Output JSON for browser-based GitHub push ──────────────────────────────────
output = {
    "api_url": "https://api.github.com/repos/" + GITHUB_USER + "/" + GITHUB_REPO + "/contents/index.html",
    "token": GITHUB_TOKEN,
    "commit_message": "digest: " + date_str,
    "content_b64": base64.b64encode(wrapper.encode()).decode(),
    "site_url": "https://" + GITHUB_USER + ".github.io/" + GITHUB_REPO + "/"
}

print(json.dumps(output))
