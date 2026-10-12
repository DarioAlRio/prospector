#!/usr/bin/env python3
"""
Tests de prospector.py y marcar_contactado.py.

No tocan la red ni gastan API: `requests` se sustituye por un doble que
devuelve respuestas de Google Places inventadas. Tampoco tocan tus
leads.csv / leads_db.json reales (usan un directorio temporal).

Uso:
    python3 test_prospector.py
"""
import csv
import json
import os
import shutil
import subprocess
import sys
import tempfile
import types
from contextlib import redirect_stdout
from pathlib import Path

BASE_DIR = Path(__file__).parent

# --- doble de `requests` (debe instalarse antes de importar prospector) ----

# Respuestas tal y como las devuelve Places API (New).
PLACES = [
    {"id": "p1", "displayName": {"text": "Peluqueria Ana", "languageCode": "es"},
     "formattedAddress": "C/ Falsa 1", "internationalPhoneNumber": "+34 612 345 678",
     "googleMapsUri": "https://maps.google.com/?cid=17395", "types": ["hair_care", "point_of_interest"],
     "rating": 4.7, "userRatingCount": 88, "businessStatus": "OPERATIONAL"},
    {"id": "p2", "displayName": {"text": "Barberia Con Web", "languageCode": "es"},
     "formattedAddress": "C/ Falsa 2", "internationalPhoneNumber": "+34 612 000 002",
     "websiteUri": "https://barberia.example", "types": ["hair_care"],
     "businessStatus": "OPERATIONAL"},
    {"id": "p3", "displayName": {"text": "Estetica Sin Tel", "languageCode": "es"},
     "formattedAddress": "C/ Falsa 3", "types": ["beauty_salon"], "businessStatus": "OPERATIONAL"},
    {"id": "p4", "displayName": {"text": "Salon Cerrado", "languageCode": "es"},
     "formattedAddress": "C/ Falsa 4", "internationalPhoneNumber": "+34 612 000 004",
     "types": ["beauty_salon"], "businessStatus": "CLOSED_PERMANENTLY"},
    {"id": "p5", "displayName": {"text": "Uñas Belén", "languageCode": "es"},
     "formattedAddress": "C/ Falsa 5", "nationalPhoneNumber": "912 345 678",
     "types": ["beauty_salon"], "rating": 4.2, "userRatingCount": 15,
     "businessStatus": "OPERATIONAL"},
]

# Los mismos negocios, tal y como los devuelve Overpass (etiquetas de OSM).
ELEMENTOS_OSM = [
    {"type": "node", "id": 1, "tags": {
        "name": "Peluqueria Ana", "shop": "hairdresser", "phone": "+34 612 345 678",
        "addr:street": "C/ Falsa", "addr:housenumber": "1", "addr:postcode": "28038", "addr:city": "Madrid"}},
    {"type": "node", "id": 2, "tags": {
        "name": "Barberia Con Web", "shop": "hairdresser", "contact:phone": "+34 612 000 002",
        "website": "https://barberia.example"}},
    {"type": "node", "id": 3, "tags": {"name": "Estetica Sin Tel", "shop": "beauty"}},
    {"type": "way", "id": 4, "tags": {
        "name": "Uñas Belén", "shop": "beauty", "contact:phone": "912 345 678"}},
    {"type": "node", "id": 7, "tags": {
        "name": "Dos Telefonos", "shop": "hairdresser", "phone": "+34 611 000 007;+34 911 000 007"}},
    # duplicado del anterior (mismo negocio mapeado como nodo y como edificio)
    {"type": "node", "id": 5, "tags": {
        "name": "Uñas Belén", "shop": "beauty", "phone": "912 345 678"}},
    {"type": "node", "id": 6, "tags": {"shop": "beauty", "phone": "+34 600 000 000"}},  # sin nombre
]

llamadas = {"searchText": 0, "nominatim": 0, "overpass": 0}
peticiones = []          # cuerpos/headers enviados, para verificarlos
red_caida = False
respuesta_forzada = None  # (status_code, payload) para simular errores
zona_desconocida = False  # simula que Nominatim no encuentra la zona


class _Resp:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


class _RequestException(Exception):
    pass


