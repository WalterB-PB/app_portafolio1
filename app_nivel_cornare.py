"""
App Streamlit — Nivel de ríos/quebradas (CORNARE / MARCO) · Estación 41

Cinco secciones (inicio · niveles · municipio · crecientes · estiaje) con
navegación por query params, así que los parámetros del sidebar sobreviven
el cambio de página: ?p=...&estacion=...&desde=...&hasta=...

Para correrla:  streamlit run app_nivel_cornare.py
"""

import inspect
import math

import requests
import urllib3
import pandas as pd
import numpy as np
import streamlit as st
import altair as alt
import pydeck as pdk

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ------------------------------------------------------------------
# Constantes
# ------------------------------------------------------------------
API_ROOT = "https://marco.cornare.gov.co/api/v1"

LAT_DEFECTO = 6.2766
LON_DEFECTO = -75.5901

ESTACION_DEFECTO = "41"
NOMBRE_DEFECTO = "Tu Nombre Aquí"
DESDE_DEFECTO = pd.to_datetime("2026-08-23").date()
HASTA_DEFECTO = pd.to_datetime("2026-08-30").date()

LLAVE_FECHA = "fecha"
LLAVE_VALOR = "nivel"
CANDIDATOS_LAT = ["lat", "latitude", "latitud"]
CANDIDATOS_LON = ["lng", "lon", "longitude", "longitud"]

FORMATO_FECHA = "%Y-%m-%d %H:%M"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
}

SECCIONES = [
    ("inicio", "Inicio", "La estación y su serie de nivel"),
    ("niveles", "Niveles", "Estadística de la ventana consultada"),
    ("municipio", "Municipio", "Abejorral, sus veredas y la estación"),
    ("crecientes", "Crecientes", "Qué se afecta cuando el nivel sube"),
    ("estiaje", "Niveles bajos", "Qué se compromete cuando el nivel baja"),
]

# ------------------------------------------------------------------
# CONTEXTO_LOCAL — datos escritos en el código (no provienen de la API).
# Cada entrada declara su campo `fuente`, que la app pinta como badge:
# "API CORNARE" = dato medido; "Supuesto del ejercicio" = supuesto editable.
# ------------------------------------------------------------------
PERFIL_MUNICIPIO = {
    "texto": (
        "Abejorral es un municipio de montaña del oriente antioqueño, perteneciente a la región "
        "del Páramo. Su economía gira en torno a la agricultura de pequeña escala y la ganadería "
        "de leche. La Quebrada La Aduanilla atraviesa el perímetro urbano por los barrios "
        "Los Llanos y Los Dolores, donde convive con viviendas, huertas y senderos peatonales; "
        "por la pendiente fuerte de su cuenca responde con rapidez a los aguaceros y baja con la "
        "misma velocidad en época seca."
    ),
    "fuente": "Supuesto del ejercicio",
}

PUNTOS_CRITICOS = [
    {
        "nombre": "Paso peatonal barrios Los Llanos – Los Dolores",
        "tipo": "Paso",
        "banda": "P75",
        "descripcion_afectacion": (
            "El agua cubre las piedras de paso y el tránsito a pie entre los dos barrios se "
            "vuelve inseguro; los vecinos deben rodear por la vía principal."
        ),
        "fuente": "Supuesto del ejercicio",
    },
    {
        "nombre": "Vía Abejorral – La Ceja, sector puente sobre La Aduanilla",
        "tipo": "Vía",
        "banda": "P90",
        "descripcion_afectacion": (
            "La lámina llega al estribo del puente y se restringe el paso de vehículos pesados; "
            "se recomienda señalización preventiva y monitoreo del punto."
        ),
        "fuente": "Supuesto del ejercicio",
    },
    {
        "nombre": "Vía veredal hacia La Salada, box culvert",
        "tipo": "Box coulvert",
        "banda": "P95",
        "descripcion_afectacion": (
            "El box culvert se anega y la vía queda intransitable; el transporte de leche y "
            "carga veredal debe suspenderse o desviarse por un camino alternativo."
        ),
        "fuente": "Supuesto del ejercicio",
    },
]

USOS_AGUA = [
    {
        "nombre": "Riego de huertas y parcelas campesinas",
        "banda_afectacion": "P25",
        "poblacion_estimada": 150,
        "hectareas": 18,
        "fuente": "Supuesto del ejercicio",
    },
    {
        "nombre": "Abrevadero de ganado lechero",
        "banda_afectacion": "P10",
        "poblacion_estimada": 90,
        "hectareas": 0,
        "fuente": "Supuesto del ejercicio",
    },
    {
        "nombre": "Beneficio de café en pequeñas fincas",
        "banda_afectacion": "P10",
        "poblacion_estimada": 60,
        "hectareas": 25,
        "fuente": "Supuesto del ejercicio",
    },
    {
        "nombre": "Acueductos veredales (captaciones menores)",
        "banda_afectacion": "P05",
        "poblacion_estimada": 420,
        "hectareas": 0,
        "fuente": "Supuesto del ejercicio",
    },
]

AVISO_CONTEXTO = (
    "Las cifras de vías, población y hectáreas son supuestos de referencia del ejercicio, "
    "no provienen de la API de CORNARE. Los niveles, umbrales, fechas y veredas sí son "
    "datos medidos."
)

st.set_page_config(page_title="Nivel de estación — CORNARE", page_icon="🌊", layout="wide")


# --- Paleta: el lenguaje visual sale de la mira limnimétrica y del
# hidrograma (ticks, bandas horizontales, línea de agua bajo lo activo).
LECHO, AGUA, ESPUMA = "#10242E", "#0F6E7D", "#4FB3B8"   # agua honda, primario, acento
NIEBLA, TINTA, TENUE, LINEA = "#F2F5F4", "#1B2B31", "#5F7278", "#D6DEDD"
SEDIMENTO = "#C08B3E"                                    # marca los supuestos del ejercicio
C_NORMAL, C_VIGILANCIA, C_ALERTA, C_EMERGENCIA = "#2F7D4F", "#D9A21B", "#DE7629", "#B3322C"
COLOR_BANDA = {"P25": C_VIGILANCIA, "P10": C_ALERTA, "P05": C_EMERGENCIA,
               "P75": C_VIGILANCIA, "P90": C_ALERTA, "P95": C_EMERGENCIA}


