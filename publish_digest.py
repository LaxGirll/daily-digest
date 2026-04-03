#!/usr/bin/env python3
"""
publish_digest.py
Encrypts the daily digest with AES-256-CBC and outputs a JSON payload
ready for the GitHub API (to be pushed via browser fetch).

Usage: python3 publish_digest.py "digest text"
       cat digest.txt | python3 publish_digest.py
Output: JSON to stdout with {api_url, token, commit_message, content_b64, site_url}
"""

import sys, os, json, base64, hashlib, subprocess, tempfile, html as html_mod
from pathlib import Path
from datetime import datetime

def _e(s):
    """HTML-escape a string."""
    return html_mod.escape(str(s))

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
    raw_input = " ".join(sys.argv[1:])
else:
    raw_input = sys.stdin.read()

# Detect structured JSON input from daily_digest.py
try:
    _data          = json.loads(raw_input)
    digest_text    = _data.get("digest_text", "")
    _gmail_creds   = _data.get("gmail_creds", {})
    _needs_attn    = _data.get("needs_attention", [])
    _promotions    = _data.get("promotions", [])
    _notifications = _data.get("notifications", [])
    _gmail_labels  = _data.get("gmail_labels", {})
    _total_emails  = _data.get("total_emails", 0)
    _books         = _data.get("books", [])
    _food          = _data.get("food", [])
    _kids          = _data.get("kids", [])
    _regular       = _data.get("regular", [])
    _cleanup       = _data.get("cleanup", [])
except (json.JSONDecodeError, TypeError):
    digest_text    = raw_input
    _gmail_creds   = {}
    _needs_attn    = []
    _promotions    = []
    _notifications = []
    _gmail_labels  = {}
    _total_emails  = 0
    _books         = []
    _food          = []
    _kids          = []
    _regular       = []
    _cleanup       = []

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
    # Interactive action items
    ".aitem{display:flex;align-items:flex-start;gap:10px;padding:10px 0;"
    "border-bottom:1px solid #f0f0f0;transition:opacity .3s;}"
    ".aitem:last-child{border-bottom:none;}"
    ".aitem-body{flex:1;min-width:0;}"
    ".atext{font-size:.88rem;line-height:1.5;color:#1a1a2e;}"
    ".ameta{font-size:.75rem;color:#aaa;margin-top:2px;}"
    ".abtns{display:flex;gap:6px;margin-top:6px;flex-wrap:wrap;}"
    ".btn-trash{background:#fff1f0;color:#cf1322;border:1px solid #ffd6d6;"
    "border-radius:6px;padding:3px 10px;font-size:.75rem;cursor:pointer;}"
    ".btn-trash:hover{background:#ffd6d6;}"
    ".btn-trash:disabled{opacity:.5;cursor:default;}"
    ".lbl-select{font-size:.75rem;border:1px solid #e5e7eb;border-radius:6px;"
    "padding:3px 6px;color:#666;cursor:pointer;max-width:160px;}"
    ".moved-tag{font-size:.75rem;color:#4f46e5;margin-left:4px;}"
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
    # Counts card
    ".counts-grid{display:grid;grid-template-columns:1fr auto;gap:4px 16px;font-size:.85rem;}"
    ".counts-grid span:nth-child(even){font-weight:600;text-align:right;}"
    # Todo buttons and clear
    ".btn-todo{background:#f0fdf4;color:#16a34a;border:1px solid #bbf7d0;"
    "border-radius:6px;padding:3px 10px;font-size:.75rem;cursor:pointer;white-space:nowrap;}"
    ".btn-todo:hover{background:#dcfce7;}"
    ".btn-todo:disabled{opacity:.5;cursor:default;}"
    ".btn-clear{background:none;border:none;color:#aaa;font-size:.75rem;cursor:pointer;"
    "padding:2px 6px;border-radius:4px;}"
    ".btn-clear:hover{background:#f5f5f5;color:#666;}"
    ".todo-empty{color:#aaa;font-size:.85rem;padding:8px 0;}"
    ".age-badge{display:inline-block;background:#f0f0f0;color:#888;font-size:.7rem;"
    "border-radius:10px;padding:1px 8px;margin-right:6px;white-space:nowrap;}"
)

# Raw string so backslashes pass through to JavaScript unchanged
JS_TMPL = r"""var D=PAYLOAD_JSON;
function esc(s){return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}
function rIntro(t){return '';}
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
renderTodos();
document.getElementById('digest').innerHTML=renderDigest(D);"""

js = JS_TMPL.replace('PAYLOAD_JSON', json.dumps(digest_text))