def _fake_post(url, headers=None, json=None, data=None, timeout=None):
    if red_caida:
        raise _RequestException("sin red (simulado)")
    peticiones.append({"url": url, "headers": headers, "body": json, "data": data})
    if respuesta_forzada:
        return _Resp(respuesta_forzada[1], respuesta_forzada[0])

    if "overpass" in url:
        llamadas["overpass"] += 1
        return _Resp({"elements": ELEMENTOS_OSM})

    llamadas["searchText"] += 1
    # Segunda página: devolvemos vacío para cortar la paginación
    if json and json.get("pageToken"):
        return _Resp({"places": []})
    return _Resp({"places": PLACES})


def _fake_get(url, params=None, headers=None, timeout=None):
    if red_caida:
        raise _RequestException("sin red (simulado)")
    llamadas["nominatim"] += 1
    peticiones.append({"url": url, "headers": headers, "params": params})
    if zona_desconocida:
        return _Resp([])
    return _Resp([{"boundingbox": ["40.38", "40.42", "-3.68", "-3.64"]}])


_fake_requests = types.ModuleType("requests")
_fake_requests.RequestException = _RequestException
_fake_requests.post = _fake_post
_fake_requests.get = _fake_get
sys.modules["requests"] = _fake_requests

sys.path.insert(0, str(BASE_DIR))
import fuentes  # noqa: E402
import prospector as p  # noqa: E402

fuentes.time.sleep = lambda s: None  # sin esperas artificiales en los tests

# --- mini framework -------------------------------------------------------

fallos = []


def check(nombre, cond, extra=""):
    print(("  OK   " if cond else "  FALLO") + f"  {nombre}" + (f"   -> {extra}" if not cond and extra else ""))
    if not cond:
        fallos.append(nombre)


def seccion(titulo):
    print(f"\n{titulo}")


# --- unitarios ------------------------------------------------------------

def test_telefonos():
    seccion("Normalización de teléfonos y links de WhatsApp")
    check("internacional con +", p.normalizar_telefono("+34 612 34 56 78", "34") == "34612345678")
    check("nacional sin prefijo", p.normalizar_telefono("912 345 678", "34") == "34912345678")
    check("nacional con 0 inicial", p.normalizar_telefono("0612 345 678", "34") == "34612345678")
    check("prefijo 00 internacional", p.normalizar_telefono("(0034) 612-345-678", "34") == "34612345678")
    check("otro país", p.normalizar_telefono("+33 6 12 34 56 78", "34") == "33612345678")
    # OSM mapea a veces fijo y móvil en el mismo campo: hay que coger uno solo
    check("varios teléfonos separados por ;",
          p.normalizar_telefono("+34 611000007;+34 91 1000007", "34") == "34611000007",
          p.normalizar_telefono("+34 611000007;+34 91 1000007", "34"))

    link = p.link_whatsapp("+34 612 345 678", "Hola! ¿Qué tal? 50% & más", "34")
    check("mensaje va url-encoded", "%C2%BF" in link and "%26" in link and " " not in link, link)


def test_plantilla():
    seccion("Mensaje de plantilla")
    m = p.generar_mensaje_plantilla("Peluqueria Ana", "hair_care", "28038 Madrid")
    check("nombra el negocio y la zona", "Peluqueria Ana" in m and "28038 Madrid" in m)


def test_dotenv():
    seccion("Carga de .env")
    with tempfile.TemporaryDirectory() as d:
        env = Path(d) / ".env"
        env.write_text('# comentario\nGOOGLE_PLACES_API_KEY="abc123"\nYA_EXISTE=nuevo\n\n', encoding="utf-8")
        os.environ["YA_EXISTE"] = "original"
        os.environ.pop("GOOGLE_PLACES_API_KEY", None)
        p.cargar_dotenv(env)
        check("lee la clave y quita comillas", os.environ.get("GOOGLE_PLACES_API_KEY") == "abc123")
        check("no pisa variables del entorno", os.environ.get("YA_EXISTE") == "original")
    p.cargar_dotenv(Path(d) / "no_existe.env")
    check("sin fichero .env no falla", True)


