# Facturador Electrónico AFIP

## Autor

GitHub: [@Razor9889](https://github.com/Razor9889)

## ¿Qué es esto?

Un **webservice de facturación electrónica** que se conecta con AFIP para emitir comprobantes y obtener el **CAE** (Código de Autorización Electrónico) necesario para que un comprobante sea válido fiscalmente en Argentina.

El sistema expone una **API REST en Flask (Python)** que recibe los datos de una factura por JSON (o por Excel masivo) y devuelve el CAE otorgado por AFIP. Cada comprobante autorizado se guarda en una base de datos SQLite local y se puede descargar como PDF con QR de verificación.

---

## Arquitectura

```
Cliente (React / Excel / curl)
        │
        ▼
  Flask API (app.py)
        │
        ├── WSAA (Autenticación)
        │   └── wsaahomo.afip.gov.ar  ← Homologación
        │       Firma TRA con certificado .crt + .pem
        │       Devuelve Token + Sign (válidos 12hs)
        │       Se cachea en temp/token_cache.json
        │
        ├── WSFE (Facturación Electrónica)
        │   └── wswhomo.afip.gov.ar
        │       Envía comprobante con Token + Sign
        │       Devuelve CAE + Fecha Vencimiento
        │
        ├── SQLite (temp/facturas.db)
        │   └── Guarda cada comprobante autorizado
        │
        └── PDF + QR
            └── Genera PDF con QR de verificación AFIP (RG 5616)
```

---

## Estructura de carpetas

```
Facturador/
├── app.py                  ← API principal
├── index.html              ← Frontend (reemplazar por React)
├── requirements.txt
├── README.md
├── facturas.xlsx           ← Excel modelo para carga masiva
├── certs/
│   ├── cert.crt            ← Certificado AFIP (secreto)
│   └── privkey.pem         ← Clave privada (secreto)
└── temp/                   ← Generado automáticamente
    ├── token_cache.json    ← Cache del token AFIP (secreto)
    ├── facturas.db         ← Base de datos SQLite
    └── resultados_facturas.xlsx
```

---

## Instalación

```bash
# Requisitos previos:
# - Python 3.12+
# - OpenSSL en el PATH
# - Certificado AFIP en certs/

pip install -r requirements.txt
py app.py
```

El servidor queda corriendo en `http://127.0.0.1:5000`.

---

## Configuración

| Variable | Default | Descripción |
|---|---|---|
| `CUIT_EMISOR` | `20415706619` | CUIT del emisor de facturas |
| `CERT_PATH` | `certs/cert.crt` | Ruta al certificado AFIP |
| `KEY_PATH` | `certs/privkey.pem` | Ruta a la clave privada |
| `HOMOLOGACION` | `true` | `true` = testing, `false` = producción |

---

## Cómo funciona la autenticación (WSAA)

AFIP usa autenticación basada en certificados digitales X.509.

1. Se genera un **TRA** (Ticket de Requerimiento de Acceso): XML con `generationTime`, `expirationTime` y el servicio (`wsfe`).
2. El TRA se firma con OpenSSL → genera un CMS en formato DER.
3. El CMS en Base64 se envía al endpoint SOAP del WSAA.
4. AFIP devuelve **Token** y **Sign** válidos por 12 horas.
5. El token se cachea en memoria y en `temp/token_cache.json`. Al expirar se renueva automáticamente.

### Detalle crítico — Timezone en el TRA

Las fechas deben estar en **UTC puro con sufijo `Z`**:

```python
# ✅ Correcto
desde = (now - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")

# ❌ Sin timezone — AFIP lo interpreta como hora local Argentina y lo rechaza
desde = (now - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%S")

# ❌ Con -03:00 hardcodeado sobre hora UTC — la pone 3hs en el futuro
desde = (now - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%S-03:00")
```

---

## Cómo funciona la facturación (WSFE)

Con el Token y Sign se llama a `FECAESolicitar` con los datos del comprobante. El número de comprobante se obtiene automáticamente consultando `FECompUltimoAutorizado` y sumando 1 — no hay que manejarlo a mano.

AFIP responde con el **CAE** y su fecha de vencimiento (10 días desde la emisión).

---

## Generación de PDF con QR (RG 5616)

Desde la RG 5616 (2024) AFIP reemplazó el código de barras por un **QR** que apunta a:

```
https://www.afip.gob.ar/fe/qr/?p=BASE64
```

Donde el BASE64 es un JSON con los datos del comprobante:

```json
{
  "ver": 1,
  "fecha": "2026-05-14",
  "cuit": 20415706619,
  "ptoVta": 1,
  "tipoCmp": 11,
  "nroCmp": 3,
  "importe": 1000.00,
  "moneda": "PES",
  "ctz": 1,
  "tipoDocRec": 80,
  "nroDocRec": 20111111112,
  "tipoCodAut": "E",
  "codAut": 86190156302210
}
```

---

## Endpoints disponibles

| Método | Ruta | Descripción |
|---|---|---|
| GET | `/api/status` | Estado del backend y ambiente |
| GET | `/api/ultimo-cbte?punto_vta=1&tipo_cbte=11` | Último comprobante autorizado en AFIP |
| POST | `/api/autorizar` | Autoriza una factura (JSON) |
| POST | `/api/upload-excel` | Autoriza lote desde Excel |
| GET | `/api/descargar-resultado` | Descarga el Excel con resultados del último lote |
| GET | `/api/comprobantes` | Lista comprobantes guardados (con filtros) |
| GET | `/api/comprobantes/<id>/pdf` | Descarga PDF individual |
| POST | `/api/comprobantes/zip` | Descarga ZIP con PDFs seleccionados |

### Filtros de `/api/comprobantes`

```
GET /api/comprobantes?desde=2026-05-01&hasta=2026-05-31&tipo_cbte=11&cuit_receptor=20111111112
```

### Body de `/api/autorizar`

```json
{
  "tipo_cbte": 11,
  "punto_vta": 1,
  "fecha_cbte": "20260514",
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

### Body de `/api/comprobantes/zip`

```json
{ "ids": [1, 2, 3, 4] }
```

---

## Tipos de comprobante

| Código | Descripción |
|---|---|
| 1 | Factura A |
| 3 | Nota de Crédito A |
| 6 | Factura B |
| 8 | Nota de Crédito B |
| 11 | Factura C |
| 13 | Nota de Crédito C |
| 19 | Factura E (exportación) |
| 51 | Factura M |

## Condición IVA del receptor (RG 5616)

| ID | Descripción |
|---|---|
| 1 | IVA Responsable Inscripto |
| 4 | IVA Sujeto Exento |
| 5 | Consumidor Final |
| 6 | Responsable Monotributo |
| 13 | Monotributista Social |

## Alícuotas IVA

| Lo que se manda | ID AFIP | Significado |
|---|---|---|
| `0` | `3` | Exento |
| `10.5` | `4` | 10,5% |
| `21` | `5` | 21% |
| `27` | `6` | 27% |

## Monedas

| Código | Moneda |
|---|---|
| `PES` | Peso Argentino |
| `DOL` | Dólar Estadounidense |
| `EU` | Euro |

---

## Pruebas en consola

### Iniciar el servidor
```bash
cd ~/Desktop/Facturador
py app.py
```

### Autorizar una factura individual
```bash
py -c "
import requests
r = requests.post('http://127.0.0.1:5000/api/autorizar', json={
    'tipo_cbte': 11, 'punto_vta': 1,
    'fecha_cbte': '20260514',
    'cuit_receptor': '20111111112',
    'concepto': 1,
    'imp_neto': 1000.00, 'alicuota_iva': 0,
    'imp_iva': 0, 'imp_total': 1000.00,
    'moneda': 'PES', 'cotizacion': 1,
    'condicion_iva_receptor_id': 5
})
print(r.status_code, r.json())
"
```

### Respuesta esperada
```
200
{'cae': '86190156302210', 'cae_vto': '20260524', 'nro_cbte': 2, 'resultado': 'A'}
```

---

## Errores comunes y soluciones

### `generationTime posee formato o dato inválido`
Las fechas del TRA no tienen timezone. Usar sufijo `Z` (UTC puro).

### `alreadyAuthenticated`
AFIP ya tiene un token activo y el cache local está vacío. Esperar que expire (~12hs) o recuperar el token del `wsaa_debug.txt` si existe.

### `ValidacionDeToken: No validaron las fechas del token`
El `temp/token_cache.json` está vencido. El sistema lo borra automáticamente al arrancar o al hacer el próximo request. Si persiste, borrarlo manualmente y reiniciar Flask.

### `No se pudo parsear la respuesta del WSAA` (con status 200)
La respuesta de AFIP viene con el XML interno encodificado como HTML entities. El código aplica `html.unescape()` antes del regex. Si aparece este error, verificar que la versión del `app.py` sea la correcta.

### `ERR 10016: El numero o fecha del comprobante no se corresponde`
La fecha del comprobante es anterior a uno ya emitido para ese punto de venta y tipo. Usar la fecha de hoy en formato `YYYYMMDD`.

### `FECAEDetResponse instance has no attribute 'Errores'`
El objeto zeep no tiene `Errores` a nivel de detalle, solo `Observaciones`. Los errores de negocio vienen en `det.Observaciones.Obs` y los de cabecera en `res.Errors.Err`.

### `AFIP_RECHAZO: Campo Condicion Frente al IVA del receptor es obligatorio`
Falta el campo `condicion_iva_receptor_id`. Requerido desde la RG 5616 de 2024.

### `AFIP_RECHAZO: DocNro no se encuentra registrado en los padrones`
En homologación usar CUITs de prueba válidos como `20111111112`.

### `AFIP_RECHAZO:` con mensaje vacío
AFIP rechazó pero no envió observaciones. Verificar fecha, tipo de comprobante y CUIT receptor.

---

## Próximos pasos

### Frontend en React
- Tabla de comprobantes emitidos con filtros por fecha, tipo y CUIT
- Descarga individual de PDF con QR
- Descarga por lote (ZIP con PDFs seleccionados)
- Carga masiva subiendo Excel directamente desde el browser
- Indicador del estado del token AFIP (vigente / expirado / tiempo restante)

Stack: **React + Vite + TailwindCSS** consumiendo la API Flask en `localhost:5000`.