# ── Gmail API JS (only when credentials are present) ───────────────────────────
GMAIL_JS = ""
if _gmail_creds:
    GMAIL_JS = (
        "var GC=" + json.dumps(_gmail_creds) + ";"
        "var _gt=null,_ge=0;"
        "async function getGT(){"
        "if(_gt&&Date.now()<_ge)return _gt;"
        "const r=await fetch('https://oauth2.googleapis.com/token',{"
        "method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},"
        "body:new URLSearchParams({client_id:GC.ci,client_secret:GC.cs,"
        "refresh_token:GC.rt,grant_type:'refresh_token'})});"
        "const d=await r.json();_gt=d.access_token;_ge=Date.now()+(d.expires_in-60)*1000;return _gt;}"
        "async function trashEmail(id,btn){"
        "const el=btn.closest('.aitem');btn.textContent='...';btn.disabled=true;"
        "try{const t=await getGT();"
        "await fetch('https://gmail.googleapis.com/gmail/v1/users/me/messages/'+id+'/trash',"
        "{method:'POST',headers:{Authorization:'Bearer '+t}});"
        "el.style.opacity='.3';btn.textContent='\u2713 Deleted';"
        "}catch(e){btn.textContent='Error';btn.disabled=false;}}"
        "async function moveEmail(id,sel){"
        "const lid=sel.value;if(!lid)return;"
        "const el=sel.closest('.aitem');sel.disabled=true;"
        "const lname=sel.options[sel.selectedIndex].text;"
        "try{const t=await getGT();"
        "await fetch('https://gmail.googleapis.com/gmail/v1/users/me/messages/'+id+'/modify',"
        "{method:'POST',headers:{Authorization:'Bearer '+t,'Content-Type':'application/json'},"
        "body:JSON.stringify({addLabelIds:[lid],removeLabelIds:['INBOX']})});"
        "el.style.opacity='.5';"
        "const sp=document.createElement('span');sp.className='moved-tag';"
        "sp.textContent='\u2192 '+lname;sel.parentNode.insertBefore(sp,sel.nextSibling);"
        "sel.style.display='none';"
        "}catch(e){sel.disabled=false;alert('Move failed: '+e.message);}}"
        "var _TK='dd_todos';"
        "function _lt(){try{return JSON.parse(localStorage.getItem(_TK))||[];}catch{return[];}}"
        "function _st(t){localStorage.setItem(_TK,JSON.stringify(t));}"
        "function addToTodo(txt,btn){"
        "var t=_lt();"
        "t.unshift({id:Date.now(),text:txt,done:false,added:new Date().toLocaleDateString()});"
        "_st(t);renderTodos();"
        "btn.textContent='\u2713 Added';btn.disabled=true;}"
        "function toggleTodo(id){"
        "var t=_lt(),item=t.find(function(x){return x.id===id;});"
        "if(item)item.done=!item.done;_st(t);renderTodos();}"
        "function deleteTodo(id){_st(_lt().filter(function(x){return x.id!==id;}));renderTodos();}"
        "function clearDone(){_st(_lt().filter(function(x){return !x.done;}));renderTodos();}"
        "function renderTodos(){"
        "var el=document.getElementById('todo-list');if(!el)return;"
        "var t=_lt();"
        "if(!t.length){el.innerHTML='<p class=\"todo-empty\">Nothing here yet \u2014 hit \u2795 To\u00a0Do on any item.</p>';return;}"
        "var done=t.filter(function(x){return x.done;}).length;"
        "var cb=done?'<button class=\"btn-clear\" onclick=\"clearDone()\">Clear '+done+' done</button>':'';"
        "el.innerHTML=t.map(function(x){"
        "return '<div class=\"aitem'+(x.done?' done':'')+'\" id=\"td-'+x.id+'\">' "
        "+'<input type=\"checkbox\"'+(x.done?' checked':'')+' onchange=\"toggleTodo('+x.id+')\">' "
        "+'<div class=\"aitem-body\"><div class=\"atext\">'+x.text+'</div>'"
        "+'<div class=\"ameta\">Added '+x.added+'</div></div>'"
        "+'<div class=\"abtns\"><button class=\"btn-trash\" onclick=\"deleteTodo('+x.id+')\">&#128465; Delete</button></div>'"
        "+'</div>';"
        "}).join('')+cb;}"
    )

# ── Python helpers that build interactive HTML sections ─────────────────────────

def esc(s):
    """HTML-escape a string for use in JS string literals (single-quoted)."""
    return html_mod.escape(str(s), quote=False).replace("'", "&#39;")

def build_counts_html(total, n_attn, n_notifs, n_ai, n_promos):
    other = max(0, total - n_attn - n_notifs - n_ai - n_promos)
    rows = [
        ("\U0001f4e7 Total emails scanned", total),
        ("\u26a1 Needs attention", n_attn),
        ("\U0001f514 Notifications", n_notifs),
        ("\U0001f4f0 AI newsletters", n_ai),
        ("\U0001f6d2 Promotions", n_promos),
    ]
    if other:
        rows.append(("\U0001f4e9 Other", other))
    cells = "".join(
        f"<span>{label}</span><span>{count}</span>"
        for label, count in rows
    )
    return f"<div class='card'><div class='sec-title'>\U0001f4ca Today's Inbox</div><div class='counts-grid'>{cells}</div></div>"

def _label_select(email_id, labels):
    if not labels:
        return ""
    opts = "<option value=''>Move to label\u2026</option>"
    for name, lid in sorted(labels.items()):
        opts += f"<option value='{_e(lid)}'>{_e(name)}</option>"
    return (f"<select class='lbl-select' onchange=\"moveEmail('{email_id}',this)\">"
            + opts + "</select>")