def test_persistencia():
    seccion("CSV y base de datos")
    with tempfile.TemporaryDirectory() as d:
        p.CSV_PATH = Path(d) / "leads.csv"
        lead = {c: "x" for c in p.CAMPOS_CSV}
        p.escribir_leads_csv([lead])
        p.escribir_leads_csv([lead])
        filas = list(csv.reader(p.CSV_PATH.open(encoding="utf-8-sig")))
        check("cabecera una sola vez, 2 filas", len(filas) == 3 and filas[0] == p.CAMPOS_CSV, filas)

        p.CSV_PATH = Path(d) / "vacio.csv"
        p.CSV_PATH.touch()
        p.escribir_leads_csv([lead])
        filas = list(csv.reader(p.CSV_PATH.open(encoding="utf-8-sig")))
        check("csv existente pero vacío -> escribe cabecera", filas[0] == p.CAMPOS_CSV)

        p.DB_PATH = Path(d) / "leads_db.json"
        check("db inexistente -> dict vacío", p.load_db() == {})
        p.save_db({"pid1": {"nombre": "Ñoño Café", "estado": "pendiente_revision"}})
        check("round-trip con acentos", p.load_db()["pid1"]["nombre"] == "Ñoño Café")
        check("no deja ficheros .tmp sueltos", not (Path(d) / "leads_db.json.tmp").exists())


def test_tipos_osm():
    seccion("Traducción de búsqueda a etiquetas de OSM")
    check("peluquerias -> shop=hairdresser", fuentes.tags_para("peluquerias") == [("shop", "hairdresser")])
    check("con acentos y mayúsculas", fuentes.tags_para("Centros de Estética") == [("shop", "beauty")])
    check("clinicas dentales -> dentist", fuentes.tags_para("clinicas dentales") == [("amenity", "dentist")])
    check("gana la coincidencia más larga",
          fuentes.tags_para("peluqueria canina") == [("shop", "pet_grooming")],
          fuentes.tags_para("peluqueria canina"))
    check("etiqueta a pelo", fuentes.tags_para("shop=beauty") == [("shop", "beauty")])
    check("desconocido -> sin etiquetas", fuentes.tags_para("tienda de cosas raras") == [])
    check("singular y plural dan igual", fuentes.tags_para("dentista") == fuentes.tags_para("dentistas"))
    check("'clinica' a secas sigue siendo clinic", fuentes.tags_para("clinicas") == [("amenity", "clinic")])
    check("no confunde barberia con bar", fuentes.tags_para("barberias") == [("shop", "hairdresser")],
          fuentes.tags_para("barberias"))


def test_osm():
    seccion("Fuente OpenStreetMap (sin API key)")
    peticiones.clear()
    negocios = fuentes.buscar_osm("peluquerias", "28038 Madrid", 20)

    nominatim = peticiones[0]
    check("geocodifica con Nominatim", "nominatim" in nominatim["url"])
    check("se identifica con User-Agent", "prospector" in nominatim["headers"].get("User-Agent", ""))
    check("limita el país", nominatim["params"]["countrycodes"] == "es")

    overpass = peticiones[1]
    consulta = overpass["data"]["data"]
    check("consulta Overpass con la etiqueta correcta", 'nwr["shop"="hairdresser"]' in consulta, consulta)
    check("acota a la caja geográfica", "40.380000,-3.680000,40.420000,-3.640000" in consulta, consulta)

    nombres = [n["nombre"] for n in negocios]
    check("descarta los que no tienen nombre", "" not in nombres and len(nombres) == 5, nombres)
    check("se queda con el primer teléfono cuando hay varios",
          [n for n in negocios if n["nombre"] == "Dos Telefonos"][0]["telefono"] == "+34 611 000 007")
    # Orden esperado: primero los que se pueden contactar, luego los que no tienen
    # teléfono, y al final los que ya tienen web (esos no interesan de ninguna manera).
    utiles = [n for n in negocios if n["telefono"] and not n["web"]]
    check("prioriza los aprovechables (con tel y sin web)",
          negocios[:len(utiles)] == utiles and negocios[-1]["web"],
          [(n["nombre"], bool(n["telefono"]), bool(n["web"])) for n in negocios])
    check("deduplica el mismo negocio mapeado dos veces", nombres.count("Uñas Belén") == 1, nombres)
    check("lee phone y contact:phone",
          [n for n in negocios if n["nombre"] == "Uñas Belén"][0]["telefono"] == "912 345 678")
    check("detecta la web cuando está mapeada",
          [n for n in negocios if n["nombre"] == "Barberia Con Web"][0]["web"] == "https://barberia.example")
    check("compone la dirección desde addr:*",
          [n for n in negocios if n["nombre"] == "Peluqueria Ana"][0]["direccion"] == "C/ Falsa 1, 28038 Madrid",
          [n for n in negocios if n["nombre"] == "Peluqueria Ana"][0]["direccion"])
    check("marca la fuente", all(n["fuente"] == "osm" for n in negocios))
    check("id único por elemento", negocios[0]["id"].startswith("osm/node/"))

    ana = [n for n in negocios if n["nombre"] == "Peluqueria Ana"][0]
    check("enlace a la ficha de Google con nombre y dirección",
          ana["google_maps"].startswith("https://www.google.com/maps/search/?api=1&query=")
          and "Peluqueria%20Ana" in ana["google_maps"] and "Falsa" in ana["google_maps"],
          ana["google_maps"])
    sin_dir = [n for n in negocios if n["nombre"] == "Dos Telefonos"][0]
    check("sin dirección usa la zona", "28038" in sin_dir["google_maps"], sin_dir["google_maps"])