def inyectar_estilos():
    """Todo el CSS de la app. Ojo: va dentro de un f-string, así que las llaves
    de CSS se escriben duplicadas ({{ }}). Y usa st.html, porque st.markdown
    descarta la etiqueta <style>."""
    st.html(f"""<style>
@import url('https://fonts.googleapis.com/css2?family=Archivo:wght@400;500;600;700&display=swap');
:root {{ --lecho:{LECHO}; --agua:{AGUA}; --espuma:{ESPUMA}; --tinta:{TINTA}; --tenue:{TENUE}; --linea:{LINEA}; }}
html, body, .stApp, .stApp p, .stApp li, .stApp label, .stApp div, .stApp span,
.stApp h1, .stApp h2, .stApp h3, .stApp h4 {{ font-family:'Archivo',system-ui,sans-serif; }}
.stApp {{ background:{NIEBLA}; color:{TINTA}; }}
[data-testid="stHeader"] {{ background:transparent; }}
#MainMenu, footer, [data-testid="stAppDeployButton"] {{ display:none; }}
.block-container, [data-testid="stMainBlockContainer"] {{ padding:1.4rem 1rem 4rem; max-width:1280px; }}
.stApp h1 {{ font-weight:700; letter-spacing:-.022em; line-height:1.08; }}
.stApp h2, .stApp h3, .stApp h4 {{ font-weight:600; letter-spacing:-.012em; }}
.stApp p {{ max-width:74ch; line-height:1.6; }}
[data-testid="stMetricValue"], .stApp td, .stApp th {{ font-variant-numeric:tabular-nums; }}

/* Riel de aforo (navegación) */
.riel {{ background:{LECHO}; border-radius:4px; padding:0 4px; margin-bottom:1.5rem; display:flex; flex-wrap:wrap; overflow:hidden; }}
.riel-marca {{ display:flex; flex-direction:column; justify-content:center; padding:.8rem 1.1rem .8rem .9rem; margin-right:.4rem; border-right:1px solid rgba(255,255,255,.13); }}
.riel-marca b {{ color:#fff; font-size:.94rem; font-weight:600; }}
.riel-marca span {{ color:{ESPUMA}; font-size:.72rem; }}
.riel a {{ position:relative; display:flex; align-items:center; padding:1rem .95rem .95rem; text-decoration:none !important; color:rgba(255,255,255,.62) !important; font-size:.87rem; font-weight:500; border-bottom:3px solid transparent; }}
.riel a::before {{ content:""; position:absolute; top:0; left:0; width:1px; height:7px; background:rgba(255,255,255,.22); }}
.riel a:hover {{ color:#fff !important; }}
.riel a:focus-visible {{ outline:2px solid {ESPUMA}; outline-offset:-3px; }}
.riel a.activa {{ color:#fff !important; font-weight:600; border-bottom-color:{ESPUMA}; background:rgba(79,179,184,.10); }}

/* Franja de lectura: la cifra sobre la mira graduada */
.lectura {{ display:flex; flex-wrap:wrap; gap:2.2rem; align-items:flex-end; padding:.2rem 0 1.5rem; border-bottom:1px solid {LINEA}; margin-bottom:1.9rem; }}
.lectura-id {{ flex:1.15 1 340px; min-width:300px; }}
.lectura-id h1 {{ margin:0 0 .35rem; font-size:clamp(1.4rem,2.4vw,1.9rem); text-wrap:balance; }}
.lectura-id .meta {{ color:{TENUE}; font-size:.88rem; line-height:1.5; }}
.lectura-id .meta b {{ color:{TINTA}; font-weight:600; }}
.mira {{ flex:1 1 380px; min-width:300px; }}
.mira-cifra {{ display:flex; align-items:baseline; gap:.45rem; }}
.mira-cifra .valor {{ font-size:2.5rem; font-weight:700; line-height:1; letter-spacing:-.03em; font-variant-numeric:tabular-nums; color:{LECHO}; }}
.mira-cifra .unidad {{ font-size:1rem; font-weight:500; color:{TENUE}; }}
.mira-cifra .etiqueta {{ margin-left:auto; font-size:.78rem; color:{TENUE}; }}
.mira-pista {{ position:relative; height:30px; background:#E4EAE9; border-radius:2px; margin:2.4rem 0 .35rem; }}
.mira-relleno {{ position:absolute; inset:0 auto 0 0; background:linear-gradient(90deg,{AGUA},{ESPUMA}); border-radius:2px 0 0 2px; }}
.mira-tick {{ position:absolute; top:0; bottom:0; width:2px; }}
.mira-tick span {{ position:absolute; top:-17px; left:50%; transform:translateX(-50%); font-size:.66rem; font-weight:600; white-space:nowrap; }}
.mira-tick span.fila2 {{ top:-34px; }}
.mira-aguja {{ position:absolute; top:-5px; bottom:-5px; width:3px; background:{LECHO}; border-radius:2px; }}
.mira-extremos {{ display:flex; justify-content:space-between; font-size:.72rem; color:{TENUE}; font-variant-numeric:tabular-nums; }}

/* Encabezado de sección y marca de procedencia del dato */
.sec {{ border-left:3px solid {AGUA}; padding-left:.85rem; margin:2.1rem 0 .9rem; }}
.sec b {{ display:block; font-size:1.12rem; font-weight:600; }}
.sec span {{ display:block; color:{TENUE}; font-size:.85rem; }}
.proc {{ display:inline-block !important; width:auto !important; font-size:.7rem; font-weight:600; line-height:1.5; padding:1px 8px; border-radius:3px; vertical-align:3px; margin-left:.5rem; }}
.proc-api {{ background:rgba(15,110,125,.11); color:{AGUA}; }}
.proc-sup {{ background:rgba(192,139,62,.15); color:#8A6120; }}

/* Semáforo de bandas */
.banda {{ border:1px solid {LINEA}; border-left:4px solid var(--color); background:#fff; padding:.7rem .9rem; border-radius:3px; height:100%; }}
.banda b {{ display:block; font-size:.82rem; color:{TENUE}; font-weight:600; }}
.banda i {{ display:block; font-style:normal; font-size:1.45rem; font-weight:700; font-variant-numeric:tabular-nums; color:var(--color); }}
.banda small {{ color:{TENUE}; font-size:.72rem; }}

/* Widgets de Streamlit */
[data-testid="stMetric"] {{ background:#fff; border:1px solid {LINEA}; border-radius:3px; padding:.7rem .9rem; }}
[data-testid="stMetricLabel"] p {{ font-size:.79rem !important; color:{TENUE} !important; }}
[data-testid="stMetricValue"] {{ font-size:1.4rem !important; font-weight:700; color:{LECHO}; }}
section[data-testid="stSidebar"] {{ background:#fff; border-right:1px solid {LINEA}; }}
section[data-testid="stSidebar"] h2 {{ font-size:1rem; }}
.stTabs [data-baseweb="tab-list"] {{ gap:.25rem; border-bottom:1px solid {LINEA}; }}
.stTabs [data-baseweb="tab"] {{ height:auto; padding:.5rem .85rem; font-size:.88rem; }}
.stTabs [aria-selected="true"] {{ color:{AGUA} !important; font-weight:600; }}
[data-testid="stExpander"] {{ border:1px solid {LINEA}; border-radius:3px; background:#fff; }}
[data-testid="stAlert"] {{ border-radius:3px; }}
.stButton button, .stDownloadButton button {{ border-radius:3px; }}
.pie {{ margin-top:3rem; padding-top:1rem; border-top:1px solid {LINEA}; color:{TENUE}; font-size:.78rem; }}
@media (prefers-reduced-motion:reduce) {{ * {{ transition:none !important; animation:none !important; }} }}
</style>""")


inyectar_estilos()


# ------------------------------------------------------------------
# Funciones de consulta (todas con caché: la navegación recarga la página)
# ------------------------------------------------------------------
def _get_json(url, params=None, timeout=30):
    try:
        resp = requests.get(url, params=params, headers=HEADERS, timeout=timeout, verify=False)
        if resp.status_code == 200:
            resp.encoding = "utf-8"
            return resp.json(), None
        return None, f"HTTP {resp.status_code}"
    except requests.exceptions.RequestException as e:
        return None, f"Error de red: {e}"


def _valores(datos):
    if isinstance(datos, dict):
        return datos.get("values", [])
    return datos if isinstance(datos, list) else []


@st.cache_data(ttl=600, show_spinner=False)
def obtener_serie_nivel(codigo_estacion, desde, hasta, calidad=1, timeout=30):
    datos, error = _get_json(
        f"{API_ROOT}/estaciones/{codigo_estacion}/nivel",
        params={"desde": desde, "hasta": hasta, "calidad": calidad},
        timeout=timeout,
    )
    return datos, error


@st.cache_data(ttl=600, show_spinner=False)
def obtener_serie_precipitacion(codigo_estacion, desde, hasta, calidad=1, timeout=30):
    datos, error = _get_json(
        f"{API_ROOT}/estaciones/{codigo_estacion}/precipitacion",
        params={"desde": desde, "hasta": hasta, "calidad": calidad},
        timeout=timeout,
    )
    return datos, error


