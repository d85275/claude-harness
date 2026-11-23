"""Inline CSS and JavaScript for the board page. No network, no external files.

The script never builds HTML from text: it only toggles `hidden`, classes and `textContent`.
All ticket text is escaped (and Markdown rendered) at build time in render.py.
"""

CSS = """
:root{--bg:#f6f7f9;--panel:#fff;--text:#1d2330;--muted:#5d6678;--line:#d9dde5;--accent:#2457c5;
--warn:#b4232a;--warn-bg:#fde8e8;--chip:#eceff4;--shadow:0 1px 2px rgba(0,0,0,.08)}
:root[data-theme=dark]{--bg:#14171d;--panel:#1d222b;--text:#e6e9ef;--muted:#9aa3b5;--line:#313845;
--accent:#7da6ff;--warn:#ff9a9f;--warn-bg:#41222a;--chip:#2a303b;--shadow:none}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#14171d;--panel:#1d222b;--text:#e6e9ef;
--muted:#9aa3b5;--line:#313845;--accent:#7da6ff;--warn:#ff9a9f;--warn-bg:#41222a;--chip:#2a303b;--shadow:none}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font:15px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif}
header{display:flex;flex-wrap:wrap;gap:8px 16px;align-items:baseline;justify-content:space-between;padding:12px 16px}
h1{font-size:20px;margin:0}
.built{color:var(--muted);font-size:13px}
button,select,input{font:inherit;color:inherit;background:var(--panel);border:1px solid var(--line);border-radius:6px;padding:5px 9px}
button{cursor:pointer}
.tools{display:flex;flex-wrap:wrap;gap:8px;padding:0 16px 12px;align-items:center}
.tools input[type=search]{flex:1 1 200px;min-width:0}
.tools select{flex:1 1 110px;min-width:0}
header>div{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
.tools label{display:flex;gap:6px;align-items:center;color:var(--muted)}
.tools input[type=checkbox]{padding:0}
.board{display:grid;grid-template-columns:repeat(6,minmax(220px,1fr));gap:12px;padding:0 16px 24px;overflow-x:auto}
.col{background:var(--chip);border-radius:8px;padding:8px;min-width:0}
.col h2{font-size:13px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted);margin:2px 4px 8px;display:flex;justify-content:space-between}
.card{display:block;width:100%;text-align:left;background:var(--panel);border:1px solid var(--line);border-radius:8px;
padding:9px 10px;margin-bottom:8px;box-shadow:var(--shadow)}
.card:hover,.card:focus-visible{border-color:var(--accent);outline:none}
.card[hidden]{display:none}
.top{display:flex;gap:7px;align-items:center;color:var(--muted);font-size:13px}
.id{font-weight:600;color:var(--text)}
.title{margin:4px 0;overflow-wrap:anywhere}
.icon{display:inline-flex;width:20px;height:20px;border-radius:50%;align-items:center;justify-content:center;
font-size:11px;font-weight:700;color:#fff;flex:none}
.tags{display:flex;flex-wrap:wrap;gap:4px;margin-top:4px}
.tag{font-size:12px;padding:1px 7px;border-radius:10px;background:var(--chip);color:var(--muted);overflow-wrap:anywhere}
.tag.mismatch{background:var(--warn-bg);color:var(--warn);font-weight:600}
.tag.high-risk{background:var(--warn-bg);color:var(--warn)}
.extra{font-size:12px;color:var(--muted);overflow-wrap:anywhere}
.empty{color:var(--muted);font-size:13px;padding:4px}
#issues{position:fixed;inset:0;background:rgba(0,0,0,.45);display:flex;justify-content:flex-end;z-index:5}
#issues[hidden],.issue[hidden]{display:none}
.issue{background:var(--bg);width:min(760px,100%);height:100%;overflow:auto;padding:16px;border-left:1px solid var(--line)}
.issue .bar{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:8px}
.issue h2.name{font-size:20px;margin:6px 0 10px;overflow-wrap:anywhere}
dl.details{display:grid;grid-template-columns:max-content 1fr;gap:4px 14px;background:var(--panel);border:1px solid var(--line);
border-radius:8px;padding:10px 12px;margin:0 0 14px}
dl.details dt{color:var(--muted)}dl.details dd{margin:0;overflow-wrap:anywhere}
.warnbox{background:var(--warn-bg);color:var(--warn);border-radius:8px;padding:8px 12px;margin-bottom:14px}
.md{overflow-wrap:anywhere}
.md pre{background:var(--chip);padding:10px;border-radius:6px;overflow:auto}
.md code{background:var(--chip);padding:1px 4px;border-radius:4px;font-size:90%}
.md pre code{background:none;padding:0}
.md table{border-collapse:collapse;display:block;overflow-x:auto}
.md th,.md td{border:1px solid var(--line);padding:4px 8px;text-align:left}
.md blockquote{margin:8px 0;padding-left:12px;border-left:3px solid var(--line);color:var(--muted)}
.md a{color:var(--accent)}
@media (max-width:700px){.board{grid-template-columns:minmax(0,1fr);overflow-x:visible}.issue{padding:12px}}
"""