def test_busqueda_general():
    """Tipo de negocio vacío = cualquier negocio de la zona."""
    seccion("Búsqueda general (sin tipo de negocio)")
    peticiones.clear()
    negocios = fuentes.buscar_osm("", "28038 Madrid", 20)
    consulta = peticiones[-1]["data"]["data"]

    check("pide todas las tiendas", 'nwr["shop"]["name"]' in consulta, consulta)
    check("pide oficinas y talleres", 'nwr["office"]["name"]' in consulta and 'nwr["craft"]' in consulta)
    check("filtra amenity a los que son negocio", '"amenity"~"^(restaurant|cafe' in consulta, consulta)
    check("no cuela mobiliario urbano", '"amenity"]["name"]' not in consulta, consulta)
    check("solo elementos con nombre", consulta.count('["name"]') >= 4, consulta)
    check("devuelve resultados igualmente", len(negocios) > 0)

    # Con espacios en blanco también cuenta como general
    peticiones.clear()
    fuentes.buscar_osm("   ", "28038 Madrid", 20)
    check("solo espacios cuenta como general", 'nwr["shop"]["name"]' in peticiones[-1]["data"]["data"])

    peticiones.clear()
    fuentes.buscar_google("", "28038 Madrid", "k", 10)
    texto = peticiones[-1]["body"]["textQuery"]
    check("google recibe un término genérico", texto == "negocios y comercios en 28038 Madrid", texto)


def test_eleccion_de_fuente():
    seccion("Elección de fuente")
    check("auto sin key -> osm", p.elegir_fuente("auto", None) == "osm")
    check("auto con key -> google", p.elegir_fuente("auto", "una-key") == "google")
    check("explícita manda", p.elegir_fuente("osm", "una-key") == "osm")


def test_peticion_a_google():
    seccion("Petición a Places API (New)")
    peticiones.clear()
    fuentes.buscar_google("peluquerias", "28038 Madrid", "mi-key", 20)
    pet = peticiones[-1]
    check("usa el endpoint nuevo", pet["url"] == "https://places.googleapis.com/v1/places:searchText", pet["url"])
    check("manda la key en la cabecera", pet["headers"].get("X-Goog-Api-Key") == "mi-key")
    check("manda field mask", "places.internationalPhoneNumber" in pet["headers"].get("X-Goog-FieldMask", ""))
    check("pide la web (para saber quién no tiene)", "places.websiteUri" in pet["headers"]["X-Goog-FieldMask"])
    check("pide el enlace a la ficha", "places.googleMapsUri" in pet["headers"]["X-Goog-FieldMask"])
    check("query en el cuerpo", pet["body"]["textQuery"] == "peluquerias en 28038 Madrid", pet["body"])
    check("resultados en español", pet["body"]["languageCode"] == "es")
    check("pageSize dentro del límite de Google", 1 <= pet["body"]["pageSize"] <= 20, pet["body"]["pageSize"])


def falla_con(fn, texto_esperado):
    """Devuelve (True, mensaje) si fn() lanza FuenteNoDisponible con ese texto."""
    try:
        fn()
        return False, "no lanzó nada (devolvió lista vacía: parecería 'no hay nada')"
    except fuentes.FuenteNoDisponible as e:
        return texto_esperado.lower() in str(e).lower(), str(e)[:90]
    except Exception as e:  # noqa: BLE001
        return False, f"lanzó {type(e).__name__}: {e}"


