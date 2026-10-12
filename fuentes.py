#!/usr/bin/env python3
"""
Fuentes de datos de negocios para prospector.py.

Dos backends que devuelven exactamente la misma estructura:

  - "osm":    OpenStreetMap, vía Nominatim (geocodifica la zona) + Overpass
              (busca los negocios). Gratis, sin API key, sin cuenta y sin
              tarjeta. Es el modo por defecto.
  - "google": Google Places API (New). Datos más completos y fiables, sobre
              todo el campo "tiene web", pero necesita API key con
              facturación activada.

Cada backend devuelve una lista de dicts con estas claves:

    id, nombre, direccion, web, telefono, rating, resenas,
    estado_negocio, tipo, fuente

OJO con el dato clave del proyecto ("¿tiene web?"):
  - En Google, `websiteUri` es fiable: si no está, es que no tiene.
  - En OSM, la ausencia de `website` solo significa que nadie la ha mapeado.
    Por eso los leads de OSM salen marcados como "sin web (sin confirmar)".
"""

import re
import time
import unicodedata
import urllib.parse

import requests

# --- Cabeceras educadas ----------------------------------------------------
# Nominatim y Overpass son servicios gratuitos mantenidos por donaciones y
# piden identificarse con un User-Agent descriptivo y no machacarlos a peticiones.
UA = "prospector/1.0 (herramienta personal de prospeccion, uso bajo volumen)"


class FuenteNoDisponible(Exception):
    """La fuente falló: sin red, servidor saturado, zona no encontrada, key mal...

    Es distinto de "la búsqueda no encontró nada": si no separamos los dos casos,
    un servidor caído se le presenta al usuario como 'aquí no hay negocios'.
    """


def _headers(contacto: str | None = None) -> dict:
    ua = UA if not contacto else f"{UA} contacto: {contacto}"
    return {"User-Agent": ua}


# --- OpenStreetMap ---------------------------------------------------------

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"

# Overpass son servidores gratuitos y compartidos: cuando el principal está
# saturado (429/504) probamos un espejo antes de rendirnos.
OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]

# Traducción de "lo que escribe Dario" a etiquetas de OpenStreetMap.
# La clave se busca por coincidencia parcial sobre el texto normalizado.
TIPOS_OSM = {
    "peluqueria": [("shop", "hairdresser")],
    "barberia": [("shop", "hairdresser")],
    "estetica": [("shop", "beauty")],
    "belleza": [("shop", "beauty")],
    "unas": [("shop", "beauty")],
    "manicura": [("shop", "beauty")],
    "masaje": [("shop", "massage")],
    "spa": [("leisure", "spa")],
    "tatuaje": [("shop", "tattoo")],
    "dentista": [("amenity", "dentist")],
    "dental": [("amenity", "dentist")],
    "clinica dental": [("amenity", "dentist")],
    "fisio": [("healthcare", "physiotherapist")],
    "clinica": [("amenity", "clinic")],
    "veterinario": [("amenity", "veterinary")],
    "optica": [("shop", "optician")],
    "farmacia": [("amenity", "pharmacy")],
    "gimnasio": [("leisure", "fitness_centre")],
    "restaurante": [("amenity", "restaurant")],
    "pizzeria": [("amenity", "restaurant")],
    "bar": [("amenity", "bar"), ("amenity", "pub")],
    "cafeteria": [("amenity", "cafe")],
    "panaderia": [("shop", "bakery")],
    "pasteleria": [("shop", "pastry")],
    "heladeria": [("amenity", "ice_cream")],
    "carniceria": [("shop", "butcher")],
    "fruteria": [("shop", "greengrocer")],
    "pescaderia": [("shop", "seafood")],
    "floristeria": [("shop", "florist")],
    "ferreteria": [("shop", "hardware"), ("shop", "doityourself")],
    "ropa": [("shop", "clothes")],
    "zapateria": [("shop", "shoes")],
    "joyeria": [("shop", "jewelry")],
    "mueble": [("shop", "furniture")],
    "copisteria": [("shop", "copyshop")],
    "lavanderia": [("shop", "laundry")],
    "tintoreria": [("shop", "dry_cleaning")],
    "taller": [("shop", "car_repair")],
    "autoescuela": [("amenity", "driving_school")],
    "inmobiliaria": [("office", "estate_agent")],
    "abogado": [("office", "lawyer")],
    "asesoria": [("office", "accountant"), ("office", "tax_advisor")],
    "gestoria": [("office", "accountant"), ("office", "tax_advisor")],
    "academia": [("amenity", "language_school"), ("office", "educational_institution")],
    "hotel": [("tourism", "hotel")],
    "hostal": [("tourism", "guest_house")],
    "peluqueria canina": [("shop", "pet_grooming")],
    "animales": [("shop", "pet")],
}


