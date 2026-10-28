import os
import zipfile
import shutil
from flask import Flask, request, jsonify, send_from_directory
from hub import BotHub

app = Flask(__name__)

HUB_DIR = os.path.dirname(os.path.abspath(__file__))
EVIDENCIAS_DIR = os.path.join(HUB_DIR, "data", "evidencias")
DESCARGAS_FOMAG_DIR = os.path.join(HUB_DIR, "data", "descargas_fomag")
os.makedirs(EVIDENCIAS_DIR, exist_ok=True)
os.makedirs(DESCARGAS_FOMAG_DIR, exist_ok=True)

@app.route('/health', methods=['GET'])
def health():
    return jsonify({
        "status": "UP",
        "service": "RPA Bots Hub API",
        "eps_disponibles": [e["codigo"] for e in BotHub.eps_disponibles()]
    })

@app.route('/api/bots', methods=['GET'])
def get_bots():
    return jsonify({
        "success": True,
        "bots": BotHub.eps_disponibles()
    })

@app.route('/api/validar', methods=['POST'])
def api_validar():
    """
    Endpoint principal para validar derechos/autorizaciones desde cualquier sistema externo.
    Body JSON requerido:
    {
        "eps": "colsanitas",
        "doc_tipo": "CC",
        "doc_num": "17337293",
        "headless": true
    }
    """
    try:
        data = request.get_json() or {}
        eps = data.get("eps", "colsanitas")
        doc_tipo = data.get("doc_tipo", "CC")
        doc_num = data.get("doc_num")
        headless = data.get("headless", True)
        
        if not doc_num:
            return jsonify({
                "success": False,
                "error": "El parámetro 'doc_num' es obligatorio."
            }), 400
            
        resultado = BotHub.validar(
            eps=eps,
            doc_tipo=doc_tipo,
            doc_num=doc_num,
            headless=headless,
            evidencias_dir=EVIDENCIAS_DIR
        )
        
        return jsonify({
            "success": True,
            "data": resultado
        })
        
    except ValueError as ve:
        return jsonify({"success": False, "error": str(ve)}), 400
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/restaurar_state/<eps_codigo>', methods=['POST'])
def restaurar_state(eps_codigo):
    token = request.headers.get("X-Session-Token", "")
    if token != os.getenv("SESSION_RESTORE_TOKEN", "neuro2026"):
        return jsonify({"success": False, "error": "Token inválido"}), 403
    if eps_codigo != "colsanitas":
        return jsonify({"success": False, "error": "EPS no soportada aún"}), 400
    if "file" not in request.files:
        return jsonify({"success": False, "error": "No se recibió archivo"}), 400
    f = request.files["file"]
    state_path = os.path.join(HUB_DIR, "data", f"{eps_codigo}_state.json")
    f.save(state_path)
    return jsonify({"success": True, "mensaje": f"Storage state guardado en {state_path}"})

@app.route('/upload_sesion', methods=['GET'])
def upload_sesion_form():
    return '''<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Restaurar Sesión</title>
<style>body{font-family:sans-serif;max-width:500px;margin:60px auto;padding:20px}
h2{color:#333}input[type=file]{margin:16px 0;display:block}
button{background:#2563eb;color:#fff;border:none;padding:12px 24px;border-radius:6px;font-size:16px;cursor:pointer}
#msg{margin-top:20px;padding:12px;border-radius:6px;display:none}
.ok{background:#d1fae5;color:#065f46}.err{background:#fee2e2;color:#991b1b}</style></head>
<body><h2>Restaurar sesión Colsanitas</h2>
<p>Sube el archivo <b>colsanitas_state.json</b> (cookies portables) o el ZIP del perfil.</p>
<input type="file" id="f" accept=".zip,.json">
<button onclick="subir()">Subir sesión</button>
<div id="msg"></div>
<script>
async function subir(){
  const f=document.getElementById('f').files[0];
  if(!f){alert('Selecciona un archivo ZIP primero');return;}
  const fd=new FormData();fd.append('file',f);
  const msg=document.getElementById('msg');
  msg.style.display='block';msg.className='';msg.textContent='Subiendo...';
  try{
    const endpoint=f.name.endsWith('.json')?'/api/restaurar_state/colsanitas':'/api/restaurar_sesion/colsanitas';
    const r=await fetch(endpoint,{method:'POST',headers:{'X-Session-Token':'neuro2026'},body:fd});
    const d=await r.json();
    if(d.success){msg.className='ok';msg.textContent='✓ '+d.mensaje;}
    else{msg.className='err';msg.textContent='Error: '+d.error;}
  }catch(e){msg.className='err';msg.textContent='Error de red: '+e;}
}
</script></body></html>'''

@app.route('/api/restaurar_sesion/<eps_codigo>', methods=['POST'])
def restaurar_sesion(eps_codigo):
    token = request.headers.get("X-Session-Token", "")
    if token != os.getenv("SESSION_RESTORE_TOKEN", "neuro2026"):
        return jsonify({"success": False, "error": "Token inválido"}), 403
    allowed = {"colsanitas", "salud_total", "capital_salud", "fomag"}
    if eps_codigo not in allowed:
        return jsonify({"success": False, "error": "EPS no permitida"}), 400
    if "file" not in request.files:
        return jsonify({"success": False, "error": "No se recibió archivo"}), 400
    f = request.files["file"]
    session_dir = os.path.join(HUB_DIR, "data", f".session_{eps_codigo}")
    if os.path.exists(session_dir):
        shutil.rmtree(session_dir, ignore_errors=True)
    os.makedirs(session_dir, exist_ok=True)
    tmp_zip = os.path.join(HUB_DIR, "data", f"_tmp_session_{eps_codigo}.zip")
    f.save(tmp_zip)
    try:
        with zipfile.ZipFile(tmp_zip, "r") as z:
            z.extractall(session_dir)
        os.remove(tmp_zip)
        return jsonify({"success": True, "mensaje": f"Sesión {eps_codigo} restaurada en {session_dir}"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/evidencia/<filename>', methods=['GET'])
def serve_evidencia(filename):
    return send_from_directory(EVIDENCIAS_DIR, filename)

@app.route('/api/descarga_fomag/<doc_num>/<filename>', methods=['GET'])
def serve_descarga_fomag(doc_num, filename):
    doc_dir = os.path.join(DESCARGAS_FOMAG_DIR, doc_num)
    return send_from_directory(doc_dir, filename)

if __name__ == '__main__':
    puerto = int(os.getenv("PORT_API", "5055"))
    print("\n" + "="*60)
    print(f"  RPA HUB - SERVICIO REST API INICIADO")
    print(f"  Escuchando en: http://0.0.0.0:{puerto}")
    print(f"  Endpoint de validación: POST http://0.0.0.0:{puerto}/api/validar")
    print("="*60 + "\n")
    app.run(host='0.0.0.0', port=puerto, debug=False, threaded=True)
