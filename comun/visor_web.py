"""
Visor web de cada contenedor de simulación (puerto interno 8080).

    /             página con la vista 3D y el panel de estado
    /frame.jpg    último cuadro renderizado por PyBullet
    /estado.json  estado de la simulación (lo arma cada contenedor)
    /health       salud del contenedor (lo consulta el monitor de la VLAN 3)

Para no gastar CPU, el contenedor solo renderiza mientras alguien está
mirando la página (demanda en los últimos 3 s).
"""
import io
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from PIL import Image

PAGINA = """<!doctype html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>__TITULO__</title>
<style>
:root{--bg:#0e1116;--panel:#161b23;--line:#252c38;--txt:#e6eaf0;--sub:#8a94a6;--acc:__COLOR__}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--txt);font:15px/1.45 system-ui,Segoe UI,Roboto,sans-serif}
header{display:flex;gap:12px;align-items:center;flex-wrap:wrap;padding:14px 18px;border-bottom:1px solid var(--line)}
.tag{font:600 12px/1 ui-monospace,Consolas,monospace;padding:5px 9px;border-radius:6px;background:var(--acc);color:#0e1116}
h1{font-size:18px;margin:0;font-weight:650}.sub{color:var(--sub);font-size:13px}
.pill{margin-left:auto;font-size:12px;padding:4px 10px;border-radius:99px;background:#2a3140;color:var(--sub)}
.pill.ok{background:#12391f;color:#58d68d}
main{display:grid;grid-template-columns:minmax(0,1fr) 320px;gap:14px;padding:14px}
@media(max-width:900px){main{grid-template-columns:1fr}}
.vista{background:#000;border-radius:10px;overflow:hidden;aspect-ratio:4/3}.vista img{width:100%;height:100%;object-fit:cover;display:block}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:12px 14px;margin-bottom:12px}
.panel h2{font-size:11px;letter-spacing:.07em;text-transform:uppercase;color:var(--sub);margin:0 0 8px}
.kv{display:flex;justify-content:space-between;gap:10px;padding:5px 0;border-top:1px solid var(--line);font-size:14px}
.kv:first-of-type{border-top:0}.kv span:last-child{font-variant-numeric:tabular-nums;text-align:right}
.big{font-size:28px;font-weight:700;font-variant-numeric:tabular-nums}
.ctl{display:grid;grid-template-columns:auto 1fr auto;gap:6px 10px;align-items:center;font-size:13px}
.bar{height:8px;background:#232a36;border-radius:4px;position:relative;overflow:hidden}
.bar i{position:absolute;top:0;bottom:0;background:var(--acc)}
.btn{display:inline-block;width:26px;height:26px;border-radius:6px;background:#232a36;text-align:center;line-height:26px;font-size:12px;margin-right:4px}
.btn.on{background:var(--acc);color:#0e1116;font-weight:700}
</style></head><body>
<header><span class="tag">__VLAN__</span><div><h1>__TITULO__</h1><div class="sub">__SUB__</div></div>
<span class="pill" id="esp">ESP32 desconectada</span></header>
<main><div class="vista"><img id="f" alt="Simulación"></div><div>
<div class="panel" id="hud"></div>
<div class="panel esp"><h2>Mandos de la ESP32 maestra</h2><div class="ctl" id="ctl"></div></div>
<div class="panel esp"><h2>Enlace ESP32 &rarr; contenedor</h2><div id="enl"></div></div>
</div></main>
<script>
const NOMBRES=__NOMBRES__;
function frame(){const i=new Image();i.onload=()=>{document.getElementById('f').src=i.src;setTimeout(frame,60)};
i.onerror=()=>setTimeout(frame,600);i.src='/frame.jpg?t='+Date.now()}frame();
const kv=(k,v)=>`<div class="kv"><span>${k}</span><span>${v}</span></div>`;
function barra(v,centro){v=Math.max(-1000,Math.min(1000,v));if(centro){const a=50+Math.min(0,v)/20,w=Math.abs(v)/20;return `<div class="bar"><i style="left:${a}%;width:${w}%"></i></div>`}
return `<div class="bar"><i style="left:0;width:${Math.max(0,v)/10}%"></i></div>`}
async function tick(){try{const s=await (await fetch('/estado.json')).json();
const p=document.getElementById('esp');
if(!s.enlace){p.textContent=s.pill||'';p.className='pill ok';document.querySelectorAll('.esp').forEach(x=>x.style.display='none');}
const e=s.enlace||{};if(s.enlace){p.textContent=e.conectada?`ESP32 conectada · ${e.pps} paq/s`:'ESP32 desconectada';p.className='pill'+(e.conectada?' ok':'');}
document.getElementById('hud').innerHTML=(s.titulo_hud?`<h2>${s.titulo_hud}</h2>`:'')+(s.grande?`<div class="big">${s.grande}</div>`:'')+Object.entries(s.hud||{}).map(([k,v])=>kv(k,v)).join('');
const c=s.ctrl||{};document.getElementById('ctl').innerHTML=
`<span>POT1</span>${barra(c.a1||0,NOMBRES.a1c)}<span>${NOMBRES.a1}</span><span>POT2</span>${barra(c.a2||0,NOMBRES.a2c)}<span>${NOMBRES.a2}</span>`+
`<span>Botones</span><span>${['b1','b2','b3'].map(b=>`<span class="btn ${c[b]?'on':''}" title="${NOMBRES[b]}">${b.toUpperCase()}</span>`).join('')}</span><span style="color:var(--sub)">${NOMBRES.b1} · ${NOMBRES.b2} · ${NOMBRES.b3}</span>`;
document.getElementById('enl').innerHTML=kv('RTT (medido por la ESP32)',e.rtt_ms!=null?e.rtt_ms+' ms':'—')+kv('Jitter de llegada (RFC 3550)',(e.jitter_ms??0)+' ms')+kv('Paquetes perdidos',`${e.perdidos??0} en total · ${e.perdida_pct??0} % (últimos 10 s)`)+kv('Origen',e.origen||'—');
}catch(err){}setTimeout(tick,400)}tick();
</script></body></html>"""