def enlace_ficha_google(nombre: str, donde: str = "") -> str:
    """Enlace a la ficha de empresa en Google Maps.

    Google Places da la URL exacta (`googleMapsUri`); para los negocios que
    vienen de OpenStreetMap no hay identificador de Google, así que usamos la
    URL de búsqueda de Maps con nombre y dirección: con los dos datos suele
    caer directamente en la ficha del negocio.
    """
    consulta = " ".join(x for x in (nombre, donde) if x).strip()
    return "https://www.google.com/maps/search/?api=1&query=" + urllib.parse.quote(consulta)


def normalizar_texto(texto: str) -> str:
    """minúsculas y sin acentos, para poder comparar 'Estética' con 'estetica'."""
    sin_acentos = unicodedata.normalize("NFKD", texto.lower())
    return "".join(c for c in sin_acentos if not unicodedata.combining(c))


def _variantes(palabra: str) -> set[str]:
    """La palabra y su singular aproximado: 'dentales' -> 'dental', 'clinicas' -> 'clinica'."""
    formas = {palabra}
    if palabra.endswith("es") and len(palabra) > 4:
        formas.add(palabra[:-2])
    if palabra.endswith("s") and len(palabra) > 3:
        formas.add(palabra[:-1])
    return formas


def _encaja(palabra_clave: str, palabra_query: str) -> bool:
    """Compara dos palabras tolerando plurales y terminaciones ('peluqueria'/'peluquerias')."""
    for forma in _variantes(palabra_query):
        if palabra_clave == forma:
            return True
        if len(palabra_clave) >= 4 and forma.startswith(palabra_clave):
            return True
        if len(forma) >= 4 and palabra_clave.startswith(forma):
            return True
    return False


def tags_para(query: str) -> list[tuple[str, str]]:
    """Devuelve las etiquetas OSM que corresponden a la búsqueda, o [] si no
    hay ninguna conocida (entonces se busca por nombre)."""
    q = normalizar_texto(query)

    # Permite escribir la etiqueta a pelo: --query "shop=beauty"
    if "=" in q:
        clave, _, valor = q.partition("=")
        return [(clave.strip(), valor.strip())]

    palabras_query = q.split()
    encajes = []
    for clave, tags in TIPOS_OSM.items():
        palabras_clave = clave.split()
        if all(any(_encaja(pc, pq) for pq in palabras_query) for pc in palabras_clave):
            encajes.append((clave, tags))

    if not encajes:
        return []

    # Gana la clave más específica: primero la que usa más palabras
    # ("clinica dental" antes que "clinica"), y a igualdad, la más larga.
    return max(encajes, key=lambda par: (len(par[0].split()), len(par[0])))[1]


def geocodificar(zona: str, region: str = "ES", contacto: str | None = None) -> tuple[float, float, float, float]:
    """Convierte 'Vallecas Madrid' o '28038 Madrid' en una caja (sur, oeste, norte, este)."""
    params = {"q": zona, "format": "json", "limit": 1, "countrycodes": region.lower()}
    try:
        resp = requests.get(NOMINATIM_URL, params=params, headers=_headers(contacto), timeout=20)
        datos = resp.json()
    except (requests.RequestException, ValueError) as e:
        raise FuenteNoDisponible(
            f"No se pudo contactar con el buscador de zonas de OpenStreetMap ({e}). "
            f"Comprueba tu conexión y vuelve a intentarlo.") from e

    if not datos:
        raise FuenteNoDisponible(
            f"No encuentro la zona «{zona}». Prueba con el nombre del barrio o distrito "
            f"y la ciudad, por ejemplo «Puente de Vallecas, Madrid». Los códigos "
            f"postales sueltos suelen fallar.")

    sur, norte, oeste, este = (float(v) for v in datos[0]["boundingbox"])
    return sur, oeste, norte, este


