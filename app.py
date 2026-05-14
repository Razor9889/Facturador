import os, base64, re, traceback, subprocess, json
from datetime import datetime, timezone, timedelta
from pathlib import Path

from flask import Flask, request, jsonify
from flask_cors import CORS
from zeep import Client as ZeepClient
from zeep.transports import Transport
import requests

# --- CONFIGURACIÓN ---
CUIT_EMISOR   = int(os.getenv("CUIT_EMISOR",   "20415706619"))
CERT_PATH     = os.getenv("CERT_PATH",         "cert.crt")
KEY_PATH      = os.getenv("KEY_PATH",          "privkey.pem")
HOMOLOGACION  = os.getenv("HOMOLOGACION", "true").lower() == "true"

WSAA_URL = (
    "https://wsaahomo.afip.gov.ar/ws/services/LoginCms"
    if HOMOLOGACION else
    "https://wsaa.afip.gov.ar/ws/services/LoginCms"
)
WSFE_WSDL = (
    "https://wswhomo.afip.gov.ar/wsfev1/service.asmx?WSDL"
    if HOMOLOGACION else
    "https://servicios1.afip.gov.ar/wsfev1/service.asmx?WSDL"
)

app = Flask(__name__)
CORS(app)

# Cache en memoria + en disco (sobrevive reinicios de Flask)
_token_cache = {"token": None, "sign": None, "expira": None}
TOKEN_CACHE_FILE = Path("token_cache.json")

def _save_token_cache():
    data = {
        "token":  _token_cache["token"],
        "sign":   _token_cache["sign"],
        "expira": _token_cache["expira"].isoformat() if _token_cache["expira"] else None,
    }
    TOKEN_CACHE_FILE.write_text(json.dumps(data), encoding="utf-8")

def _load_token_cache():
    global _token_cache
    if TOKEN_CACHE_FILE.exists():
        try:
            data = json.loads(TOKEN_CACHE_FILE.read_text(encoding="utf-8"))
            if data.get("expira"):
                data["expira"] = datetime.fromisoformat(data["expira"])
            _token_cache.update(data)
            pass
        except Exception as e:
            pass

# Cargar cache al arrancar
_load_token_cache()

# --- AUTENTICACIÓN (WSAA) ---

