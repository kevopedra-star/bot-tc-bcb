import calendar
import datetime
import os
import re
from bs4 import BeautifulSoup
import requests
from supabase import Client, create_client

# Lee de las variables cifradas de GitHub Secrets
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise ValueError("❌ Faltan las variables de entorno SUPABASE_URL o SUPABASE_KEY.")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

URL_IFRAME = "https://www.bcb.gob.bo/librerias/indicadores/dolar/periodos.php"

MESES_TEXTO = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4,
    "mayo": 5, "junio": 6, "julio": 7, "agosto": 8,
    "septiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12
}

def parsear_fecha_texto(texto):
    patron_texto = r"(\d{1,2})\s+de\s+([a-zA-ZáéíóúÁÉÍÓÚ]+)(?:\s+de)?\s+(\d{4})"
    match = re.search(patron_texto, texto, re.IGNORECASE)
    if match:
        dia = int(match.group(1))
        mes_nombre = match.group(2).lower()
        anio = int(match.group(3))
        mes_num = MESES_TEXTO.get(mes_nombre)
        if mes_num:
            return datetime.date(anio, mes_num, dia)
            
    patron_slash = r"(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})"
    match_s = re.search(patron_slash, texto)
    if match_s:
        dia, mes, anio = int(match_s.group(1)), int(match_s.group(2)), int(match_s.group(3))
        return datetime.date(anio, mes, dia)
        
    return None

def obtener_ultima_fecha_bd():
    try:
        res = (
            supabase.table("tipo_cambio_bcb")
            .select("fecha, tipo_cambio")
            .order("fecha", desc=True)
            .limit(1)
            .execute()
        )
        if res.data:
            f_str = res.data[0]["fecha"]
            tc = float(res.data[0]["tipo_cambio"])
            return datetime.date.fromisoformat(f_str), tc
    except Exception as e:
        print(f"⚠️ Error al consultar última fecha en Supabase: {e}")
    return None, None

def consultar_bcb_rango(fecha_inicio, fecha_fin):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://www.bcb.gob.bo/?q=cotizaciones_tc"
    }
    
    params = {
        "sdd": str(fecha_inicio.day),
        "smm": str(fecha_inicio.month),
        "saa": str(fecha_inicio.year),
        "edd": str(fecha_fin.day),
        "emm": str(fecha_fin.month),
        "eaa": str(fecha_fin.year),
        "qlist": "1",
        "Button": "  Ver  "
    }
    
    session = requests.Session()
    tc_oficiales = {}
    
    try:
        res = session.get(URL_IFRAME, headers=headers, params=params, timeout=25)
        soup = BeautifulSoup(res.text, "html.parser")
        
        for fila in soup.find_all("tr"):
            texto_fila = " ".join([c.get_text(" ", strip=True) for c in fila.find_all(["td", "th"])])
            fecha_obj = parsear_fecha_texto(texto_fila)
            if not fecha_obj:
                continue
                
            valores_tc = re.findall(r"Bs\s*([0-9]+[.,][0-9]+)", texto_fila, re.IGNORECASE)
            if not valores_tc:
                valores_tc = re.findall(r"([0-9]{1,2}[.,][0-9]{2,4})", texto_fila)
                
            if valores_tc:
                val_str = valores_tc[1].replace(",", ".") if len(valores_tc) >= 2 else valores_tc[0].replace(",", ".")
                tc_oficiales[fecha_obj] = float(val_str)
                
    except Exception as e:
        print(f"❌ Error al consultar portal BCB: {e}")
        
    return tc_oficiales

def sincronizar_dias_faltantes():
    hoy = datetime.date.today()
    print("\n" + "="*70)
    print(f"[{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Verificando días pendientes...")

    ultima_fecha, ultimo_tc = obtener_ultima_fecha_bd()
    
    if not ultima_fecha:
        fecha_inicio = hoy - datetime.timedelta(days=30)
    else:
        fecha_inicio = ultima_fecha

    tc_publicados = consultar_bcb_rango(fecha_inicio, hoy)
    
    if ultimo_tc is None and tc_publicados:
        primer_dia = min(tc_publicados.keys())
        ultimo_tc = tc_publicados[primer_dia]

    registros_a_insertar = []
    dia_actual = fecha_inicio

    while dia_actual <= hoy:
        if dia_actual in tc_publicados:
            ultimo_tc = tc_publicados[dia_actual]
            
        if ultimo_tc is not None:
            registros_a_insertar.append({
                "fecha": dia_actual.isoformat(),
                "moneda": "USD",
                "tipo_cambio": round(ultimo_tc, 4)
            })
            
        dia_actual += datetime.timedelta(days=1)

    if not registros_a_insertar:
        print("Todo se encuentra al día. No hay registros pendientes.")
        print("="*70)
        return

    print(f"📋 Se procesarán {len(registros_a_insertar)} día(s):")
    for r in registros_a_insertar:
        print(f" • {r['fecha']} -> USD {r['tipo_cambio']}")

    print("\n⏳ Enviando lote a Supabase...")
    try:
        res = supabase.table("tipo_cambio_bcb").upsert(registros_a_insertar, on_conflict="fecha").execute()
        print(f"✅ Sincronización exitosa. Registros actualizados: {len(res.data)}")
    except Exception as e:
        print(f"❌ Error al enviar a Supabase: {repr(e)}")

    print("="*70)

if __name__ == "__main__":
    sincronizar_dias_faltantes()