class VisorWeb:
    def __init__(self, titulo, sub, vlan, color, nombres_mandos, estado_fn, salud_fn, puerto=None):
        puerto = puerto or int(os.environ.get("PUERTO_WEB", "8080"))
        self.jpeg = b""
        self.lock = threading.Lock()
        self.demanda = 0.0
        pagina = (PAGINA.replace("__TITULO__", titulo).replace("__SUB__", sub)
                  .replace("__VLAN__", vlan).replace("__COLOR__", color)
                  .replace("__NOMBRES__", json.dumps(nombres_mandos))).encode()
        visor = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _env(self, cuerpo, tipo, cod=200):
                self.send_response(cod)
                self.send_header("Content-Type", tipo)
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(cuerpo)))
                self.end_headers()
                self.wfile.write(cuerpo)

            def do_GET(self):
                ruta = self.path.split("?")[0]
                try:
                    if ruta == "/frame.jpg":
                        visor.demanda = time.time()
                        with visor.lock:
                            img = visor.jpeg
                        if img:
                            self._env(img, "image/jpeg")
                        else:
                            self._env(b"", "image/jpeg", 503)
                    elif ruta == "/estado.json":
                        self._env(json.dumps(estado_fn()).encode(), "application/json")
                    elif ruta == "/health":
                        self._env(json.dumps(salud_fn()).encode(), "application/json")
                    else:
                        self._env(pagina, "text/html; charset=utf-8")
                except (BrokenPipeError, ConnectionResetError):
                    pass

        self.srv = ThreadingHTTPServer(("0.0.0.0", puerto), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def hay_demanda(self):
        return time.time() - self.demanda < 3.0

    def guardar_jpeg(self, jpeg):
        with self.lock:
            self.jpeg = jpeg

    def guardar_rgba(self, ancho, alto, rgba):
        img = Image.frombytes("RGBA", (ancho, alto), bytes(bytearray(rgba))).convert("RGB")
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=80)
        with self.lock:
            self.jpeg = buf.getvalue()
