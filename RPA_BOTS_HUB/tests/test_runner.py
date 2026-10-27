import os
import sys
import json
import time

# Agregar directorio padre al path
HUB_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HUB_DIR not in sys.path:
    sys.path.insert(0, HUB_DIR)

from hub import BotHub

def run_tests():
    casos_file = os.path.join(os.path.dirname(__file__), "casos_prueba.json")
    if not os.path.exists(casos_file):
        print(f"[ERROR] No se encontró el archivo de casos: {casos_file}")
        return

    with open(casos_file, "r", encoding="utf-8") as f:
        casos = json.load(f)

    print("\n" + "="*70)
    print(f"  EJECUTANDO BATERÍA DE PRUEBAS DE REGRESIÓN ({len(casos)} CASOS)")
    print("="*70)

    aciertos = 0
    errores = 0

    for i, c in enumerate(casos, 1):
        print(f"\n[{i}/{len(casos)}] Probando: {c['descripcion']}")
        print(f"      EPS: {c['eps']} | Doc: {c['tipo']} {c['documento']}")
        
        t0 = time.time()
        try:
            res = BotHub.validar(
                eps=c["eps"],
                doc_tipo=c["tipo"],
                doc_num=c["documento"],
                headless=True
            )
            duracion = round(time.time() - t0, 1)
            
            # Validar expectativas
            esperado_status = c["esperado"].get("status")
            status_obtenido = res.get("status")
            
            pasa = True
            if esperado_status and status_obtenido != esperado_status:
                pasa = False
                
            if "num_autorizacion_contiene" in c["esperado"]:
                req_num = c["esperado"]["num_autorizacion_contiene"]
                actual_num = str(res.get("num_autorizacion") or "")
                if req_num not in actual_num:
                    pasa = False
                    
            if pasa:
                aciertos += 1
                print(f"      [PASÓ] Status: {status_obtenido} (Tiempo: {duracion}s)")
                if res.get("num_autorizacion"):
                    print(f"             Autorización: #{res['num_autorizacion']} | Vigencia: {res.get('fecha_vigencia')}")
            else:
                errores += 1
                print(f"      [FALLÓ] Esperado: {esperado_status} | Obtenido: {status_obtenido}")
                print(f"              Mensaje: {res.get('mensaje')}")
                
        except Exception as e:
            errores += 1
            print(f"      [ERROR DE EJECUCIÓN]: {e}")

    print("\n" + "="*70)
    print(f"  RESUMEN DE PRUEBAS: {aciertos} PASADAS | {errores} FALLIDAS")
    print("="*70 + "\n")

if __name__ == "__main__":
    run_tests()
