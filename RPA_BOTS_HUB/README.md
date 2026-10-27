# 🤖 RPA BOTS HUB — FÁBRICA Y HUB DE VALIDACIÓN EPS

Plataforma modular para construir, probar e integrar **Bots RPA de validación de derechos y autorizaciones médicas en portales de EPS**.

---

## 📁 Estructura del Proyecto

```text
RPA_BOTS_HUB/
│
├── bots/                           # Módulos de cada EPS
│   ├── base_bot.py                 # Contrato e interfaz estándar obligatoria
│   ├── bot_colsanitas.py           # Bot Colsanitas / Sanitas (Implementado)
│   ├── bot_plantilla.py            # Plantilla para crear nuevos bots
│   └── __init__.py                 # Registro central de EPS soportadas
│
├── data/
│   └── evidencias/                 # Pantallazos PNG de respaldo de auditoría
│
├── tests/
│   ├── casos_prueba.json           # Banco de cédulas para pruebas de regresión
│   └── test_runner.py              # Ejecutor automático de casos de prueba
│
├── templates/
│   └── playground.html             # Interfaz del Laboratorio Web de pruebas
│
├── api.py                          # Microservicio REST API (Puerto 5055)
├── playground.py                   # Laboratorio interactivo visual (Puerto 5070)
├── cli.py                          # Herramienta de pruebas por consola
├── hub.py                          # Fachada Python para importación directa
├── requirements.txt                # Dependencias mínimas
└── .env                            # Credenciales y puertos
```

---

## 🧪 1. Cómo Hacer Pruebas Previas

Tienes **3 formas sencillas** de probar cualquier bot antes de usarlo en producción:

### Opción A: Laboratorio Web Visual (Recomendado)
Abre una interfaz gráfica en tu navegador para probar con un clic:
```bash
python playground.py
```
Abre en tu navegador: **`http://localhost:5070`**
1. Selecciona la EPS.
2. Ingresa la cédula (o haz clic en los botones de prueba rápida).
3. Activa o desactiva el **"Modo Visual"** (para ver cómo el navegador se abre y hace los clics en vivo).
4. Haz clic en **"Ejecutar Prueba"** y verás el resultado estructurado junto con la captura de pantalla de evidencia.

---

### Opción B: Consola CLI (Línea de Comandos)
Prueba rápida desde cualquier terminal PowerShell o Bash:

* **Prueba con navegador visible (Modo Visual):**
  ```bash
  python cli.py --eps colsanitas --doc 17337293 --visual
  ```
* **Prueba silenciosa e impresión en JSON:**
  ```bash
  python cli.py --eps colsanitas --doc 17337293 --json
  ```
* **Listar EPS soportadas:**
  ```bash
  python cli.py --list
  ```

---

### Opción C: Batería Automática de Casos (`test_runner.py`)
Ejecuta automáticamente todas las cédulas configuradas en `tests/casos_prueba.json` y verifica que ninguna regla se haya roto:
```bash
python tests/test_runner.py
```

---

## 🚀 2. Cómo Integrar el Bot en Otro Proyecto

### Método 1: Como Microservicio API REST (HTTP)
Inicia la API:
```bash
python api.py
```
*(Escuchando en el puerto `5055`)*

Cualquier sistema (CitasWeb, PHP, Node.js, n8n, Digiturno, etc.) solo debe hacer un `POST`:

#### Ejemplo con cURL:
```bash
curl -X POST http://localhost:5055/api/validar \
  -H "Content-Type: application/json" \
  -d '{
    "eps": "colsanitas",
    "doc_tipo": "CC",
    "doc_num": "17337293",
    "headless": true
  }'
```

#### Ejemplo con JavaScript / Fetch (Web o Node.js):
```javascript
const respuesta = await fetch('http://localhost:5055/api/validar', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    eps: 'colsanitas',
    doc_tipo: 'CC',
    doc_num: '17337293',
    headless: true
  })
});
const resultado = await respuesta.json();
console.log(resultado.data.num_autorizacion); // "356856718"
```

---

### Método 2: Como Módulo Python Directo (Si tu otro proyecto es Python)
En tu otro proyecto, importa la fachada `BotHub`:
```python
import sys
sys.path.append("C:/Users/86055679.NEURODX/Desktop/RPA_BOTS_HUB")

from hub import BotHub

resultado = BotHub.validar(
    eps="colsanitas",
    doc_tipo="CC",
    doc_num="17337293",
    headless=True
)

if resultado["status"] == "AUTORIZADO":
    print(f"Volante aprobado: #{resultado['num_autorizacion']}")
    print(f"Vigencia: {resultado['fecha_vigencia']}")
elif resultado["status"] == "SIN_VOLANTE":
    print("El paciente no tiene autorización expedida aún.")
```

---

## 🧩 3. Cómo Crear un Nuevo Bot (Paso a Paso)

1. **Duplica la plantilla:**
   Copia `bots/bot_plantilla.py` y nómbrala con la nueva EPS (ej: `bots/bot_nuevaeps.py`).

2. **Personaliza nombres y credenciales:**
   ```python
   class BotNuevaEPS(BaseEPSBot):
       NOMBRE_EPS = "Nueva EPS"
       CODIGO_EPS = "nuevaeps"
   ```

3. **Programa los pasos de la página en `consultar()`:**
   - Login en el portal (`page.fill("#user", ...)`).
   - Búsqueda por documento (`page.fill("#doc", ...)`).
   - Extracción de datos y captura de pantalla (`page.screenshot(...)`).

4. **Regístralo en `bots/__init__.py`:**
   ```python
   from .bot_nuevaeps import BotNuevaEPS

   REGISTRO_BOTS = {
       "colsanitas": BotColsanitas,
       "nuevaeps": BotNuevaEPS  # <-- Agregado
   }
   ```
¡Listo! Automáticamente estará disponible en el Playground web, en la CLI y en la API REST sin tener que tocar nada más.

---

## 🐳 4. Despliegue con Docker (Opcional)

Si deseas correr este Hub en un servidor Linux/Debian con Docker:
```bash
docker compose up -d
```
- **API REST:** `http://<IP_SERVIDOR>:5055`
- **Playground Web:** `http://<IP_SERVIDOR>:5070`
