import sys
import json
import argparse
from hub import BotHub

def main():
    parser = argparse.ArgumentParser(
        description="CLI para pruebas y ejecución de Bots RPA Validador de EPS."
    )
    parser.add_argument("--eps", "-e", type=str, default="colsanitas", help="Código de la EPS (ej: colsanitas, sanitas)")
    parser.add_argument("--tipo", "-t", type=str, default="CC", help="Tipo de documento (CC, TI, CE, etc.)")
    parser.add_argument("--doc", "-d", type=str, help="Número de documento a consultar")
    parser.add_argument("--visual", "-v", action="store_true", help="Abre el navegador visualmente (headless=False) para observar la prueba")
    parser.add_argument("--json", "-j", action="store_true", help="Imprime solo el resultado en formato JSON puro")
    parser.add_argument("--list", "-l", action="store_true", help="Lista todas las EPS soportadas")

    args = parser.parse_args()

    if args.list:
        print("\n=== EPS SOPORTADAS EN EL HUB ===")
        for eps in BotHub.eps_disponibles():
            print(f" * [{eps['codigo']}] {eps['nombre']} (Alias: {', '.join(eps['alias'])})")
        print()
        sys.exit(0)

    if not args.doc:
        parser.print_help()
        print("\n[ERROR] Debe especificar el documento a consultar usando --doc <numero>\n")
        sys.exit(1)

    is_headless = not args.visual

    if not args.json:
        print("\n" + "="*60)
        print("  INICIANDO PRUEBA DE BOT RPA")
        print("="*60)
        print(f"  EPS:         {args.eps.upper()}")
        print(f"  Documento:   {args.tipo} {args.doc}")
        print(f"  Modo Visual: {'SÍ (Navegador visible)' if args.visual else 'NO (Invisible / Headless)'}")
        print("="*60)
        print("Consultando portal EPS, por favor espere...\n")

    resultado = BotHub.validar(
        eps=args.eps,
        doc_tipo=args.tipo,
        doc_num=args.doc,
        headless=is_headless
    )

    if args.json:
        print(json.dumps(resultado, indent=2, ensure_ascii=False))
    else:
        print("\n" + "="*60)
        print(f"  RESULTADO DE LA CONSULTA: [{resultado['status']}]")
        print("="*60)
        print(f"  Estado EPS:        {resultado.get('estado_eps')}")
        print(f"  Autorización / Vol:{resultado.get('num_autorizacion') or 'No disponible'}")
        print(f"  CUPS:              {resultado.get('cups') or 'No disponible'}")
        print(f"  Servicio:          {resultado.get('servicio') or 'No disponible'}")
        print(f"  Vigencia:          {resultado.get('fecha_vigencia') or 'No disponible'}")
        print(f"  Prestador:         {resultado.get('prestador') or 'No disponible'}")
        print(f"  Mensaje:           {resultado.get('mensaje')}")
        print(f"  Captura Soporte:   {resultado.get('soporte_evidencia')}")
        print(f"  Fecha Consulta:    {resultado.get('fecha_consulta')}")
        print("="*60 + "\n")

if __name__ == "__main__":
    main()
