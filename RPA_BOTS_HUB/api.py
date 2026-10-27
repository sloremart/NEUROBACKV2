import os
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
