import os
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

if __name__ == '__main__':
    puerto = int(os.getenv("PORT_PLAYGROUND", "5070"))
    print("\n" + "="*60)
    print("  RPA HUB - LABORATORIO VISUAL DE PRUEBAS")
    print(f"  Abra en su navegador: http://localhost:{puerto}")
    print("="*60 + "\n")
    app.run(host='0.0.0.0', port=puerto, debug=False, threaded=True)