@st.cache_data(ttl=600, show_spinner=False)
def obtener_estacion(codigo_estacion, timeout=30):
    return _get_json(f"{API_ROOT}/estaciones/{codigo_estacion}", timeout=timeout)


@st.cache_data(ttl=600, show_spinner=False)
def obtener_municipios(timeout=30):
    datos, _ = _get_json(f"{API_ROOT}/municipios/", timeout=timeout)
    return _valores(datos)


@st.cache_data(ttl=600, show_spinner=False)
def obtener_regiones(timeout=30):
    datos, _ = _get_json(f"{API_ROOT}/regiones/", timeout=timeout)
    return _valores(datos)


@st.cache_data(ttl=600, show_spinner=False)
def obtener_veredas(timeout=30):
    datos, _ = _get_json(f"{API_ROOT}/veredas/", timeout=timeout)
    return _valores(datos)


@st.cache_data(ttl=600, show_spinner=False)
def obtener_parametros(timeout=30):
    datos, _ = _get_json(f"{API_ROOT}/parametros", timeout=timeout)
    return _valores(datos)


@st.cache_data(ttl=600, show_spinner=False)
def obtener_curvas_calibracion(timeout=30):
    datos, _ = _get_json(f"{API_ROOT}/curvas_calibracion/", timeout=timeout)
    return _valores(datos)


def obtener_todas_las_paginas(datos_json, timeout=30):
    registros = list(datos_json.get("values", []))
    siguiente_url = datos_json.get("next")
    while siguiente_url:
        try:
            resp = requests.get(siguiente_url, timeout=timeout, verify=False)
        except requests.exceptions.RequestException:
            break
        if resp.status_code != 200:
            break
        resp.encoding = "utf-8"
        pagina = resp.json()
        registros.extend(pagina.get("values", []))
        siguiente_url = pagina.get("next")
    return registros


# ------------------------------------------------------------------
# Helpers de análisis
# ------------------------------------------------------------------
def detectar_coordenadas(datos_json):
    """Busca lat/lon en las llaves raíz de la respuesta. Si no las encuentra, usa el valor por defecto."""
    if not isinstance(datos_json, dict):
        return LAT_DEFECTO, LON_DEFECTO, False

    lat = next((datos_json[k] for k in CANDIDATOS_LAT if k in datos_json), None)
    lon = next((datos_json[k] for k in CANDIDATOS_LON if k in datos_json), None)

    if lat is not None and lon is not None:
        try:
            return float(lat), float(lon), True
        except (TypeError, ValueError):
            pass
    return LAT_DEFECTO, LON_DEFECTO, False


def coords_de_estacion(estacion, datos_crudos):
    """Coordenadas reales desde /estaciones/{cod}; si no vienen, intenta la serie y cae al default."""
    if isinstance(estacion, dict):
        lat = estacion.get("latitud")
        lon = estacion.get("longitud")
        if lat is not None and lon is not None:
            try:
                return float(lat), float(lon), True
            except (TypeError, ValueError):
                pass
    return detectar_coordenadas(datos_crudos)


def construir_df(registros):
    df = pd.DataFrame(registros)
    df = df.rename(columns={LLAVE_FECHA: "fecha", LLAVE_VALOR: "nivel"})
    df["fecha"] = pd.to_datetime(df["fecha"], errors="coerce")
    df["nivel"] = pd.to_numeric(df["nivel"], errors="coerce")
    return df.dropna(subset=["fecha", "nivel"]).sort_values("fecha").reset_index(drop=True)


def calcular_indice_calidad(df):
    """Índice simple (0-100) combinando completitud de la serie y proporción de outliers."""
    if df.empty or len(df) < 2:
        return 0.0, 0, 0

    df_idx = df.set_index("fecha")
    frecuencia_tipica = df["fecha"].diff().dropna().mode()
    if len(frecuencia_tipica) == 0:
        return 0.0, 0, 0
    frecuencia_tipica = frecuencia_tipica[0]

    rango_completo = pd.date_range(start=df_idx.index.min(), end=df_idx.index.max(), freq=frecuencia_tipica)
    esperados = len(rango_completo)
    huecos = esperados - len(df_idx)
    completitud = max(0.0, 1 - (huecos / esperados)) if esperados > 0 else 0.0

    Q1, Q3 = df["nivel"].quantile(0.25), df["nivel"].quantile(0.75)
    IQR = Q3 - Q1
    lim_inf, lim_sup = Q1 - 1.5 * IQR, Q3 + 1.5 * IQR
    es_outlier = (df["nivel"] < lim_inf) | (df["nivel"] > lim_sup) | (df["nivel"] < 0)
    proporcion_outliers = es_outlier.mean()

    indice = (completitud * 0.7 + (1 - proporcion_outliers) * 0.3) * 100
    return round(indice, 1), int(huecos), int(es_outlier.sum())


def calcular_bandas(df):
    """Cortes P05..P95 de la propia serie y bandas (semaforización) por arriba y por abajo."""
    quantiles = {"P05": 0.05, "P10": 0.10, "P25": 0.25, "P50": 0.50, "P75": 0.75, "P90": 0.90, "P95": 0.95}
    cortes = {nombre: float(df["nivel"].quantile(q)) for nombre, q in quantiles.items()}
    bandas_altas = [
        ("Normal", None, cortes["P75"], C_NORMAL, "white"),
        ("Vigilancia", cortes["P75"], cortes["P90"], C_VIGILANCIA, "#212529"),
        ("Alerta", cortes["P90"], cortes["P95"], C_ALERTA, "white"),
        ("Emergencia", cortes["P95"], None, C_EMERGENCIA, "white"),
    ]
    bandas_bajas = [
        ("Bajo moderado", "P25"),
        ("Bajo severo", "P10"),
        ("Bajo extremo", "P05"),
    ]
    return {"cortes": cortes, "altas": bandas_altas, "bajas": bandas_bajas}


def detectar_eventos(df, umbral, arriba=True):
    """Agrupa los cruces consecutivos de un umbral en eventos con inicio, fin, duración y valor pico."""
    eventos = []
    grupo = None
    for fecha, nivel in zip(df["fecha"], df["nivel"]):
        activo = nivel >= umbral if arriba else nivel <= umbral
        if activo:
            if grupo is None:
                grupo = {"inicio": fecha, "fin": fecha, "pico": nivel}
            else:
                grupo["fin"] = fecha
                if arriba:
                    grupo["pico"] = max(grupo["pico"], nivel)
                else:
                    grupo["pico"] = min(grupo["pico"], nivel)
        elif grupo is not None:
            eventos.append(grupo)
            grupo = None
    if grupo is not None:
        eventos.append(grupo)
    for evento in eventos:
        evento["duracion"] = evento["fin"] - evento["inicio"]
    return eventos


def distancia_km(lat1, lon1, lat2, lon2):
    """Distancia haversine en km."""
    radio = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * radio * math.asin(math.sqrt(a))


