import os
import re
import json
import asyncio
from flask import Flask, render_template, request, jsonify, send_from_directory
from hub import BotHub

app = Flask(__name__)

HUB_DIR = os.path.dirname(os.path.abspath(__file__))
EVIDENCIAS_DIR = os.path.join(HUB_DIR, "data", "evidencias")
DESCARGAS_FOMAG_DIR = os.path.join(HUB_DIR, "data", "descargas_fomag")
os.makedirs(EVIDENCIAS_DIR, exist_ok=True)
os.makedirs(DESCARGAS_FOMAG_DIR, exist_ok=True)

@app.route('/')
def index():
    eps_list = BotHub.eps_disponibles()
    return render_template('playground.html', eps_list=eps_list)

@app.route('/api/ejecutar_prueba', methods=['POST'])
def ejecutar_prueba():
    try:
        data = request.get_json() or {}
        eps = data.get("eps", "colsanitas")
        doc_tipo = data.get("doc_tipo", "CC")
        doc_num = data.get("doc_num", "")
        visual = data.get("visual", True)
        
        if not doc_num:
            return jsonify({"error": "Debe proporcionar el número de documento"}), 400
            
        resultado = BotHub.validar(
            eps=eps,
            doc_tipo=doc_tipo,
            doc_num=doc_num,
            headless=(not visual),
            evidencias_dir=EVIDENCIAS_DIR
        )
        return jsonify(resultado)
    except Exception as e:
        return jsonify({"error": str(e), "status": "ERROR"}), 500

@app.route('/api/evidencia/<filename>')
def serve_evidencia(filename):
    return send_from_directory(EVIDENCIAS_DIR, filename)

@app.route('/api/descarga_fomag/<doc_num>/<filename>')
def serve_descarga_fomag(doc_num, filename):
    carpeta_paciente = os.path.join(DESCARGAS_FOMAG_DIR, doc_num)
    return send_from_directory(carpeta_paciente, filename)

@app.route('/api/abrir_carpeta_fomag/<doc_num>', methods=['POST', 'GET'])
def abrir_carpeta_fomag(doc_num):
    carpeta_paciente = os.path.join(DESCARGAS_FOMAG_DIR, doc_num)
    os.makedirs(carpeta_paciente, exist_ok=True)
    try:
        os.startfile(carpeta_paciente)
        return jsonify({"ok": True, "carpeta": carpeta_paciente})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e), "carpeta": carpeta_paciente}), 500


def _obtener_pdf_bytes(data: dict):
    """Descarga o decodifica el PDF del payload. Retorna (bytes, nombre) o (None, None)."""
    import base64
    import urllib.request

    pdf_b64 = data.get("pdf_b64") or ""
    url_pdf  = data.get("url_pdf")  or ""
    nombre   = data.get("pdf_nombre") or "orden.pdf"

    if pdf_b64:
        if "," in pdf_b64:
            pdf_b64 = pdf_b64.split(",", 1)[1]
        try:
            return base64.b64decode(pdf_b64), nombre
        except Exception:
            return None, None

    if url_pdf:
        bases = (["http://192.168.1.211:5050", "http://localhost:5050"]
                 if url_pdf.startswith("/") else [""])
        for base in bases:
            try:
                full = base + url_pdf
                req = urllib.request.Request(full, headers={"User-Agent": "RPA-HUB"})
                with urllib.request.urlopen(req, timeout=15) as resp:
                    raw = resp.read()
                if raw[:4] == b"%PDF":
                    return raw, nombre
            except Exception:
                continue

    return None, None