# Para la búsqueda general ("todos los negocios de la zona"). En OSM, `amenity`
# incluye bancos, papeleras y aparcamientos, así que solo dejamos pasar los
# valores que son negocios de cara al público. `shop`, `office`, `craft` y
# `healthcare` sí son de negocio por definición y entran enteros.
AMENITIES_NEGOCIO = (
    "restaurant", "cafe", "bar", "pub", "fast_food", "ice_cream", "biergarten",
    "dentist", "clinic", "doctors", "veterinary", "pharmacy",
    "driving_school", "language_school", "music_school", "nightclub",
)
LEISURE_NEGOCIO = ("fitness_centre", "spa", "dance", "escape_game")
TOURISM_NEGOCIO = ("hotel", "guest_house", "hostel", "apartment")


def _consulta_general(caja: str) -> list[str]:
    def alternativas(clave, valores):
        return f'  nwr["{clave}"~"^({"|".join(valores)})$"]["name"]({caja});'

    return [
        f'  nwr["shop"]["name"]({caja});',
        f'  nwr["office"]["name"]({caja});',
        f'  nwr["craft"]["name"]({caja});',
        f'  nwr["healthcare"]["name"]({caja});',
        alternativas("amenity", AMENITIES_NEGOCIO),
        alternativas("leisure", LEISURE_NEGOCIO),
        alternativas("tourism", TOURISM_NEGOCIO),
    ]


def _consulta_overpass(tags: list[tuple[str, str]], nombre: str | None, bbox: tuple) -> str:
    caja = ",".join(f"{c:.6f}" for c in bbox)
    # Pedimos solo elementos con nombre: sin nombre no hay mensaje que escribir,
    # y así la respuesta pesa bastante menos.
    if tags:
        partes = [f'  nwr["{clave}"="{valor}"]["name"]({caja});' for clave, valor in tags]
    elif not (nombre or "").strip():
        partes = _consulta_general(caja)          # búsqueda general: cualquier negocio
    else:
        # Sin etiqueta conocida: buscamos por nombre entre cosas que sean negocios.
        seguro = re.sub(r"[^\w\s]", "", nombre, flags=re.UNICODE).strip()
        partes = [f'  nwr["name"~"{seguro}",i]["{clave}"]({caja});'
                  for clave in ("shop", "amenity", "office", "craft", "healthcare")]

    cuerpo = "\n".join(partes)
    return f"[out:json][timeout:90];\n(\n{cuerpo}\n);\nout center tags;"


def _primer_telefono(valor: str | None) -> str | None:
    """En OSM es habitual mapear varios teléfonos en un mismo campo separados
    por ';' (fijo y móvil). Nos quedamos con el primero."""
    if not valor:
        return None
    primero = re.split(r"[;,]", valor)[0].strip()
    return primero or None


def _direccion_osm(tags: dict) -> str:
    calle = " ".join(x for x in (tags.get("addr:street"), tags.get("addr:housenumber")) if x)
    resto = " ".join(x for x in (tags.get("addr:postcode"), tags.get("addr:city")) if x)
    return ", ".join(x for x in (calle, resto) if x)


# Cuánto esperamos a Overpass. Son servidores compartidos: cuando van lentos,
# van muy lentos. Más vale fallar en ~1 minuto con un mensaje claro que tener a
# alguien mirando una barra de progreso cinco minutos para acabar sin nada.
ESPERA_OVERPASS = 40
REINTENTOS_OVERPASS = (0, 5)