def _build_action_items_html(items, labels, show_move=False):
    if not items:
        return ""
    rows = ""
    for item in items:
        eid      = item.get("id", "")
        text     = _e(item.get("text", ""))
        meta     = _e(item.get("from", ""))
        move_btn = _label_select(eid, labels) if show_move else ""
        todo_btn = f"<button class='btn-todo' onclick=\"addToTodo('{esc(item['text'])}',this)\">\u2795 To&nbsp;Do</button>"
        rows += (
            f"<div class='aitem' id='ai-{eid}'>"
            f"<div class='aitem-body'>"
            f"<div class='atext'>{text}</div>"
            f"<div class='ameta'>{meta}</div>"
            f"<div class='abtns'>"
            f"<button class='btn-trash' onclick=\"trashEmail('{eid}',this)\">\U0001f5d1 Delete</button>"
            f"{move_btn}"
            f"{todo_btn}"
            f"</div></div></div>"
        )
    return rows

needs_attn_html = ""
if _needs_attn:
    rows = _build_action_items_html(_needs_attn, _gmail_labels, show_move=True)
    needs_attn_html = (
        "<div class='card'>"
        "<div class='sec-title'>&#9889; Needs attention today</div>"
        + rows + "</div>"
    )

promos_html = ""
if _promotions:
    rows = _build_action_items_html(_promotions, _gmail_labels, show_move=True)
    promos_html = (
        "<div class='card'>"
        "<div class='sec-title'>&#127991; Promotions &amp; Deals</div>"
        + rows + "</div>"
    )

notifs_html = ""
if _notifications:
    rows = _build_action_items_html(_notifications, _gmail_labels, show_move=True)
    notifs_html = (
        "<div class='card'>"
        "<div class='sec-title'>&#128276; Notifications</div>"
        + rows + "</div>"
    )

def _build_books_html(items, labels):
    if not items:
        return ''
    rows = _build_action_items_html(items, labels, show_move=True)
    return (
        "<div class='card'>"
        "<div class='sec-title'>&#128218; Books &amp; Reading</div>"
        + rows + "</div>"
    )

def _build_food_html(items, labels):
    if not items:
        return ''
    rows = _build_action_items_html(items, labels, show_move=True)
    return (
        "<div class='card'>"
        "<div class='sec-title'>&#127859; Food &amp; Recipes</div>"
        + rows + "</div>"
    )

def _build_kids_html(items, labels):
    if not items:
        return ''
    rows = _build_action_items_html(items, labels, show_move=True)
    return (
        "<div class='card'>"
        "<div class='sec-title'>&#128106; Kids &amp; Family</div>"
        + rows + "</div>"
    )

def _build_regular_html(items, labels):
    if not items:
        return ''
    rows = _build_action_items_html(items, labels, show_move=True)
    return (
        "<div class='card'>"
        "<div class='sec-title'>&#128233; Regular Emails</div>"
        + rows + "</div>"
    )

def _build_cleanup_html(items, labels):
    if not items:
        return ''
    rows = ""
    for item in items:
        eid      = item.get("id", "")
        age      = _e(item.get("age", ""))
        sender   = _e(item.get("from", ""))
        subject  = _e(item.get("subject", "(no subject)"))
        move_btn = _label_select(eid, labels)
        todo_txt = esc(item.get("from", "") + " — " + item.get("subject", ""))
        todo_btn = f"<button class='btn-todo' onclick=\"addToTodo('{todo_txt}',this)\">\u2795 To&nbsp;Do</button>"
        rows += (
            f"<div class='aitem' id='cl-{eid}'>"
            f"<div class='aitem-body'>"
            f"<div class='atext'><span class='age-badge'>{age}</span>"
            f"<strong>{sender}</strong> \u2014 {subject}</div>"
            f"<div class='abtns'>"
            f"<button class='btn-trash' onclick=\"trashEmail('{eid}',this)\">\U0001f5d1 Delete</button>"
            f"{move_btn}"
            f"{todo_btn}"
            f"</div></div></div>"
        )
    return (
        "<div class='card'>"
        "<div class='sec-title'>&#129529; Inbox Cleanup \u2014 25 Older Emails</div>"
        "<div style='font-size:.78rem;color:#aaa;margin-bottom:8px;'>Chipping away at your inbox \u2014 25 at a time.</div>"
        + rows + "</div>"
    )

counts_html = build_counts_html(
    _total_emails,
    len(_needs_attn),
    len(_notifications),
    0,
    len(_promotions),
)

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
    + counts_html
    + "<div class='card'><div class='sec-title'>&#128203; To Do</div><div id='todo-list'></div></div>"
    + needs_attn_html
    + notifs_html
    + _build_books_html(_books, _gmail_labels)
    + _build_food_html(_food, _gmail_labels)
    + _build_kids_html(_kids, _gmail_labels)
    + promos_html
    + _build_regular_html(_regular, _gmail_labels)
    + _build_cleanup_html(_cleanup, _gmail_labels)
    + "<div id='digest'></div>"
    + "</div>"
    "<script>" + GMAIL_JS + js + "</script>"
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