@app.route('/api/fomag/extraer_orden', methods=['POST'])
def api_fomag_extraer_orden():
    try:
        data = request.get_json(silent=True) or {}
        cups = str(data.get("cups") or "").strip()
        pdf_bytes, pdf_nombre = _obtener_pdf_bytes(data)

        if not pdf_bytes:
            return jsonify({"ok": False, "error": "No se pudo obtener el PDF de la orden", "datos": {}})

        from bots.bot_fomag_transcripcion import extraer_datos_orden_pdf
        datos_raw = extraer_datos_orden_pdf(pdf_bytes, cups)

        return jsonify({
            "ok": True,
            "datos": {
                "fecha_orden":       datos_raw.get("fecha_orden", ""),
                "medico_documento":  datos_raw.get("medico_documento", ""),
                "medico_nombre":     datos_raw.get("medico_nombre", ""),
                "medico_registro":   datos_raw.get("medico_registro", ""),
                "especialidad":      datos_raw.get("especialidad", "Fisiatria"),
                "diagnosticos":      [d["codigo"] for d in datos_raw.get("diagnosticos", [])],
                "diagnosticos_detalle": datos_raw.get("diagnosticos", []),
                "cantidad":          datos_raw.get("cantidad", 1),
                "descripcion_cups":  datos_raw.get("descripcion_cups", ""),
            },
            "tiene_pdf":    True,
            "pdf_nombre":   pdf_nombre,
            "pdf_tamano_kb": round(len(pdf_bytes) / 1024),
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e), "datos": {}}), 500


@app.route('/api/fomag/radicar_transcripcion', methods=['POST'])
def api_fomag_radicar_transcripcion():
    try:
        data = request.get_json(silent=True) or {}
        pdf_bytes, pdf_nombre = _obtener_pdf_bytes(data)

        if not pdf_bytes:
            return jsonify({"ok": False, "error": "No se pudo obtener el PDF de la orden. Adjúntelo manualmente."})

        diagnosticos_raw = data.get("diagnosticos", "")
        if isinstance(diagnosticos_raw, str):
            diagnosticos = [d.strip() for d in re.split(r"[,;]+", diagnosticos_raw) if d.strip()]
        else:
            diagnosticos = [str(d).strip() for d in (diagnosticos_raw or []) if str(d).strip()]

        from bots.bot_fomag_transcripcion import BotFomagTranscripcion
        bot = BotFomagTranscripcion()

        resultado = asyncio.run(bot.radicar(
            documento      = str(data.get("documento", "")),
            cups           = str(data.get("cups", "")),
            pdf_bytes      = pdf_bytes,
            pdf_nombre     = pdf_nombre,
            doc_tipo       = str(data.get("doc_tipo", "CC")),
            cantidad       = int(data.get("cantidad") or 1),
            fecha_orden    = str(data.get("fecha_orden", "")),
            medico_documento = str(data.get("medico_documento", "")),
            medico_registro  = str(data.get("medico_registro", "")),
            especialidad     = str(data.get("especialidad", "Fisiatria")),
            diagnosticos     = diagnosticos,
            ambito           = str(data.get("ambito", "Ambulatorio")),
            medico_ordeno    = str(data.get("medico_ordeno", "Médico especialista externo")),
            finalidad        = str(data.get("finalidad", "Diagnóstico")),
            observaciones    = str(data.get("observaciones", "Mejor oportunidad")),
            rep_nit          = str(data.get("rep_nit", "901119103")),
            rep_codigo       = str(data.get("rep_codigo", "500010210901")),
            headless         = not bool(data.get("visual", False)),
            simular          = bool(data.get("simular", False)),
            evidencias_dir   = EVIDENCIAS_DIR,
        ))

        if resultado.get("volante_pdf_nombre") and resultado.get("documento"):
            doc_clean = re.sub(r"[^0-9A-Za-z]", "", str(resultado.get("documento", "")))
            resultado["volante_pdf_url"] = f"/api/descarga_fomag/{doc_clean}/{resultado['volante_pdf_nombre']}"

        return jsonify(resultado)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


if __name__ == '__main__':
    puerto = int(os.getenv("PORT_PLAYGROUND", "5070"))
    print("\n" + "="*60)
    print("  RPA HUB - LABORATORIO VISUAL DE PRUEBAS")
    print(f"  Abra en su navegador: http://localhost:{puerto}")
    print("="*60 + "\n")
    app.run(host='0.0.0.0', port=puerto, debug=False, threaded=True)