def _pedir_a_overpass(consulta: str, contacto: str | None, log=print) -> list:
    """Lanza la consulta probando los distintos servidores.
    Lanza FuenteNoDisponible si ninguno responde."""
    problemas = []
    for intento, espera in enumerate(REINTENTOS_OVERPASS):
        url = OVERPASS_URLS[intento % len(OVERPASS_URLS)]
        if espera:
            log(f"  [i] Probando otro servidor de OpenStreetMap...")
            time.sleep(espera)

        try:
            resp = requests.post(url, data={"data": consulta}, headers=_headers(contacto),
                                 timeout=ESPERA_OVERPASS)
        except requests.RequestException as e:
            problemas.append(f"{url}: {type(e).__name__}")
            continue

        if resp.status_code in (429, 504):
            problemas.append(f"{url}: saturado ({resp.status_code})")
            continue
        if resp.status_code != 200:
            raise FuenteNoDisponible(f"OpenStreetMap respondió {resp.status_code} y no se pudo "
                                     f"completar la búsqueda.")

        try:
            return resp.json().get("elements", [])
        except ValueError as e:
            raise FuenteNoDisponible(f"OpenStreetMap devolvió una respuesta ilegible ({e}).") from e

    raise FuenteNoDisponible(
        "Los servidores de OpenStreetMap no responden ahora mismo (son gratuitos y "
        "compartidos, y se saturan a ratos). Espera un par de minutos y vuelve a "
        "intentarlo, o reduce el tamaño de la zona.\nDetalle: " + "; ".join(problemas))


def buscar_osm(query: str, zona: str, max_resultados: int, region: str = "ES",
               contacto: str | None = None, log=print) -> list[dict]:
    bbox = geocodificar(zona, region, contacto)

    tags = tags_para(query) if query.strip() else []
    if not query.strip():
        log("  [i] Búsqueda general: cualquier negocio de la zona. Puede tardar más.")
    elif not tags:
        log(f"  [i] '{query}' no está en la lista de tipos conocidos: se buscará por nombre.\n"
            f"      (usa --listar-tipos para ver los tipos con búsqueda precisa)")

    consulta = _consulta_overpass(tags, query, bbox)
    elementos = _pedir_a_overpass(consulta, contacto, log)

    negocios = []
    vistos = set()
    for el in elementos:
        tags_el = el.get("tags", {})
        nombre = tags_el.get("name")
        if not nombre:
            continue  # sin nombre no hay mensaje que escribir

        telefono = _primer_telefono(
            tags_el.get("phone") or tags_el.get("contact:phone")
            or tags_el.get("mobile") or tags_el.get("contact:mobile"))
        web = (tags_el.get("website") or tags_el.get("contact:website") or tags_el.get("url"))

        # El mismo negocio puede estar mapeado como nodo y como edificio
        clave = (normalizar_texto(nombre), re.sub(r"\D", "", telefono or ""))
        if clave in vistos:
            continue
        vistos.add(clave)

        tipo = next((f"{k}={tags_el[k]}" for k in ("shop", "amenity", "office", "craft", "healthcare")
                     if k in tags_el), "")

        direccion = _direccion_osm(tags_el)
        negocios.append({
            "id": f"osm/{el.get('type')}/{el.get('id')}",
            "nombre": nombre,
            "direccion": direccion,
            "google_maps": enlace_ficha_google(nombre, direccion or zona),
            "web": web,
            "telefono": telefono,
            "rating": None,      # OSM no tiene valoraciones
            "resenas": None,
            "estado_negocio": None,
            "tipo": tipo,
            "fuente": "osm",
        })

    # En OSM muchos negocios están mapeados sin teléfono, y sin teléfono no hay
    # WhatsApp que mandar. Ordenamos los aprovechables primero para que --max
    # no se gaste en los que de todas formas íbamos a descartar.
    negocios.sort(key=lambda n: (bool(n["web"]), not bool(n["telefono"])))

    utiles = sum(1 for n in negocios if n["telefono"] and not n["web"])
    if utiles < len(negocios):
        log(f"  [i] En OpenStreetMap hay {len(negocios)} negocios de este tipo en la zona, "
            f"pero solo {utiles} tienen teléfono mapeado y ninguna web.")

    return negocios[:max_resultados]


# --- Google Places (New) ---------------------------------------------------