def _build_tra() -> str:
    """Genera el TRA en UTC puro (Z) para evitar problemas de timezone con AFIP."""
    now = datetime.now(timezone.utc)
    desde = (now - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    hasta = (now + timedelta(hours=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
    uni   = str(int(now.timestamp()))

    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<loginTicketRequest version="1.0">'
        '<header>'
        f'<uniqueId>{uni}</uniqueId>'
        f'<generationTime>{desde}</generationTime>'
        f'<expirationTime>{hasta}</expirationTime>'
        '</header>'
        '<service>wsfe</service>'
        '</loginTicketRequest>'
    )

def _sign_tra(tra_xml: str) -> str:
    """Firma el TRA usando OpenSSL."""
    tra_path = Path("tra.xml")
    cms_path = Path("tra.cms")
    try:
        tra_path.write_text(tra_xml, encoding="utf-8")
        if cms_path.exists(): cms_path.unlink()

        subprocess.run([
            "openssl", "smime", "-sign",
            "-in",      str(tra_path),
            "-out",     str(cms_path),
            "-signer",  CERT_PATH,
            "-inkey",   KEY_PATH,
            "-outform", "DER",
            "-nodetach",
        ], check=True, capture_output=True)

        return base64.b64encode(cms_path.read_bytes()).decode("utf-8")
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Error al firmar con OpenSSL: {e.stderr.decode()}")
    finally:
        if tra_path.exists():
            try: tra_path.unlink()
            except: pass
        if cms_path.exists():
            try: cms_path.unlink()
            except: pass

def obtener_token() -> tuple[str, str]:
    """Obtiene o recupera del cache el Token y Sign del WSAA."""
    global _token_cache
    now = datetime.now(timezone.utc)

    # Si el cache en memoria es válido, lo usamos
    if _token_cache["token"] and _token_cache["expira"] and now < (_token_cache["expira"] - timedelta(minutes=5)):
        pass
        return _token_cache["token"], _token_cache["sign"]

    tra = _build_tra()
    cms = _sign_tra(tra)

    soap_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" '
        'xmlns:wsaa="http://wsaa.view.sua.dvadac.desein.afip.gov.ar">'
        '<soapenv:Body>'
        '<wsaa:loginCms>'
        f'<wsaa:in0>{cms}</wsaa:in0>'
        '</wsaa:loginCms>'
        '</soapenv:Body>'
        '</soapenv:Envelope>'
    )

    resp = requests.post(
        WSAA_URL,
        data=soap_xml,
        headers={"Content-Type": "text/xml; charset=utf-8", "SOAPAction": ""},
        timeout=30,
    )

    # AFIP devuelve 500 con alreadyAuthenticated cuando ya hay un TA vigente.
    # En ese caso reutilizamos el token guardado en disco.
    if resp.status_code != 200:
        if "alreadyAuthenticated" in resp.text and _token_cache["token"]:
            pass
            return _token_cache["token"], _token_cache["sign"]
        raise RuntimeError(f"WSAA_AFIP_ERROR_{resp.status_code}: {resp.text[:500]}")

    body = resp.text
    pass

    try:
        token = re.search(r"<token>(.*?)</token>", body, re.S).group(1)
        sign  = re.search(r"<sign>(.*?)</sign>",   body, re.S).group(1)
        exp   = re.search(r"<expirationTime>(.*?)</expirationTime>", body, re.S).group(1)

        # Normalizar formato de fecha para fromisoformat
        if ":" == exp[-3:-2]:
            exp = exp[:-3] + exp[-2:]

        _token_cache = {
            "token":  token,
            "sign":   sign,
            "expira": datetime.fromisoformat(exp),
        }
        _save_token_cache()
        return token, sign
    except Exception:
        raise RuntimeError(f"No se pudo parsear la respuesta del WSAA. Body: {body[:300]}")

# --- NEGOCIO (WSFE) ---

def _wsfe_client():
    session = requests.Session()
    return ZeepClient(WSFE_WSDL, transport=Transport(session=session))

def ultimo_cbte(punto_vta: int, tipo_cbte: int) -> int:
    token, sign = obtener_token()
    client = _wsfe_client()
    auth = {"Token": token, "Sign": sign, "Cuit": CUIT_EMISOR}
    res = client.service.FECompUltimoAutorizado(Auth=auth, PtoVta=punto_vta, CbteTipo=tipo_cbte)
    if res.Errors:
        raise RuntimeError(f"AFIP_ULTIMO: {res.Errors.Err[0].Msg}")
    return res.CbteNro

def autorizar_comprobante(factura: dict) -> dict:
    token, sign = obtener_token()
    client = _wsfe_client()
    auth = {"Token": token, "Sign": sign, "Cuit": CUIT_EMISOR}

    punto_vta = int(factura["punto_vta"])
    tipo_cbte = int(factura["tipo_cbte"])
    nro_cbte  = ultimo_cbte(punto_vta, tipo_cbte) + 1
    fecha     = str(factura["fecha_cbte"]).replace("-", "").replace("/", "")

    imp_neto  = float(factura.get("imp_neto", 0))
    imp_iva   = float(factura.get("imp_iva", 0))
    imp_total = float(factura.get("imp_total", 0))
    alicuota  = float(factura.get("alicuota_iva", 0))

    ALICUOTA_IDS = {0: 3, 2.5: 9, 5: 8, 10.5: 4, 21: 5, 27: 6}
    id_iva = ALICUOTA_IDS.get(alicuota, 3)

    iva_list = []
    if alicuota > 0 and imp_iva > 0:
        iva_list = [{"Id": id_iva, "BaseImp": round(imp_neto, 2), "Importe": round(imp_iva, 2)}]

    cbte = {
        "Concepto":   int(factura.get("concepto", 1)),
        "DocTipo":    int(factura.get("doc_tipo", 80)),
        "DocNro":     int(factura["cuit_receptor"]),
        "CbteDesde":  nro_cbte,
        "CbteHasta":  nro_cbte,
        "CbteFch":    fecha,
        "ImpTotal":   round(imp_total, 2),
        "ImpTotConc": 0.0,
        "ImpNeto":    round(imp_neto, 2),
        "ImpOpEx":    0.0,
        "ImpIVA":     round(imp_iva, 2),
        "ImpTrib":    0.0,
        "MonId":      str(factura.get("moneda", "PES")),
        "MonCotiz":   float(factura.get("cotizacion", 1)),
        "Iva": {"AlicIva": iva_list} if iva_list else None,
        # RG 5616: condición frente al IVA del receptor (5 = Consumidor Final)
        "CondicionIVAReceptorId": int(factura.get("condicion_iva_receptor_id", 5)),
    }

    if cbte["Concepto"] in (2, 3):
        cbte["FchServDesde"] = str(factura.get("fecha_desde", fecha))
        cbte["FchServHasta"] = str(factura.get("fecha_hasta", fecha))
        cbte["FchVtoPago"]   = str(factura.get("fecha_vto_pago", fecha))

    req = {
        "FeCAEReq": {
            "FeCabReq": {"CantReg": 1, "PtoVta": punto_vta, "CbteTipo": tipo_cbte},
            "FeDetReq": {"FECAEDetRequest": [cbte]}
        }
    }

    res = client.service.FECAESolicitar(Auth=auth, **req)

    if res.FeDetResp:
        det = res.FeDetResp.FECAEDetResponse[0]
        if det.Resultado == "R":
            obs = "; ".join(o.Msg for o in (det.Observaciones.Obs if det.Observaciones else []))
            raise RuntimeError(f"AFIP_RECHAZO: {obs}")
        return {
            "cae":       det.CAE,
            "cae_vto":   det.CAEFchVto,
            "nro_cbte":  nro_cbte,
            "resultado": det.Resultado,
        }

    if res.Errors:
        raise RuntimeError(f"AFIP_SOAP_ERROR: {res.Errors.Err[0].Msg}")

# --- API ---

@app.route("/api/autorizar", methods=["POST"])
def api_autorizar():
    try:
        data = request.json
        if not data:
            return jsonify({"error": "No se enviaron datos"}), 400
        res = autorizar_comprobante(data)
        return jsonify(res)
    except Exception as e:
        return jsonify({
            "error": str(e),
            "trace": traceback.format_exc()
        }), 500

if __name__ == "__main__":
    app.run(debug=True, port=5000)