def test_errores():
    """Un fallo de la fuente NO puede parecer 'esta zona no tiene negocios'."""
    seccion("Fallos de la fuente, distinguidos de 'no hay resultados'")
    global red_caida, respuesta_forzada, zona_desconocida

    red_caida = True
    try:
        check("google sin red avisa", *falla_con(lambda: fuentes.buscar_google("x", "y", "k", 5), "no se pudo contactar"))
        check("osm sin red avisa", *falla_con(lambda: fuentes.buscar_osm("x", "y", 5), "conexión"))
    finally:
        red_caida = False

    zona_desconocida = True
    try:
        ok, msg = falla_con(lambda: fuentes.buscar_osm("peluquerias", "Zona Inventada", 5), "no encuentro la zona")
        check("zona inexistente avisa y sugiere el arreglo", ok and "barrio" in msg.lower(), msg)
    finally:
        zona_desconocida = False

    respuesta_forzada = (403, {"error": {"status": "PERMISSION_DENIED", "message": "Places API (New) has not been used"}})
    try:
        check("403 de Google avisa", *falla_con(lambda: fuentes.buscar_google("x", "y", "k", 5), "403"))
    finally:
        respuesta_forzada = None

    respuesta_forzada = (429, {})
    peticiones.clear()
    try:
        check("Overpass saturado avisa", *falla_con(lambda: fuentes.buscar_osm("peluquerias", "Madrid", 5), "no responden"))
        urls_probadas = {pet["url"] for pet in peticiones if "overpass" in pet["url"]}
        check("con 429 prueba otro servidor", len(urls_probadas) > 1, urls_probadas)
    finally:
        respuesta_forzada = None

    seccion("Cuánto se espera antes de rendirse")
    espera_total = fuentes.ESPERA_OVERPASS * len(fuentes.REINTENTOS_OVERPASS) + sum(fuentes.REINTENTOS_OVERPASS)
    check("el peor caso no pasa de 2 minutos", espera_total <= 120, f"{espera_total}s")


# --- integración ----------------------------------------------------------

