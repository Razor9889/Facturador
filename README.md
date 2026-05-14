# Facturador Electrónico AFIP — Documentación del Proyecto

## Autor

GitHub: [@Razor9889](https://github.com/Razor9889)

## ¿Qué es esto?

Un **webservice de facturación electrónica** que se conecta con AFIP para emitir comprobantes (facturas, notas de crédito, etc.) y obtener el **CAE** (Código de Autorización Electrónico) necesario para que un comprobante sea válido fiscalmente en Argentina.

El sistema expone una **API REST en Flask (Python)** que recibe los datos de una factura por JSON y devuelve el CAE otorgado por AFIP.

---

## Arquitectura

```
Cliente (curl / React / Excel)
        │
        ▼
  Flask API (app.py)
  POST /api/autorizar
        │
        ├── WSAA (Autenticación)
        │   └── wsaahomo.afip.gov.ar  ← Homologación
        │       Firma TRA con certificado .crt + .pem
        │       Devuelve Token + Sign (válidos 12hs)
        │
        └── WSFE (Facturación Electrónica)
            └── wswhomo.afip.gov.ar
                Envía comprobante con Token + Sign
                Devuelve CAE + Fecha Vencimiento
```

---

## Archivos del proyecto

| Archivo | Descripción |
|---|---|
| `app.py` | API principal en Flask |
| `cert.crt` | Certificado digital emitido por AFIP |
| `privkey.pem` | Clave privada del certificado |
| `token_cache.json` | Cache del token AFIP (se genera automáticamente) |
| `requirements.txt` | Dependencias Python |
| `wsaa_debug.txt` | Debug de la primera autenticación exitosa |

---

## Configuración

Las variables de entorno que controlan el comportamiento del sistema:

| Variable | Default | Descripción |
|---|---|---|
| `CUIT_EMISOR` | `20415706619` | CUIT del emisor de facturas |
| `CERT_PATH` | `cert.crt` | Ruta al certificado AFIP |
| `KEY_PATH` | `privkey.pem` | Ruta a la clave privada |
| `HOMOLOGACION` | `true` | `true` = testing, `false` = producción |

---

## Cómo funciona la autenticación (WSAA)

AFIP usa un sistema de autenticación basado en certificados digitales X.509.

1. Se genera un **TRA** (Ticket de Requerimiento de Acceso): un XML con `generationTime`, `expirationTime` y el servicio solicitado (`wsfe`).
2. El TRA se firma con OpenSSL usando el certificado y la clave privada → genera un archivo CMS en formato DER.
3. El CMS firmado se envía en Base64 al endpoint SOAP del WSAA.
4. AFIP valida la firma y devuelve un **Token** y un **Sign** válidos por 12 horas.
5. El token se cachea en memoria y en disco (`token_cache.json`) para no repetir el proceso en cada factura.

### Detalle crítico — Timezone en el TRA

Las fechas del TRA deben estar en **UTC puro con sufijo `Z`**. Sin el sufijo, AFIP interpreta la hora como local del servidor (Argentina, UTC-3) y rechaza el request con `generationTime inválido`.

```python
# ✅ Correcto
desde = (now - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")

# ❌ Incorrecto — AFIP suma 3hs y lo marca como futuro
desde = (now - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%S")

# ❌ Incorrecto — hardcodear -03:00 sobre hora UTC la pone 3hs en el futuro
desde = (now - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%S-03:00")
```

---

## Cómo funciona la facturación (WSFE)

Una vez obtenido el Token y Sign, se llama al servicio `FECAESolicitar` del WSFE con los datos del comprobante:

- Tipo de comprobante (ej: `11` = Factura C)
- Punto de venta
- CUIT del receptor
- Importes (neto, IVA, total)
- Condición IVA del receptor (requerido por RG 5616 desde 2024)

AFIP responde con el **CAE** y su fecha de vencimiento (10 días desde la emisión).

---

## Instalación

```bash
# Requisitos previos
# - Python 3.12+
# - OpenSSL instalado y en el PATH
# - Certificado AFIP (.crt + .pem) en la carpeta del proyecto

pip install -r requirements.txt
py app.py
```

El servidor queda corriendo en `http://127.0.0.1:5000`.

---

## Endpoint disponible

### `POST /api/autorizar`

Autoriza un comprobante ante AFIP y devuelve el CAE.

**Body JSON:**

```json
{
  "tipo_cbte": 11,
  "punto_vta": 1,
  "fecha_cbte": "20260513",
  "cuit_receptor": "20111111112",
  "doc_tipo": 80,
  "concepto": 1,
  "imp_neto": 1000.00,
  "alicuota_iva": 0,
  "imp_iva": 0.00,
  "imp_total": 1000.00,
  "moneda": "PES",
  "cotizacion": 1,
  "condicion_iva_receptor_id": 5
}
```

**Campos:**

| Campo | Tipo | Descripción |
|---|---|---|
| `tipo_cbte` | int | Tipo de comprobante AFIP (1=FA, 6=FB, 11=FC, etc.) |
| `punto_vta` | int | Punto de venta habilitado en AFIP |
| `fecha_cbte` | string | Fecha en formato `YYYYMMDD` |
| `cuit_receptor` | string | CUIT del cliente receptor |
| `doc_tipo` | int | Tipo de documento (80=CUIT, 96=DNI) |
| `concepto` | int | 1=Productos, 2=Servicios, 3=Mixto |
| `imp_neto` | float | Importe neto gravado |
| `alicuota_iva` | float | Alícuota IVA (0, 10.5, 21, 27) |
| `imp_iva` | float | Importe de IVA |
| `imp_total` | float | Importe total del comprobante |
| `moneda` | string | Código de moneda (`PES`, `DOL`, etc.) |
| `cotizacion` | float | Cotización (1 para pesos) |
| `condicion_iva_receptor_id` | int | Condición IVA del receptor (5=Consumidor Final) |

**Respuesta exitosa (200):**

```json
{
  "cae": "86190156302210",
  "cae_vto": "20260523",
  "nro_cbte": 1,
  "resultado": "A"
}
```

**Respuesta de error (500):**

```json
{
  "error": "Descripción del error",
  "trace": "Traceback completo para debugging"
}
```

---

## Tipos de comprobante más comunes

| Código | Descripción |
|---|---|
| 1 | Factura A |
| 2 | Nota de Débito A |
| 3 | Nota de Crédito A |
| 6 | Factura B |
| 7 | Nota de Débito B |
| 8 | Nota de Crédito B |
| 11 | Factura C |
| 12 | Nota de Débito C |
| 13 | Nota de Crédito C |

## Condición IVA del receptor (RG 5616)

| ID | Descripción |
|---|---|
| 1 | IVA Responsable Inscripto |
| 4 | IVA Sujeto Exento |
| 5 | Consumidor Final |
| 6 | Responsable Monotributo |
| 13 | Monotributista Social |

---

## Pruebas en consola

### Iniciar el servidor
```bash
cd ~/Desktop/Facturador
py app.py
```

### Probar el endpoint (en otra terminal)
```bash
py -c "
import requests
r = requests.post('http://127.0.0.1:5000/api/autorizar', json={
    'tipo_cbte': 11,
    'punto_vta': 1,
    'fecha_cbte': '20260513',
    'cuit_receptor': '20111111112',
    'doc_tipo': 80,
    'concepto': 1,
    'imp_neto': 1000.00,
    'alicuota_iva': 0,
    'imp_iva': 0,
    'imp_total': 1000.00,
    'moneda': 'PES',
    'cotizacion': 1,
    'condicion_iva_receptor_id': 5
})
print(r.status_code)
print(r.json())
"
```

### Respuesta esperada
```
200
{'cae': '86190156302210', 'cae_vto': '20260523', 'nro_cbte': 1, 'resultado': 'A'}
```

### Verificar que el token cache existe
```bash
cat ~/Desktop/Facturador/token_cache.json
```

### Recuperar token manualmente desde wsaa_debug.txt (si el cache se pierde)
```bash
py -c "
import json, re, html
from pathlib import Path

txt = Path('wsaa_debug.txt').read_text(encoding='utf-8')
decoded = html.unescape(txt)
token = re.search(r'<token>(.*?)</token>', decoded, re.S).group(1).strip()
sign  = re.search(r'<sign>(.*?)</sign>',   decoded, re.S).group(1).strip()
exp   = re.search(r'<expirationTime>(.*?)</expirationTime>', decoded, re.S).group(1).strip()
if ':' == exp[-3:-2]:
    exp = exp[:-3] + exp[-2:]
cache = {'token': token, 'sign': sign, 'expira': exp}
Path('token_cache.json').write_text(json.dumps(cache), encoding='utf-8')
print('token_cache.json creado, expira:', exp)
"
```

---

## Errores comunes y soluciones

### `generationTime posee formato o dato inválido`
El TRA se está enviando sin timezone o con timezone incorrecto. Asegurarse de que las fechas usen el sufijo `Z` (UTC).

### `alreadyAuthenticated`
AFIP ya tiene un token activo para el CUIT. Dos causas posibles:
- El token fue pedido antes y no se guardó en disco → recuperarlo con el script de `wsaa_debug.txt`.
- Se están iniciando múltiples instancias de Flask → usar solo una.

### `No se pudo parsear la respuesta del WSAA`
AFIP respondió 200 pero el XML no tiene `<token>`. Generalmente indica que el certificado no coincide con el CUIT configurado en `CUIT_EMISOR`.

### `AFIP_RECHAZO: Campo Condicion Frente al IVA del receptor es obligatorio`
Falta el campo `condicion_iva_receptor_id` en el request, requerido desde la RG 5616 de 2024.

### `AFIP_RECHAZO: DocNro no se encuentra registrado en los padrones`
En homologación usar CUITs de prueba válidos como `20111111112`. En producción el CUIT del receptor debe existir en los padrones de AFIP.

---

## Próximos pasos

### 1. Carga masiva desde Excel

Agregar un endpoint `POST /api/autorizar/lote` que:
- Reciba un archivo `.xlsx` con una fila por factura
- Use `openpyxl` o `pandas` para leer las filas
- Llame a `autorizar_comprobante()` por cada fila
- Devuelva un resumen con CAE obtenido, errores y números de comprobante

Columnas sugeridas para el Excel:

| tipo_cbte | punto_vta | fecha_cbte | cuit_receptor | imp_neto | alicuota_iva | imp_iva | imp_total |
|---|---|---|---|---|---|---|---|

### 2. Frontend en React

Construir una interfaz web para:
- Formulario de carga individual de facturas con validación en tiempo real
- Tabla de facturas emitidas con CAE, fecha y estado
- Carga masiva subiendo un archivo Excel directamente desde el browser
- Descarga del comprobante en PDF con código de barras del CAE
- Indicador del estado del token AFIP (vigente / expirado)

Stack sugerido: **React + Vite + TailwindCSS**, consumiendo la API Flask en `localhost:5000`.