GOOGLE_SEARCHTEXT_URL = "https://places.googleapis.com/v1/places:searchText"

CAMPOS_RESPUESTA = ",".join([
    "places.id",
    "places.displayName",
    "places.formattedAddress",
    "places.googleMapsUri",
    "places.websiteUri",
    "places.internationalPhoneNumber",
    "places.nationalPhoneNumber",
    "places.rating",
    "places.userRatingCount",
    "places.businessStatus",
    "places.types",
    "nextPageToken",
])

MAX_POR_PAGINA = 20
MAX_TOTAL_GOOGLE = 60


def _explicar_error_google(status_code: int, data: dict) -> str:
    error = data.get("error", {})
    mensaje = error.get("message", "sin detalle")
    if "API key not valid" in mensaje:
        return f"{mensaje}\n      Revisa GOOGLE_PLACES_API_KEY en tu .env (copiada entera, sin espacios)."
    if status_code in (401, 403) or error.get("status") == "PERMISSION_DENIED":
        return (
            f"{mensaje}\n"
            "      Revisa en https://console.cloud.google.com/ que:\n"
            "        1) tienes habilitada 'Places API (New)' en el proyecto,\n"
            "        2) el proyecto tiene facturación activada (hay crédito gratuito),\n"
            "        3) la API key no tiene restricciones que bloqueen esta llamada."
        )
    if status_code == 429:
        return f"{mensaje}\n      Has pasado la cuota. Espera un rato o súbela en la consola de Google."
    return mensaje


def buscar_google(query: str, zona: str, api_key: str, max_resultados: int,
                  region: str = "ES") -> list[dict]:
    """Text Search (New): cada resultado ya trae web, teléfono y estado,
    así que basta una llamada por página (la API antigua pedía una por negocio)."""
    crudos = []
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": CAMPOS_RESPUESTA,
    }
    # Text Search siempre necesita texto; para la búsqueda general usamos un
    # término genérico (Google no tiene un "dame todos los negocios de aquí").
    texto = f"{query.strip()} en {zona}" if query.strip() else f"negocios y comercios en {zona}"

    cuerpo = {
        "textQuery": texto,
        "languageCode": "es",
        "regionCode": region,
        "pageSize": min(MAX_POR_PAGINA, max_resultados),
    }

    while True:
        try:
            resp = requests.post(GOOGLE_SEARCHTEXT_URL, headers=headers, json=cuerpo, timeout=20)
            data = resp.json()
        except (requests.RequestException, ValueError) as e:
            if crudos:
                break        # ya tenemos una página: devolvemos lo que haya
            raise FuenteNoDisponible(f"No se pudo contactar con Google Places: {e}") from e

        if resp.status_code != 200:
            raise FuenteNoDisponible(
                f"Google Places devolvió {resp.status_code}: "
                f"{_explicar_error_google(resp.status_code, data)}")

        crudos.extend(data.get("places", []))

        token = data.get("nextPageToken")
        if not token or len(crudos) >= max_resultados:
            break

        # El token tarda un par de segundos en activarse; el resto del cuerpo
        # debe ir idéntico al de la primera página.
        time.sleep(2)
        cuerpo = {**cuerpo, "pageToken": token}

    return [_aplanar_google(p) for p in crudos[:max_resultados]]


def _aplanar_google(place: dict) -> dict:
    nombre = (place.get("displayName") or {}).get("text", "")
    direccion = place.get("formattedAddress", "")
    return {
        "id": place.get("id"),
        "nombre": nombre,
        "direccion": direccion,
        # googleMapsUri es el enlace exacto a la ficha; si faltara, lo componemos
        "google_maps": place.get("googleMapsUri") or enlace_ficha_google(nombre, direccion),
        "web": place.get("websiteUri"),
        "telefono": place.get("internationalPhoneNumber") or place.get("nationalPhoneNumber"),
        "rating": place.get("rating"),
        "resenas": place.get("userRatingCount"),
        "estado_negocio": place.get("businessStatus"),
        "tipo": ", ".join(place.get("types", [])[:2]),
        "fuente": "google",
    }