def test_flujo_completo():
    d = Path(tempfile.mkdtemp())
    try:
        p.DB_PATH = d / "leads_db.json"
        p.CSV_PATH = d / "leads.csv"
        p.ENV_PATH = d / ".env"
        os.environ["GOOGLE_PLACES_API_KEY"] = "fake-key-para-tests"

        def correr(*extra):
            """Ejecuta prospector.main() silenciando su salida (imprime bastante)."""
            sys.argv = ["prospector.py", "--query", "peluquerias", "--zona", "28038 Madrid",
                        "--max", "10", "--fuente", "google", *extra]
            with open(os.devnull, "w", encoding="utf-8") as nulo, redirect_stdout(nulo):
                p.main()

        def filas_csv():
            if not p.CSV_PATH.exists():
                return []
            return list(csv.DictReader(p.CSV_PATH.open(encoding="utf-8-sig")))

        seccion("Primera ejecución: filtrado de negocios")
        correr()
        filas = filas_csv()
        db = json.loads(p.DB_PATH.read_text(encoding="utf-8"))
        nombres = sorted(f["nombre"] for f in filas)

        check("solo entran los que no tienen web", nombres == ["Peluqueria Ana", "Uñas Belén"], nombres)
        check("con web -> tiene_web", db["p2"]["estado"] == "tiene_web")
        check("sin teléfono -> sin_telefono", db["p3"]["estado"] == "sin_telefono")
        check("cerrado permanentemente -> descartado", db["p4"]["estado"] == "descartado")
        check("lead nuevo -> pendiente_revision", db["p1"]["estado"] == "pendiente_revision")
        check("teléfono nacional -> link con prefijo",
              [f for f in filas if f["nombre"] == "Uñas Belén"][0]["whatsapp_link"].startswith("https://wa.me/34912345678?"))
        check("columnas del csv correctas", list(filas[0].keys()) == p.CAMPOS_CSV, list(filas[0].keys()))
        check("con Google, el 'sin web' va confirmado", filas[0]["web_confirmada"] == "si")
        ana = [f for f in filas if f["nombre"] == "Peluqueria Ana"][0]
        check("usa el enlace exacto que da Google, no uno compuesto",
              ana["google_maps"] == "https://maps.google.com/?cid=17395", ana["google_maps"])
        belen = [f for f in filas if f["nombre"] == "Uñas Belén"][0]
        check("si Google no lo trae, se compone",
              belen["google_maps"].startswith("https://www.google.com/maps/search/"), belen["google_maps"])

        seccion("Segunda ejecución: no debe duplicar")
        correr()
        check("no duplica filas en el csv", len(filas_csv()) == 2, len(filas_csv()))
        db2 = json.loads(p.DB_PATH.read_text(encoding="utf-8"))
        check("los estados no cambian", db2 == db)

        seccion("marcar_contactado.py")
        py = sys.executable
        script = str(BASE_DIR / "marcar_contactado.py")
        # PROSPECTOR_DATA_DIR hace que el script use el histórico temporal, no el real
        entorno = dict(os.environ, PYTHONIOENCODING="utf-8", PROSPECTOR_DATA_DIR=str(d))

        def correr_script(*argumentos):
            return subprocess.run([py, script, *argumentos], capture_output=True, text=True,
                                  encoding="utf-8", env=entorno)

        r = correr_script("Peluqueria Ana")
        check("marca como contactado", r.returncode == 0 and "contactado" in r.stdout, r.stdout + r.stderr)

        r = correr_script("--pendientes")
        check("--pendientes lista solo lo pendiente",
              "Uñas Belén" in r.stdout and "Peluqueria Ana" not in r.stdout, r.stdout + r.stderr)

        r = correr_script("NoExisteEsteNegocio")
        check("nombre inexistente -> error claro", r.returncode == 1 and "No se encontró" in r.stderr, r.stderr)

        seccion("--revisar-pendientes")
        correr("--revisar-pendientes")
        filas = filas_csv()
        check("re-saca el pendiente pero no el contactado",
              len(filas) == 3 and filas[2]["nombre"] == "Uñas Belén", [f["nombre"] for f in filas])
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_flujo_osm():
    """El modo por defecto, el que funciona sin ninguna API key."""
    d = Path(tempfile.mkdtemp())
    try:
        p.DB_PATH = d / "leads_db.json"
        p.CSV_PATH = d / "leads.csv"
        p.ENV_PATH = d / ".env"
        os.environ.pop("GOOGLE_PLACES_API_KEY", None)

        seccion("Flujo completo con OSM (sin API key)")
        sys.argv = ["prospector.py", "--query", "peluquerias", "--zona", "28038 Madrid", "--max", "10"]
        with open(os.devnull, "w", encoding="utf-8") as nulo, redirect_stdout(nulo):
            p.main()

        filas = list(csv.DictReader(p.CSV_PATH.open(encoding="utf-8-sig")))
        nombres = sorted(f["nombre"] for f in filas)
        check("sin key funciona igualmente",
              nombres == ["Dos Telefonos", "Peluqueria Ana", "Uñas Belén"], nombres)
        check("marca la fuente en el csv", all(f["fuente"] == "osm" for f in filas))
        check("avisa de que el 'sin web' no está confirmado", filas[0]["web_confirmada"] == "no")
        check("sin rating no rompe el csv", filas[0]["rating"] == "")

        db = json.loads(p.DB_PATH.read_text(encoding="utf-8"))
        check("guarda el histórico con ids de OSM", all(k.startswith("osm/") for k in db), list(db)[:3])
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_api_web():
    """La app web usa el mismo núcleo; aquí comprobamos sus endpoints."""
    import app as webapp

    d = Path(tempfile.mkdtemp())
    try:
        p.DB_PATH = d / "leads_db.json"
        p.CSV_PATH = d / "leads.csv"
        p.ENV_PATH = d / ".env"
        os.environ.pop("GOOGLE_PLACES_API_KEY", None)
        cliente = webapp.app.test_client()

        seccion("API web")
        cfg = cliente.get("/api/config").get_json()
        check("config dice si hay keys", cfg["hay_key_google"] is False)
        check("config manda los tipos para autocompletar", "peluqueria" in cfg["tipos"])

        r = cliente.post("/api/buscar", json={"query": "peluquerias", "zona": ""})
        check("sin zona -> 400 con mensaje", r.status_code == 400 and "error" in r.get_json())

        datos = cliente.post("/api/buscar", json={
            "query": "peluquerias", "zona": "28038 Madrid", "max": 10}).get_json()
        nombres = sorted(l["nombre"] for l in datos["leads"])
        check("devuelve los leads", nombres == ["Dos Telefonos", "Peluqueria Ana", "Uñas Belén"], nombres)
        check("cada lead trae su id para poder marcarlo", all(l["id"] for l in datos["leads"]))
        check("incluye el resumen de descartes", datos["conteo"]["sin_telefono"] == 1, datos["conteo"])
        check("incluye la traza para enseñarla", any("Buscando" in t for t in datos["traza"]))
        check("avisa de que la web no está confirmada", datos["web_confirmada"] is False)

        pendientes = cliente.get("/api/pendientes").get_json()["pendientes"]
        check("los nuevos quedan como pendientes", len(pendientes) == 3, len(pendientes))
        check("el pendiente trae mensaje y link listos",
              all(x.get("mensaje") and x.get("whatsapp_link") for x in pendientes))

        # Registros de versiones anteriores: solo nombre, teléfono y estado
        antiguo = p.load_db()
        antiguo["osm/node/999"] = {"nombre": "Bar Antiguo", "estado": "pendiente_revision",
                                   "telefono": "+34 600 111 222", "zona": "28038 Madrid"}
        p.save_db(antiguo)
        viejo = [x for x in cliente.get("/api/pendientes").get_json()["pendientes"]
                 if x["id"] == "osm/node/999"][0]
        check("registro antiguo sin mensaje -> se regenera", "Bar Antiguo" in viejo["mensaje"])
        check("registro antiguo sin link -> se reconstruye",
              viejo["whatsapp_link"].startswith("https://wa.me/34600111222?"), viejo.get("whatsapp_link"))
        check("registro antiguo sin ficha -> también se compone",
              "Bar%20Antiguo" in viejo["google_maps"], viejo.get("google_maps"))

        nuevo = [x for x in pendientes if x["nombre"] == "Peluqueria Ana"]
        check("los pendientes llevan la ficha de Google",
              all(x.get("google_maps") for x in pendientes), [x.get("google_maps") for x in pendientes[:2]])

        objetivo = datos["leads"][0]["id"]
        antes = len(cliente.get("/api/pendientes").get_json()["pendientes"])
        r = cliente.post("/api/estado", json={"id": objetivo, "estado": "contactado"})
        check("marcar contactado responde ok", r.status_code == 200 and r.get_json()["ok"])
        despues = cliente.get("/api/pendientes").get_json()["pendientes"]
        check("y desaparece de pendientes",
              len(despues) == antes - 1 and all(x["id"] != objetivo for x in despues))

        r = cliente.post("/api/estado", json={"id": "no/existe", "estado": "contactado"})
        check("id inexistente -> 404", r.status_code == 404)
        r = cliente.post("/api/estado", json={"id": objetivo, "estado": "borrar_todo"})
        check("estado inválido -> 400", r.status_code == 400)

        r = cliente.post("/api/buscar", json={"query": "x", "zona": "y", "fuente": "google"})
        check("pedir google sin key -> error explicado",
              r.status_code == 400 and "OpenStreetMap" in r.get_json()["error"], r.get_json())

        # Lo que estaba fallando: un servicio caído se veía igual que 'no hay negocios'
        global respuesta_forzada
        respuesta_forzada = (429, {})
        try:
            r = cliente.post("/api/buscar", json={"query": "peluquerias", "zona": "Madrid"})
            check("fuente saturada -> 503, no un vacío silencioso", r.status_code == 503, r.status_code)
            check("y explica qué hacer", "espera" in r.get_json()["error"].lower(), r.get_json()["error"][:80])
        finally:
            respuesta_forzada = None

        # Sin tipo de negocio = búsqueda general. Va al final porque marca como
        # vistos a los negocios que usan los checks anteriores.
        r = cliente.post("/api/buscar", json={"query": "", "zona": "28038 Madrid", "max": 5,
                                              "revisar_pendientes": True})
        check("sin tipo de negocio sí vale (búsqueda general)", r.status_code == 200, r.get_json())
        check("la búsqueda general devuelve leads", len(r.get_json()["leads"]) > 0)

        check("la página se sirve", cliente.get("/").status_code == 200)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def main():
    for fn in (test_telefonos, test_plantilla, test_dotenv, test_persistencia,
               test_tipos_osm, test_osm, test_busqueda_general, test_eleccion_de_fuente,
               test_peticion_a_google,
               test_errores, test_flujo_completo, test_flujo_osm, test_api_web):
        fn()

    print()
    if fallos:
        print(f"{len(fallos)} FALLOS: {fallos}")
        sys.exit(1)
    print("Todos los tests pasan.")


if __name__ == "__main__":
    main()