JS = """
(function(){
var cards=[].slice.call(document.querySelectorAll('.card'));
var issues=document.getElementById('issues');
var panels={};
[].forEach.call(issues.querySelectorAll('.issue'),function(p){panels[p.getAttribute('data-id')]=p;});
var q=document.getElementById('q'),fk=document.getElementById('f-kind'),fp=document.getElementById('f-platform'),
fr=document.getElementById('f-review'),fm=document.getElementById('f-mismatch');
function applyFilters(){
  var text=q.value.trim().toLowerCase();
  cards.forEach(function(c){
    var ok=(!text||c.getAttribute('data-search').indexOf(text)>=0)
      &&(!fk.value||c.getAttribute('data-kind')===fk.value)
      &&(!fp.value||c.getAttribute('data-platform')===fp.value)
      &&(!fr.value||c.getAttribute('data-review')===fr.value)
      &&(!fm.checked||c.getAttribute('data-mismatch')==='1');
    c.hidden=!ok;
  });
  [].forEach.call(document.querySelectorAll('.col'),function(col){
    var n=col.querySelectorAll('.card:not([hidden])').length;
    col.querySelector('.count').textContent=String(n);
    col.querySelector('.empty').hidden=n>0;
  });
}
[q,fk,fp,fr,fm].forEach(function(e){e.addEventListener('input',applyFilters);e.addEventListener('change',applyFilters);});
var opener=null;
function show(id){
  var any=false,was=!issues.hidden;
  Object.keys(panels).forEach(function(k){var on=k===id;panels[k].hidden=!on;if(on)any=true;});
  issues.hidden=!any;
  if(any){
    if(!was){opener=document.activeElement&&document.activeElement.classList.contains('card')?document.activeElement:null;}
    panels[id].scrollTop=0;
    issues.setAttribute('aria-labelledby',panels[id].querySelector('h2').id);
    panels[id].querySelector('[data-close]').focus();
  }else{
    issues.removeAttribute('aria-labelledby');
    if(was&&opener){opener.focus();}
    opener=null;
  }
}
function fromHash(){
  var id=(location.hash||'').replace(/^#/,'');
  try{id=decodeURIComponent(id);}catch(x){}
  show(Object.prototype.hasOwnProperty.call(panels,id)?id:'');
}
cards.forEach(function(c){c.addEventListener('click',function(){location.hash='#'+c.getAttribute('data-id');});});
issues.addEventListener('click',function(e){
  var t=e.target;
  if(t===issues||t.getAttribute('data-close')!==null){history.pushState(null,'',location.pathname+location.search);show('');}
});
document.addEventListener('keydown',function(e){if(e.key==='Escape'&&!issues.hidden){history.pushState(null,'',location.pathname+location.search);show('');}});
window.addEventListener('hashchange',fromHash);
[].forEach.call(document.querySelectorAll('[data-copy]'),function(b){
  b.addEventListener('click',function(){
    var url=location.href.split('#')[0]+'#'+b.getAttribute('data-copy'),old=b.textContent;
    function done(){b.textContent='Copied';setTimeout(function(){b.textContent=old;},1200);}
    if(navigator.clipboard&&navigator.clipboard.writeText){navigator.clipboard.writeText(url).then(done,fallback);}else{fallback();}
    function fallback(){
      var t=document.createElement('textarea');t.value=url;document.body.appendChild(t);t.select();
      var ok=false;try{ok=document.execCommand('copy');}catch(x){}
      if(ok){done();}else{window.prompt('Copy this link',url);}
      document.body.removeChild(t);
    }
  });
});
var root=document.documentElement,tb=document.getElementById('theme');
function dark(){var t=root.getAttribute('data-theme');return t?t==='dark':window.matchMedia('(prefers-color-scheme: dark)').matches;}
try{var saved=localStorage.getItem('board-theme');if(saved==='light'||saved==='dark'){root.setAttribute('data-theme',saved);}}catch(x){}
tb.addEventListener('click',function(){
  var next=dark()?'light':'dark';root.setAttribute('data-theme',next);
  try{localStorage.setItem('board-theme',next);}catch(x){}
});
applyFilters();
fromHash();
})();
"""