def _fmt_duracion(td):
    minutos = int(td.total_seconds() // 60)
    horas, minutos = minutos // 60, minutos % 60
    return f"{horas} h {minutos:02d} min" if horas else f"{minutos} min"


def _fmt_fecha(fecha):
    return pd.to_datetime(fecha).strftime(FORMATO_FECHA)


def _con_ancho(widget, *args, **kwargs):
    """Llama un widget pidiéndole el ancho completo del contenedor, compatible
    con cualquier versión de Streamlit.

    Si la versión no acepta el parámetro de ancho (TypeError), reintenta sin él
    en lugar de romper la app.
    """
    try:
        parametros = inspect.signature(widget).parameters
        if "width" in parametros:
            kwargs.setdefault("width", "stretch")
        elif "use_container_width" in parametros:
            kwargs.setdefault("use_container_width", True)
        return widget(*args, **kwargs)
    except TypeError:
        kwargs.pop("use_container_width", None)
        kwargs.pop("width", None)
        return widget(*args, **kwargs)


def _badge_fuente(fuente):
    clase = "proc-api" if fuente == "API CORNARE" else "proc-sup"
    return f'<span class="proc {clase}">{fuente}</span>'


def _anillos_multi(geometria):
    """Extrae el anillo exterior de cada miembro de un GeoJSON MultiPolygon."""
    anillos = []
    for miembro in (geometria or {}).get("coordinates", []):
        if miembro and miembro[0]:
            anillos.append(miembro[0])
    return anillos


# ------------------------------------------------------------------
# Navbar Bootstrap 5 (por CDN, aislado en iframe con components.html)
# ------------------------------------------------------------------
def render_navbar(activa, marca, sub=""):
    """Riel de aforo: tick sobre cada enlace y 'línea de agua' bajo el activo."""
    enlaces = "".join(
        f'<a href="?p={c}" target="_self" class="{"activa" if c == activa else ""}">{t}</a>'
        for c, t, _ in SECCIONES
    )
    st.html(f'<nav class="riel"><div class="riel-marca"><b>{marca}</b><span>{sub}</span></div>{enlaces}</nav>')


def franja_lectura(titulo, meta, valor, minimo, maximo, cortes, etiqueta):
    """Hero: la lectura actual situada sobre una mira graduada, con P75/P90/P95
    como ticks. Si dos umbrales quedan a menos de 9 puntos porcentuales, el
    segundo rótulo sube de fila para no encimarse."""
    span = max(maximo - minimo, 1e-9)
    pos = lambda v: min(max((v - minimo) / span * 100, 0), 100)

    ticks, ultima = "", -99.0
    for b in ("P75", "P90", "P95"):
        x, color = pos(cortes[b]), COLOR_BANDA[b]
        fila2 = "fila2" if x - ultima < 9 else ""
        ultima = x
        ticks += (f'<div class="mira-tick" style="left:{x:.1f}%;background:{color}">'
                  f'<span class="{fila2}" style="color:{color}">{b}</span></div>')

    if valor is None:
        cifra, aguja, relleno = '<span class="valor">—</span>', "", ""
    else:
        x = pos(valor)
        cifra = f'<span class="valor">{valor:.1f}</span><span class="unidad">cm</span>'
        aguja = f'<div class="mira-aguja" style="left:calc({x:.1f}% - 1.5px)"></div>'
        relleno = f'<div class="mira-relleno" style="width:{x:.1f}%"></div>'

    st.html(
        f'<div class="lectura"><div class="lectura-id"><h1>{titulo}</h1>'
        f'<div class="meta">{meta}</div></div><div class="mira">'
        f'<div class="mira-cifra">{cifra}<span class="etiqueta">{etiqueta}</span></div>'
        f'<div class="mira-pista">{relleno}{ticks}{aguja}</div>'
        f'<div class="mira-extremos"><span>mín {minimo:.1f} cm</span>'
        f'<span>máx {maximo:.1f} cm</span></div></div></div>'
    )


def seccion(titulo, descripcion="", fuente=None):
    """Encabezado de sección: regla vertical, subtítulo y de dónde sale el dato."""
    marca = _badge_fuente(fuente) if fuente else ""
    sub = f"<span>{descripcion}</span>" if descripcion else ""
    st.html(f'<div class="sec"><b>{titulo}{marca}</b>{sub}</div>')


def _marca_navbar(estacion, codigo_estacion):
    if isinstance(estacion, dict) and estacion.get("corriente"):
        corriente = str(estacion["corriente"]).replace("Quebrada ", "Q. ").replace("Río ", "R. ")
        return f"{corriente} · Est. {codigo_estacion}"
    return f"Estación {codigo_estacion}"


# ------------------------------------------------------------------
# Sidebar — parámetros de la consulta (persistidos en la URL)
# ------------------------------------------------------------------
def _leer_fecha_qp(clave, defecto):
    bruta = st.query_params.get(clave)
    if bruta:
        try:
            return pd.to_datetime(bruta).date()
        except (ValueError, TypeError):
            pass
    return defecto


def _guardar_parametros():
    st.query_params.update(
        {
            "nombre": st.session_state.get("in_nombre", NOMBRE_DEFECTO),
            "estacion": st.session_state.get("in_estacion", ESTACION_DEFECTO),
            "desde": str(st.session_state.get("in_desde", DESDE_DEFECTO)),
            "hasta": str(st.session_state.get("in_hasta", HASTA_DEFECTO)),
        }
    )


with st.sidebar:
    st.header("Parámetros de tu consulta")
    nombre_estudiante = st.text_input(
        "Nombre del estudiante",
        value=st.query_params.get("nombre") or NOMBRE_DEFECTO,
        key="in_nombre",
        on_change=_guardar_parametros,
    )
    codigo_estacion = st.text_input(
        "Código de estación",
        value=st.query_params.get("estacion") or ESTACION_DEFECTO,
        key="in_estacion",
        on_change=_guardar_parametros,
    )
    fecha_desde = (
        st.date_input(
            "Desde",
            value=_leer_fecha_qp("desde", DESDE_DEFECTO),
            key="in_desde",
            on_change=_guardar_parametros,
        )
        .strftime("%Y-%m-%d")
    )
    fecha_hasta = (
        st.date_input(
            "Hasta",
            value=_leer_fecha_qp("hasta", HASTA_DEFECTO),
            key="in_hasta",
            on_change=_guardar_parametros,
        )
        .strftime("%Y-%m-%d")
    )
    calidad = 1  # solo datos validados: con calidad 0 la API falla
    if _con_ancho(st.button, "🔄 Refrescar datos"):
        st.cache_data.clear()
        st.rerun()


# ------------------------------------------------------------------
# Carga de datos (automática y cacheada)
# ------------------------------------------------------------------
pagina = st.query_params.get("p") or "inicio"
if pagina not in {clave for clave, _, _ in SECCIONES}:
    pagina = "inicio"

estacion, _error_estacion = obtener_estacion(codigo_estacion)
estacion = estacion if isinstance(estacion, dict) else {}

_subtitulo_seccion = next((d for c, _, d in SECCIONES if c == pagina), "")
render_navbar(pagina, _marca_navbar(estacion, codigo_estacion), _subtitulo_seccion)

with st.spinner("Consultando la API..."):
    datos_crudos, error = obtener_serie_nivel(codigo_estacion, fecha_desde, fecha_hasta, calidad)

if error:
    st.subheader("No se pudo leer la estación")
    st.error(f"La API respondió: {error}. Revisa el código de estación en el panel lateral o vuelve a intentarlo.")
    st.stop()

registros = obtener_todas_las_paginas(datos_crudos)
df = construir_df(registros)

if df.empty:
    st.subheader("Sin registros en esta ventana")
    st.warning(
        f"La estación {codigo_estacion} no reportó nivel entre {fecha_desde} y {fecha_hasta}. "
        "Amplía el rango de fechas o prueba otro código en el panel lateral."
    )
    st.stop()

bandas = calcular_bandas(df)
indice_calidad, huecos, n_outliers = calcular_indice_calidad(df)
lat_est, lon_est, coords_reales = coords_de_estacion(estacion, datos_crudos)

# --- Hero: la lectura sobre la mira -------------------------------
_nivel_actual = datos_crudos.get("current_level")
try:
    _nivel_actual = float(_nivel_actual) if _nivel_actual is not None else None
except (TypeError, ValueError):
    _nivel_actual = None

if _nivel_actual is not None and datos_crudos.get("current_level_date"):
    _etiqueta = f"Última lectura · {_fmt_fecha(datos_crudos['current_level_date'])}"
elif _nivel_actual is not None:
    _etiqueta = "Última lectura"
else:
    _nivel_actual = float(df["nivel"].iloc[-1])
    _etiqueta = f"Último dato de la ventana · {_fmt_fecha(df['fecha'].iloc[-1])}"

franja_lectura(
    titulo=estacion.get("corriente") or f"Estación {codigo_estacion}",
    meta=(
        f"{estacion.get('ubicacion_campo') or 'Ubicación no reportada'}<br>"
        f"Estación <b>{codigo_estacion}</b> · {fecha_desde} a {fecha_hasta} · "
        f"<b>{len(df):,}</b> lecturas · {nombre_estudiante}"
    ).replace(",", "."),
    valor=_nivel_actual,
    minimo=float(df["nivel"].min()),
    maximo=float(df["nivel"].max()),
    cortes=bandas["cortes"],
    etiqueta=_etiqueta,
)


# ------------------------------------------------------------------
# Sección 1 · Inicio
# ------------------------------------------------------------------
def render_inicio():
    sensores = estacion.get("sensores", {})
    sensor_nivel = sensores.get("nivel", {})
    fotos = estacion.get("fotos", [])
    portada = next((f for f in fotos if f.get("is_portada")), fotos[0] if fotos else None)

    seccion("La estación en campo", "Cómo se ve el sitio de medición y qué reporta su sensor.", "API CORNARE")
    col_foto, col_ficha = st.columns([2, 3])
    with col_foto:
        if portada:
            _con_ancho(st.image, portada["foto"], caption="Fotografía: CORNARE")
        else:
            st.info("La API no registra fotos de esta estación.")
    with col_ficha:
        if not estacion:
            st.caption("No se cargaron los metadatos de la estación; se muestran los valores por defecto.")
        st.write(f"**Etiqueta:** {estacion.get('label') or '—'}")
        st.write(f"**Ubicación en campo:** {estacion.get('ubicacion_campo') or '—'}")
        st.write(f"**Red:** {estacion.get('red') or '—'}")
        categoria = sensor_nivel.get("categoria")
        if categoria:
            color = sensor_nivel.get("color", TENUE)
            st.markdown(
                f'**Estado del sensor de nivel:** <span style="color:{color};font-weight:600">● {categoria}</span>',
                unsafe_allow_html=True,
            )

    seccion("Resumen de la ventana", "Cuánto midió la estación y qué tan confiable es esa medición.")
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Lecturas", f"{len(df)}")
    col2.metric("Nivel promedio", f"{df['nivel'].mean():.2f} cm")
    col3.metric("Índice de calidad", f"{indice_calidad} / 100")
    col4.metric("Outliers detectados", f"{n_outliers}")

    tab_serie, tab_mapa, tab_datos = st.tabs(["Serie de nivel", "Ubicación", "Datos y calidad"])

    with tab_serie:
        st.caption("Nivel en centímetros a lo largo de la ventana consultada.")
        st.line_chart(df.set_index("fecha")["nivel"], color=AGUA)

    with tab_mapa:
        if not coords_reales:
            st.caption(
                "Ni los metadatos ni la serie trajeron latitud/longitud, así que el mapa muestra el "
                "punto de partida por defecto."
            )
        st.map(pd.DataFrame({"lat": [lat_est], "lon": [lon_est]}), zoom=10)

    with tab_datos:
        st.markdown(
            f"El índice de calidad combina la **completitud** de la serie (70 %) y la proporción de "
            f"lecturas **sin outliers** (30 %). En esta ventana se detectaron **{huecos}** huecos de "
            f"reporte y **{n_outliers}** outliers sobre {len(df)} lecturas, por criterio de rango "
            f"intercuartílico y niveles negativos."
        )
        _con_ancho(st.dataframe, df, hide_index=True)
        csv = df.to_csv(index=False).encode("utf-8")
        st.download_button(
            "Descargar la serie en CSV",
            csv,
            file_name=f"nivel_estacion_{codigo_estacion}_{fecha_desde}_{fecha_hasta}.csv",
            mime="text/csv",
        )


# ------------------------------------------------------------------
# Sección 2 · Niveles (cuadro comparativo)
# ------------------------------------------------------------------
def render_niveles():
    seccion("Cuadro comparativo de niveles",
            "Toda la estadística sale de la ventana consultada, no de un histórico externo.",
            "API CORNARE")
    niveles = df["nivel"]
    minimo, maximo = niveles.min(), niveles.max()
    media, mediana = niveles.mean(), niveles.median()
    desv = niveles.std()
    cortes = bandas["cortes"]

    m1, m2, m3, m4, m5, m6 = st.columns(6)
    m1.metric("Mínimo", f"{minimo:.2f} cm")
    m2.metric("Mediana", f"{mediana:.2f} cm")
    m3.metric("Media", f"{media:.2f} cm")
    m4.metric("Máximo", f"{maximo:.2f} cm")
    m5.metric("Rango", f"{maximo - minimo:.2f} cm")
    m6.metric("Desv. estándar", f"{desv:.2f} cm")

    filas = [
        ("Mínimo", minimo),
        ("P5", cortes["P05"]),
        ("P10", cortes["P10"]),
        ("P25", cortes["P25"]),
        ("Mediana", mediana),
        ("Media", media),
        ("P75", cortes["P75"]),
        ("P90", cortes["P90"]),
        ("P95", cortes["P95"]),
        ("Máximo", maximo),
    ]
    tabla = pd.DataFrame(filas, columns=["Nivel", "Nivel (cm)"])
    tabla["Diferencia vs. media (cm)"] = tabla["Nivel (cm)"] - media
    tabla["% de la media"] = tabla["Nivel (cm)"] / media * 100
    tabla["% del máximo"] = tabla["Nivel (cm)"] / maximo * 100
    tabla["Lecturas ≤ ese nivel"] = [int((niveles <= valor).sum()) for valor in tabla["Nivel (cm)"]]

    seccion("Escalafón de niveles", "Cada percentil comparado contra la media de la ventana.")

    def _resaltar_media(fila):
        es_media = fila["Nivel"] == "Media"
        estilo = f"background-color:{AGUA};color:white;font-weight:600" if es_media else ""
        return [estilo] * len(fila)

    styler = tabla.style.apply(_resaltar_media, axis=1).format(
        {
            "Nivel (cm)": "{:.2f}",
            "Diferencia vs. media (cm)": "{:+.2f}",
            "% de la media": "{:.1f}",
            "% del máximo": "{:.1f}",
        }
    )
    _con_ancho(st.dataframe, styler, hide_index=True)

    orden = tabla.sort_values("Nivel (cm)")["Nivel"].tolist()
    barras = (
        alt.Chart(tabla)
        .mark_bar()
        .encode(
            x=alt.X("Nivel (cm):Q", title="Nivel (cm)"),
            y=alt.Y("Nivel:N", sort=orden, title=None),
            color=alt.condition(alt.datum.Nivel == "Media", alt.value(AGUA), alt.value("#A8B7B8")),
            tooltip=["Nivel:N", alt.Tooltip("Nivel (cm):Q", format=".2f")],
        )
    )
    linea_media = (
        alt.Chart(pd.DataFrame({"media": [media]}))
        .mark_rule(color=C_EMERGENCIA, strokeDash=[4, 3])
        .encode(x=alt.X("media:Q", title=None))
    )
    _con_ancho(st.altair_chart, barras + linea_media)

    paso = max((maximo - minimo) / 20, 0.05)
    histograma = (
        alt.Chart(df)
        .mark_bar(color=AGUA)
        .encode(
            x=alt.X("nivel:Q", bin=alt.Bin(step=paso), title="Nivel (cm)"),
            y=alt.Y("count()", title="Lecturas"),
        )
    )
    seccion("Distribución de las lecturas", "Cuántas veces la quebrada estuvo en cada franja de nivel.")
    _con_ancho(st.altair_chart, histograma)

    seccion("Comparativo por día", "Ordenado de menor a mayor nivel medio diario.")
    por_dia = (
        df.assign(dia=df["fecha"].dt.date)
        .groupby("dia")["nivel"]
        .agg(minimo="min", media="mean", maximo="max", desv="std")
        .reset_index()
    )
    por_dia["rango"] = por_dia["maximo"] - por_dia["minimo"]
    por_dia["dif_vs_media_general"] = por_dia["media"] - media
    por_dia = por_dia.sort_values("media").reset_index(drop=True)
    por_dia = por_dia.rename(
        columns={
            "dia": "Día",
            "minimo": "Mín (cm)",
            "media": "Media (cm)",
            "maximo": "Máx (cm)",
            "desv": "Desv. (cm)",
            "rango": "Rango (cm)",
            "dif_vs_media_general": "Δ vs. media general (cm)",
        }
    )
    _con_ancho(
        st.dataframe,
        por_dia,
        hide_index=True,
        column_config={
            col: st.column_config.NumberColumn(format="%.2f")
            for col in ("Mín (cm)", "Media (cm)", "Máx (cm)", "Desv. (cm)", "Rango (cm)", "Δ vs. media general (cm)")
        },
    )

    bajas = df.nsmallest(10, "nivel")[["fecha", "nivel"]].copy()
    altas = df.nlargest(10, "nivel")[["fecha", "nivel"]].copy()
    for tabla_top in (bajas, altas):
        tabla_top["fecha"] = tabla_top["fecha"].dt.strftime(FORMATO_FECHA)
    col_bajas, col_altas = st.columns(2)
    with col_bajas:
        st.markdown("**Diez lecturas más bajas**")
        st.dataframe(bajas.rename(columns={"fecha": "Fecha y hora", "nivel": "Nivel (cm)"}), hide_index=True)
    with col_altas:
        st.markdown("**Diez lecturas más altas**")
        st.dataframe(altas.rename(columns={"fecha": "Fecha y hora", "nivel": "Nivel (cm)"}), hide_index=True)

    dia_alto = por_dia.sort_values("Media (cm)", ascending=False).iloc[0]
    dia_bajo = por_dia.iloc[0]
    aparte_max = abs(maximo - media)
    sobre_media = (maximo / media - 1) * 100 if media else 0.0
    bajo_media = (1 - minimo / media) * 100 if media else 0.0
    if media > mediana:
        forma = "la media supera a la mediana: la cola derecha (picos de creciente) tira del promedio hacia arriba"
    elif media < mediana:
        forma = "la media queda bajo la mediana: la cola izquierda (valles de estiaje) tira del promedio hacia abajo"
    else:
        forma = "media y mediana coinciden: la distribución es prácticamente simétrica"
    seccion("Conclusión")
    st.markdown(
        f"En la ventana consultada el nivel osciló entre **{minimo:.2f} cm** y **{maximo:.2f} cm**, "
        f"con una media de **{media:.2f} cm**. El máximo se aparta **{aparte_max:.2f} cm** "
        f"({sobre_media:.0f} %) por encima de la media, mientras que el mínimo queda **{bajo_media:.0f} %** por "
        f"debajo de ella. En cuanto a la forma, {forma}. El día con nivel medio más alto fue el "
        f"**{dia_alto['Día']}** ({dia_alto['Media (cm)']:.2f} cm de media) y el de más bajo el "
        f"**{dia_bajo['Día']}** ({dia_bajo['Media (cm)']:.2f} cm de media)."
    )


# ------------------------------------------------------------------
# Sección 3 · Municipio
# ------------------------------------------------------------------
def render_municipio():
    municipios = obtener_municipios()
    regiones = obtener_regiones()
    veredas_todas = obtener_veredas()

    mun_id = estacion.get("municipio")
    municipio = next((m for m in municipios if m.get("id") == mun_id), None)
    if not municipio:
        st.warning("La API no devolvió el municipio de esta estación.")
        return
    region = next((r for r in regiones if r.get("id") == municipio.get("region")), None)
    veredas_abe = sorted(
        (v for v in veredas_todas if v.get("municipio") == mun_id),
        key=lambda v: distancia_km(lat_est, lon_est, v.get("latitud", 0), v.get("longitud", 0)),
    )

    seccion("Perfil del municipio", "Contexto territorial de la cuenca que drena hacia la estación.", PERFIL_MUNICIPIO["fuente"])
    st.write(PERFIL_MUNICIPIO["texto"])

    m1, m2, m3, m4, m5, m6 = st.columns(6)
    m1.metric("Código DANE", municipio.get("cod_dane", "—"))
    m2.metric("Área", f"{municipio.get('area', 0):.2f} km²")
    m3.metric("Perímetro", f"{municipio.get('perimetro', 0):.2f} km")
    m4.metric("Región", (region or {}).get("nombre", "—"))
    m5.metric("Veredas", len(veredas_abe))
    centro = f"{municipio.get('latitud', 0):.4f}, {municipio.get('longitud', 0):.4f}"
    m6.metric("Centroide", centro)

    seccion("Mapa del municipio y sus veredas", "Amarillo: límite municipal. Blanco: veredas. Punto rojo: la estación.", "API CORNARE")
    filas_mun = [{"poligono": anillo, "nombre": municipio.get("nombre", "")} for anillo in _anillos_multi(municipio.get("poligono"))]
    filas_veredas = [
        {"poligono": anillo, "nombre": v.get("nombre", "")}
        for v in veredas_abe
        for anillo in _anillos_multi(v.get("poligono"))
    ]
    vista = pdk.ViewState(latitude=lat_est, longitude=lon_est, zoom=10.5, pitch=0)
    capa_municipio = pdk.Layer(
        "PolygonLayer",
        data=pd.DataFrame(filas_mun),
        get_polygon="poligono",
        get_fill_color=[255, 188, 57, 45],
        get_line_color=[230, 145, 20],
        line_width_min_pixels=2,
        pickable=True,
    )
    capa_veredas = pdk.Layer(
        "PolygonLayer",
        data=pd.DataFrame(filas_veredas),
        get_polygon="poligono",
        filled=False,
        stroked=True,
        get_line_color=[255, 255, 255, 110],
        line_width_min_pixels=1,
        pickable=True,
    )
    capa_estacion = pdk.Layer(
        "ScatterplotLayer",
        data=pd.DataFrame({"posicion": [[lon_est, lat_est]]}),
        get_position="posicion",
        get_radius=180,
        get_fill_color=[179, 50, 44, 240],
        radius_min_pixels=6,
    )
    _con_ancho(
        st.pydeck_chart,
        pdk.Deck(
            layers=[capa_municipio, capa_veredas, capa_estacion],
            initial_view_state=vista,
            tooltip={"text": "{nombre}"},
        ),
    )
    st.caption("Amarillo: límites del municipio · blanco: veredas · punto rojo: estación 41.")

    seccion("Veredas del municipio", "Área, perímetro y distancia de cada vereda a la estación.", "API CORNARE")
    tabla_veredas = pd.DataFrame(
        [
            {
                "Vereda": v.get("nombre"),
                "Área (km²)": round(float(v.get("area", 0)), 3),
                "Perímetro (km)": round(float(v.get("perimetro", 0)), 2),
                "Distancia a la estación (km)": round(distancia_km(lat_est, lon_est, v.get("latitud", 0), v.get("longitud", 0)), 2),
            }
            for v in veredas_abe
        ]
    )
    _con_ancho(
        st.dataframe,
        tabla_veredas,
        hide_index=True,
        column_config={
            "Área (km²)": st.column_config.NumberColumn(format="%.3f"),
            "Perímetro (km)": st.column_config.NumberColumn(format="%.2f"),
            "Distancia a la estación (km)": st.column_config.NumberColumn(format="%.2f"),
        },
    )

    sensores = estacion.get("sensores", {})
    seccion("La estación dentro del municipio", "Metadatos y sensores instalados en el sitio de aforo.", "API CORNARE")
    c1, c2, c3 = st.columns(3)
    with c1:
        st.write(f"**Corriente:** {estacion.get('corriente') or '—'}")
        st.write(f"**Ubicación en campo:** {estacion.get('ubicacion_campo') or '—'}")
        st.write(f"**Red:** {estacion.get('red') or '—'}")
    with c2:
        st.write(f"**Coordenadas:** {lat_est:.4f}, {lon_est:.4f}")
        st.write(f"**Fotos registradas:** {len(estacion.get('fotos', []))}")
        st.write(f"**Código de estación:** {estacion.get('codigo') or codigo_estacion}")
    with c3:
        st.write("**Sensores instalados:**")
        for nombre, sensor in sensores.items():
            categoria = sensor.get("categoria")
            valor = sensor.get("valor")
            st.write(f"- `{nombre}` · valor {valor} · estado **{categoria}**")

    sensor_nivel_id = (sensores.get("nivel") or {}).get("id")
    curva = next((c for c in obtener_curvas_calibracion() if c.get("sensor") == sensor_nivel_id), None)
    if curva:
        offset = float(curva.get("offset", 0))
        seccion("Detalle técnico del sensor de nivel", "Cómo se convierte la lectura cruda en centímetros.", "API CORNARE")
        st.write(
            f"Según `/parametros`, el parámetro **nivel** se mide en **centímetros**. El sensor "
            f"**#{sensor_nivel_id}** usa una curva de calibración con **offset {offset:.0f}**, de modo que la "
            f"lectura cruda (`muestra`) se convierte así:"
        )
        st.code(f"nivel (cm) = {offset:.0f} − muestra")
        if "muestra" in df.columns:
            demo = df[["fecha", "muestra", "nivel"]].tail(5).copy()
            demo["fecha"] = demo["fecha"].dt.strftime(FORMATO_FECHA)
            st.dataframe(demo, hide_index=True)
            st.caption("La columna `nivel` viene de la API y corresponde a offset − muestra; `muestra` es la lectura cruda del sensor.")


# ------------------------------------------------------------------
# Sección 4 · Crecientes
# ------------------------------------------------------------------
def render_crecientes():
    st.info(AVISO_CONTEXTO, icon="ℹ️")
    niveles = df["nivel"]
    cortes = bandas["cortes"]
    p75, p90, p95 = cortes["P75"], cortes["P90"], cortes["P95"]

    pct_normal = float((niveles <= p75).mean() * 100)
    pct_vig = float(((niveles > p75) & (niveles <= p90)).mean() * 100)
    pct_alerta = float(((niveles > p90) & (niveles <= p95)).mean() * 100)
    pct_emerg = float((niveles > p95).mean() * 100)

    seccion("Semáforo de bandas", "Umbrales definidos por la estadística de la ventana: P75, P90 y P95.")
    chips = [
        ("Normal", pct_normal, "#198754", "white"),
        ("Vigilancia", pct_vig, "#ffc107", "#212529"),
        ("Alerta", pct_alerta, "#fd7e14", "white"),
        ("Emergencia", pct_emerg, "#dc3545", "white"),
    ]
    cols = st.columns(4)
    for col, (nombre, pct, fondo, _texto_color) in zip(cols, chips):
        col.html(
            f'<div class="banda" style="--color:{fondo}"><b>{nombre}</b>'
            f'<i>{pct:.1f} %</i><small>del tiempo en esta banda</small></div>'
        )
    _con_ancho(
        st.dataframe,
        pd.DataFrame(
            [
                {"Banda": nombre, "Desde (cm)": "—" if inferior is None else f"{inferior:.2f}",
                 "Hasta (cm)": "—" if superior is None else f"{superior:.2f}", "% del tiempo": f"{pct:.1f}"}
                for (nombre, inferior, superior, _, _), (_, pct, _, _) in zip(bandas["altas"], chips)
            ]
        ),
        hide_index=True,
    )

    seccion("Vías y pasos afectados", "Cada punto se activa cuando el nivel cruza el umbral de su banda.", "Supuesto del ejercicio")
    filas_puntos = []
    for punto in PUNTOS_CRITICOS:
        corte = cortes[punto["banda"]]
        eventos_punto = detectar_eventos(df, corte)
        superado = int((niveles > corte).sum())
        duracion_total = sum((e["duracion"] for e in eventos_punto), pd.Timedelta(0))
        peor = max(eventos_punto, key=lambda e: e["pico"]) if eventos_punto else None
        filas_puntos.append(
            {
                "Punto": punto["nombre"],
                "Tipo": punto["tipo"],
                "Banda": punto["banda"],
                "Corte (cm)": round(corte, 2),
                "Veces superado": superado,
                "Duración total": _fmt_duracion(duracion_total) if superado else "—",
                "Peor evento": f"{_fmt_fecha(peor['inicio'])} → {_fmt_fecha(peor['fin'])}" if peor else "—",
                "Pico (cm)": round(peor["pico"], 2) if peor else "—",
            }
        )
    _con_ancho(
        st.dataframe,
        pd.DataFrame(filas_puntos),
        hide_index=True,
        column_config={
            "Corte (cm)": st.column_config.NumberColumn(format="%.2f"),
            "Pico (cm)": st.column_config.NumberColumn(format="%.2f"),
        },
    )

    seccion("Afectaciones por banda", "Qué ocurre en cada punto cuando se cruza su umbral.", "Supuesto del ejercicio")
    for banda_nombre in ("P75", "P90", "P95"):
        puntos_banda = [p for p in PUNTOS_CRITICOS if p["banda"] == banda_nombre]
        if puntos_banda:
            st.markdown(f"**Corte {banda_nombre} · {cortes[banda_nombre]:.2f} cm**")
            for p in puntos_banda:
                st.write(f"- **{p['nombre']}** ({p['tipo']}): {p['descripcion_afectacion']}")

    seccion("Eventos de creciente detectados", "Cruces consecutivos del umbral de vigilancia (P75), agrupados en episodios.")
    eventos = detectar_eventos(df, p75)
    if eventos:
        tabla_eventos = pd.DataFrame(
            [
                {
                    "Inicio": _fmt_fecha(e["inicio"]),
                    "Fin": _fmt_fecha(e["fin"]),
                    "Duración": _fmt_duracion(e["duracion"]),
                    "Nivel pico (cm)": round(e["pico"], 2),
                }
                for e in eventos
            ]
        )
        _con_ancho(
            st.dataframe,
            tabla_eventos,
            hide_index=True,
            column_config={"Nivel pico (cm)": st.column_config.NumberColumn(format="%.2f")},
        )
    else:
        st.success("La quebrada no superó el umbral de vigilancia en la ventana consultada.")

    seccion("Lluvia frente a nivel", "El pico de lluvia antecede al de nivel: es el tiempo de concentración de la cuenca.", "API CORNARE")
    with st.spinner("Consultando la precipitación..."):
        datos_pp, error_pp = obtener_serie_precipitacion(codigo_estacion, fecha_desde, fecha_hasta, calidad)
    if error_pp:
        st.warning(f"No se pudo consultar la precipitación: {error_pp}")
    else:
        registros_pp = obtener_todas_las_paginas(datos_pp)
        if not registros_pp:
            st.info("La API no devolvió registros de precipitación para este rango.")
        else:
            df_pp = pd.DataFrame(registros_pp)
            df_pp["fecha"] = pd.to_datetime(df_pp["fecha"], errors="coerce")
            df_pp["intensidad"] = pd.to_numeric(df_pp["intensidad"], errors="coerce")
            df_pp["acumulado"] = pd.to_numeric(df_pp["acumulado"], errors="coerce")
            df_pp = df_pp.dropna(subset=["fecha"]).sort_values("fecha")

            df_n5 = df.set_index("fecha").resample("5min")["nivel"].mean().dropna().reset_index()
            linea_nivel = (
                alt.Chart(df_n5)
                .mark_line(color=AGUA)
                .encode(
                    x=alt.X("fecha:T", title=None),
                    y=alt.Y("nivel:Q", axis=alt.Axis(title="Nivel (cm)", titleColor=AGUA)),
                )
            )
            area_lluvia = (
                alt.Chart(df_pp)
                .mark_area(color="#7E6BB0", opacity=0.30)
                .encode(
                    x=alt.X("fecha:T", title=None),
                    y=alt.Y("intensidad:Q", axis=alt.Axis(title="Intensidad de lluvia (mm/h)", titleColor="#7E6BB0")),
                )
            )
            _con_ancho(
                st.altair_chart,
                alt.layer(area_lluvia, linea_nivel).resolve_scale(y="independent"),
            )
            st.caption(
                "Línea azul: nivel de la quebrada (cm, promediado a intervalos de 5 min). Área morada: "
                "intensidad de lluvia (mm/h). El pico de lluvia precede al pico de nivel por el tiempo "
                "que tarda la escorrentía en concentrarse."
            )
            with st.expander("Lluvia acumulada (mm) en la ventana"):
                st.line_chart(df_pp.set_index("fecha")["acumulado"])


# ------------------------------------------------------------------
# Sección 5 · Niveles bajos (estiaje)
# ------------------------------------------------------------------
def render_estiaje():
    st.info(AVISO_CONTEXTO, icon="ℹ️")
    niveles = df["nivel"]
    cortes = bandas["cortes"]

    seccion("Umbrales bajos", "Umbrales definidos por la estadística de la ventana: P25, P10 y P05.")
    filas_umbrales = []
    for nombre_bajo, clave in bandas["bajas"]:
        corte = cortes[clave]
        pct_debajo = float((niveles < corte).mean() * 100)
        filas_umbrales.append(
            {
                "Umbral": nombre_bajo,
                "Corte (cm)": round(corte, 2),
                "% del tiempo debajo": f"{pct_debajo:.1f}",
            }
        )
    _con_ancho(
        st.dataframe,
        pd.DataFrame(filas_umbrales),
        hide_index=True,
        column_config={"Corte (cm)": st.column_config.NumberColumn(format="%.2f")},
    )

    eventos_bajos = detectar_eventos(df, cortes["P25"], arriba=False)
    col_racha, col_dia = st.columns(2)
    if eventos_bajos:
        racha = max(eventos_bajos, key=lambda e: e["duracion"])
        col_racha.metric(
            "Racha más larga bajo P25",
            _fmt_duracion(racha["duracion"]),
            help=f"Del {_fmt_fecha(racha['inicio'])} al {_fmt_fecha(racha['fin'])} · mínimo {racha['pico']:.2f} cm",
        )
    else:
        col_racha.info("En toda la ventana el nivel se mantuvo sobre P25.")
    por_dia = df.groupby(df["fecha"].dt.date)["nivel"].mean()
    dia_bajo = por_dia.idxmin()
    col_dia.metric("Día de menor nivel medio", str(dia_bajo), f"{por_dia.min():.2f} cm")

    seccion("Veredas del entorno", "Ordenadas por distancia haversine desde la estación.", "API CORNARE")
    mun_id = estacion.get("municipio")
    veredas_todas = obtener_veredas()
    veredas_abe = sorted(
        (v for v in veredas_todas if v.get("municipio") == mun_id),
        key=lambda v: distancia_km(lat_est, lon_est, v.get("latitud", 0), v.get("longitud", 0)),
    )
    if not veredas_abe:
        st.info("La API no devolvió veredas para el municipio de esta estación.")
    else:
        st.caption(
            "Las veredas se ordenan por distancia haversine desde la estación; las más cercanas son el "
            "proxy de qué comunidades dependen de este tramo de la quebrada."
        )
        cercanas = veredas_abe[:10]
        tabla_cercanas = pd.DataFrame(
            [
                {
                    "Vereda": v.get("nombre"),
                    "Área (km²)": round(float(v.get("area", 0)), 3),
                    "Distancia (km)": round(distancia_km(lat_est, lon_est, v.get("latitud", 0), v.get("longitud", 0)), 2),
                }
                for v in cercanas
            ]
        )
        _con_ancho(
            st.dataframe,
            tabla_cercanas,
            hide_index=True,
            column_config={
                "Área (km²)": st.column_config.NumberColumn(format="%.3f"),
                "Distancia (km)": st.column_config.NumberColumn(format="%.2f"),
            },
        )

    seccion("Usos del agua que se comprometen", "Actividades que dependen de este tramo y el umbral en que empiezan a sufrir.", "Supuesto del ejercicio")
    filas_usos = []
    for uso in USOS_AGUA:
        corte = cortes[uso["banda_afectacion"]]
        pct_bajo_corte = float((niveles < corte).mean() * 100)
        filas_usos.append(
            {
                "Uso": uso["nombre"],
                "Se compromete bajo": uso["banda_afectacion"],
                "Corte (cm)": round(corte, 2),
                "Tiempo bajo el corte": f"{pct_bajo_corte:.1f} %",
                "Población estimada": uso["poblacion_estimada"],
                "Hectáreas": uso["hectareas"],
                "Fuente": uso["fuente"],
            }
        )
    _con_ancho(
        st.dataframe,
        pd.DataFrame(filas_usos),
        hide_index=True,
        column_config={
            "Corte (cm)": st.column_config.NumberColumn(format="%.2f"),
            "Población estimada": st.column_config.NumberColumn(format="%d personas"),
            "Hectáreas": st.column_config.NumberColumn(format="%d ha"),
        },
    )

    st.info(
        "Nota: el agua de la quebrada no es potable, pero sí sostiene actividades agrícolas y pecuarias. "
        "Cuando el nivel cae bajo los umbrales anteriores, la escasez pega primero en el riego y el "
        "abrevadero del ganado."
    )


# ------------------------------------------------------------------
# Despacho de secciones
# ------------------------------------------------------------------
if pagina == "niveles":
    render_niveles()
elif pagina == "municipio":
    render_municipio()
elif pagina == "crecientes":
    render_crecientes()
elif pagina == "estiaje":
    render_estiaje()
else:
    render_inicio()

st.html(
    f'<div class="pie">Datos de nivel, precipitación, veredas y municipios: API MARCO de CORNARE. '
    f'Los umbrales se calculan sobre la ventana consultada. Estación {codigo_estacion} · '
    f'{fecha_desde} a {fecha_hasta}.</div>'
)
