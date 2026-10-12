#!/usr/bin/env python3
"""
prospector.py
==============

Automatización de prospección de clientes web para Dario.

Flujo:
  1. Buscas negocios por texto libre (tipo de negocio + zona/código postal).
     Dos fuentes posibles (ver fuentes.py):
       - OpenStreetMap: gratis, sin API key ni tarjeta. Es la de por defecto.
       - Google Places (New): mejores datos, necesita API key con facturación.
  2. Para cada negocio, comprueba si YA tiene página web.
  3. Si NO tiene web -> genera un mensaje personalizado (con Claude, o con una
     plantilla si no configuras la API de Anthropic) y te da un link de WhatsApp
     "click-to-chat" ya con el mensaje escrito (tú solo pulsas "Enviar").
  4. Guarda todo en leads.csv y en un leads_db.json para no repetir negocios
     entre ejecuciones (evita mandar el mensaje dos veces al mismo sitio).

IMPORTANTE (por diseño, no por limitación técnica):
  Este script NO envía nada automáticamente. Genera el mensaje y el link,
  pero el envío final lo haces tú con un clic. Esto es a propósito:
    - Cumple mejor con la LSSICE/RGPD (comunicación comercial revisada por un
      humano antes de salir, no espam masivo automatizado).
    - Evita que WhatsApp banee tu número por comportamiento de bot.
    - Te deja revisar/ajustar el mensaje antes de que lo vea el negocio.

Requisitos:
    Python 3.10 o superior.
    pip install requests
    (opcional, para mensajes con IA en vez de plantilla) pip install anthropic

Variables de entorno (todas opcionales; van en un .env junto a este script):
    GOOGLE_PLACES_API_KEY   -> solo si quieres usar --fuente google
    ANTHROPIC_API_KEY       -> solo si quieres usar --usar-ia
    NOMINATIM_CONTACT       -> email de contacto para las APIs de OSM (buena
                               práctica si haces muchas búsquedas)

Uso:
    python3 prospector.py --query "peluquerias" --zona "28038 Madrid" --max 20
    python3 prospector.py --query "centros de estetica" --zona "Vallecas Madrid"
    python3 prospector.py --query "clinicas dentales" --zona "28018 Madrid" --usar-ia
"""

import argparse
import csv
import json
import os
import re
import sys
import urllib.parse
from datetime import date
from pathlib import Path

import fuentes

BASE_DIR = Path(__file__).parent
# Por defecto los datos viven junto al código. PROSPECTOR_DATA_DIR permite
# moverlos (útil para pruebas, o si quieres el histórico en otra carpeta).
DATOS_DIR = Path(os.environ.get("PROSPECTOR_DATA_DIR") or BASE_DIR)
DB_PATH = DATOS_DIR / "leads_db.json"
CSV_PATH = DATOS_DIR / "leads.csv"
ENV_PATH = BASE_DIR / ".env"

CAMPOS_CSV = [
    "fecha",
    "nombre",
    "direccion",
    "telefono",
    "tipo",
    "rating",
    "zona",
    "fuente",
    "web_confirmada",
    "mensaje",
    "whatsapp_link",
    "google_maps",
]


# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------

def cargar_dotenv(path: Path = ENV_PATH) -> None:
    """Carga un .env sencillo (KEY=valor) sin dependencias externas.
    No pisa variables que ya estén definidas en el entorno."""
    if not path.exists():
        return
    for linea in path.read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if not linea or linea.startswith("#") or "=" not in linea:
            continue
        clave, _, valor = linea.partition("=")
        clave = clave.strip()
        valor = valor.strip().strip('"').strip("'")
        if clave and clave not in os.environ:
            os.environ[clave] = valor


# ---------------------------------------------------------------------------
# Persistencia simple para no repetir contactos entre ejecuciones
# ---------------------------------------------------------------------------

def load_db() -> dict:
    if not DB_PATH.exists():
        return {}
    try:
        return json.loads(DB_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"[!] {DB_PATH.name} está corrupto ({e}). Renómbralo o bórralo para empezar de cero.", file=sys.stderr)
        sys.exit(1)


def save_db(db: dict) -> None:
    """Escritura atómica: primero a un temporal, luego reemplaza. Así un Ctrl+C
    a media escritura no deja el histórico a medias."""
    tmp = DB_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(db, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(DB_PATH)


# ---------------------------------------------------------------------------
# Elección de fuente
# ---------------------------------------------------------------------------

def elegir_fuente(pedida: str, google_key: str | None) -> str:
    """'auto' usa Google si hay API key (mejores datos) y OSM si no."""
    if pedida != "auto":
        return pedida
    return "google" if google_key else "osm"


def buscar_negocios(fuente: str, args, google_key: str | None, log=print) -> list[dict]:
    if fuente == "google":
        return fuentes.buscar_google(args.query, args.zona, google_key, args.max, args.region)
    return fuentes.buscar_osm(args.query, args.zona, args.max, args.region,
                              os.environ.get("NOMINATIM_CONTACT"), log)


# ---------------------------------------------------------------------------
# Generación de mensaje personalizado
# ---------------------------------------------------------------------------

def generar_mensaje_plantilla(nombre: str, tipo: str, zona: str) -> str:
    return (
        f"Hola! Soy Dario, desarrollador web freelance aquí en {zona}. "
        f"Vi que {nombre} no tiene página propia todavía y me gustaría ofrecerte una web "
        f"rápida, moderna y optimizada para que te encuentren en Google (sin permanencias). "
        f"¿Te viene bien que te pase una propuesta con un ejemplo hecho a medida para tu negocio?"
    )


def generar_mensaje_ia(
    nombre: str,
    tipo: str,
    zona: str,
    rating: float | None,
    resenas: int | None,
    api_key: str,
    modelo: str,
) -> str:
    """Usa la API de Anthropic para generar un mensaje más específico.
    Si falla por cualquier motivo, cae a la plantilla."""
    try:
        from anthropic import Anthropic

        client = Anthropic(api_key=api_key)
        if rating and resenas:
            contexto_rating = (
                f"En Google tiene {rating} estrellas con {resenas} reseñas: puedes mencionarlo de "
                f"forma natural como argumento de que ya tiene buena reputación y solo le falta la web. "
            )
        else:
            contexto_rating = ""
        prompt = (
            f"Escribe un mensaje corto de WhatsApp (máximo 4 frases, tono cercano y profesional, "
            f"en español de España, sin emojis excesivos, máximo 1 si acaso) de Dario, desarrollador "
            f"web freelance en {zona}, dirigido al negocio '{nombre}' (tipo: {tipo}). "
            f"El negocio no tiene página web propia según Google. {contexto_rating}"
            f"El objetivo es ofrecer crear una "
            f"página web para captar más clientes/reservas, y proponer una llamada o enviar un ejemplo "
            f"de propuesta. No inventes datos del negocio que no tengas. No uses la palabra 'oferta' "
            f"en mayúsculas ni suene a spam. Devuelve solo el texto del mensaje, sin comillas."
        )
        resp = client.messages.create(
            model=modelo,
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )
        texto = "".join(block.text for block in resp.content if block.type == "text").strip()
        return texto or generar_mensaje_plantilla(nombre, tipo, zona)
    except Exception as e:  # noqa: BLE001
        print(f"  [!] Fallback a plantilla (IA no disponible: {e})", file=sys.stderr)
        return generar_mensaje_plantilla(nombre, tipo, zona)


def normalizar_telefono(telefono: str, prefijo_pais: str) -> str:
    """Devuelve el número en el formato que quiere wa.me: solo dígitos, con
    prefijo de país y sin el '+' ni el '00' inicial.

    Google devuelve normalmente `international_phone_number` (empieza por '+'),
    pero si solo hay teléfono nacional le anteponemos el prefijo del país.
    """
    # Si vienen varios números en el mismo campo (pasa en OSM), nos quedamos con el primero
    telefono = re.split(r"[;,]", telefono)[0].strip()
    digitos = re.sub(r"\D", "", telefono)
    if telefono.startswith("+"):
        return digitos
    if digitos.startswith("00"):  # prefijo internacional escrito a la vieja usanza
        return digitos[2:]
    return prefijo_pais + digitos.lstrip("0")


def link_whatsapp(telefono: str, mensaje: str, prefijo_pais: str) -> str:
    numero = normalizar_telefono(telefono, prefijo_pais)
    return f"https://wa.me/{numero}?text={urllib.parse.quote(mensaje)}"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def escribir_leads_csv(leads: list[dict]) -> None:
    """Añade los leads al CSV. Escribe la cabecera si el fichero no existe o
    está vacío, y usa columnas fijas para que el CSV sea consistente entre
    ejecuciones."""
    necesita_cabecera = not CSV_PATH.exists() or CSV_PATH.stat().st_size == 0
    with open(CSV_PATH, "a", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=CAMPOS_CSV, extrasaction="ignore")
        if necesita_cabecera:
            writer.writeheader()
        writer.writerows(leads)


# ---------------------------------------------------------------------------
# Núcleo: lo comparten la línea de comandos (main) y la app web (app.py)
# ---------------------------------------------------------------------------

class Busqueda:
    """Parámetros de una búsqueda. Equivale a los argumentos del CLI."""

    def __init__(self, query, zona, fuente="auto", max=20, region="ES", prefijo="34",
                 usar_ia=False, modelo="claude-haiku-4-5-20251001", revisar_pendientes=False):
        self.query = query
        self.zona = zona
        self.fuente = fuente
        self.max = max
        self.region = region
        self.prefijo = prefijo
        self.usar_ia = usar_ia
        self.modelo = modelo
        self.revisar_pendientes = revisar_pendientes


def procesar_busqueda(args, log=print) -> dict:
    """Busca, filtra, genera mensajes y persiste. Devuelve los leads nuevos y
    un resumen de por qué se descartó el resto.

    `log` permite a quien llame decidir dónde va la traza (consola, o nada).
    """
    cargar_dotenv()

    google_key = os.environ.get("GOOGLE_PLACES_API_KEY")
    fuente = elegir_fuente(args.fuente, google_key)

    if fuente == "google":
        if not google_key:
            raise ValueError(
                "La fuente Google necesita GOOGLE_PLACES_API_KEY en el .env. "
                "Si aún no tienes key, usa la fuente OpenStreetMap: es gratis y no requiere registro."
            )
        args.max = min(args.max, fuentes.MAX_TOTAL_GOOGLE)

    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    usar_ia = args.usar_ia and bool(anthropic_key)
    if args.usar_ia and not usar_ia:
        log("  [!] Los mensajes con IA necesitan ANTHROPIC_API_KEY. Se usa la plantilla fija.")

    db = load_db()

    # Estados que damos por cerrados: no se vuelven a escribir en el CSV.
    # 'pendiente_revision' entra aquí también para no duplicar leads que ya están
    # esperando revisión; con revisar_pendientes se regeneran.
    estados_cerrados = {"contactado", "tiene_web", "sin_telefono", "descartado"}
    if not args.revisar_pendientes:
        estados_cerrados.add("pendiente_revision")

    origen = "OpenStreetMap" if fuente == "osm" else "Google Places"
    que = f"'{args.query}'" if args.query.strip() else "cualquier negocio"
    log(f"Buscando {que} en '{args.zona}' vía {origen} (máx {args.max})...")
    negocios = buscar_negocios(fuente, args, google_key, log)
    log(f"  -> {len(negocios)} negocios encontrados.")

    leads_nuevos = []
    conteo = {"conocidos": 0, "con_web": 0, "sin_telefono": 0, "cerrados": 0}
    hoy = date.today().isoformat()
    # Solo Google confirma de verdad si un negocio tiene web; en OSM la ausencia
    # de la etiqueta puede significar simplemente que nadie la ha mapeado.
    web_confirmada = fuente == "google"

    for d in negocios:
        neg_id = d["id"]
        nombre = d["nombre"]
        if not neg_id:
            continue

        if db.get(neg_id, {}).get("estado") in estados_cerrados:
            conteo["conocidos"] += 1
            continue  # ya procesado en una ejecución anterior

        if d["estado_negocio"] not in (None, "OPERATIONAL"):
            db[neg_id] = {"nombre": nombre, "estado": "descartado", "motivo": d["estado_negocio"]}
            conteo["cerrados"] += 1
            continue

        if d["web"]:
            db[neg_id] = {"nombre": nombre, "estado": "tiene_web", "web": d["web"]}
            conteo["con_web"] += 1
            continue

        if not d["telefono"]:
            db[neg_id] = {"nombre": nombre, "estado": "sin_telefono"}
            conteo["sin_telefono"] += 1
            continue

        if usar_ia:
            mensaje = generar_mensaje_ia(nombre, d["tipo"], args.zona, d["rating"], d["resenas"],
                                         anthropic_key, args.modelo)
        else:
            mensaje = generar_mensaje_plantilla(nombre, d["tipo"], args.zona)

        lead = {
            "id": neg_id,
            "fecha": hoy,
            "nombre": nombre,
            "direccion": d["direccion"],
            "telefono": d["telefono"],
            "tipo": d["tipo"],
            "rating": d["rating"],
            "zona": args.zona,
            "fuente": d["fuente"],
            "web_confirmada": "si" if web_confirmada else "no",
            "mensaje": mensaje,
            "whatsapp_link": link_whatsapp(d["telefono"], mensaje, args.prefijo),
            "google_maps": d.get("google_maps", ""),
        }
        leads_nuevos.append(lead)

        db[neg_id] = {
            "nombre": nombre,
            "estado": "pendiente_revision",
            "telefono": d["telefono"],
            "direccion": d["direccion"],
            "zona": args.zona,
            "fuente": d["fuente"],
            "mensaje": mensaje,
            "whatsapp_link": lead["whatsapp_link"],
            "google_maps": lead["google_maps"],
            "fecha": hoy,
        }

    save_db(db)
    if leads_nuevos:
        escribir_leads_csv(leads_nuevos)

    return {
        "leads": leads_nuevos,
        "conteo": conteo,
        "fuente": fuente,
        "encontrados": len(negocios),
        "web_confirmada": web_confirmada,
    }


def marcar_estado(identificador: str, estado: str, por_id: bool = False) -> list[dict]:
    """Marca uno o varios negocios ('contactado', 'descartado'...). Si por_id
    es True el identificador es la clave exacta; si no, se busca por nombre."""
    db = load_db()
    if por_id:
        encontrados = [identificador] if identificador in db else []
    else:
        buscado = identificador.lower()
        encontrados = [k for k, v in db.items() if buscado in v.get("nombre", "").lower()]

    for clave in encontrados:
        db[clave]["estado"] = estado

    if encontrados:
        save_db(db)
    return [{"id": k, **db[k]} for k in encontrados]


def listar_pendientes(prefijo: str = "34") -> list[dict]:
    """Negocios ya encontrados que siguen esperando a que los contactes.

    Los registros de versiones antiguas no guardaban el mensaje ni el link, así
    que se regeneran al vuelo para que la interfaz pueda mostrarlos igual."""
    db = load_db()
    pendientes = []
    for clave, info in db.items():
        if info.get("estado") != "pendiente_revision":
            continue
        lead = {"id": clave, **info}
        if not lead.get("mensaje"):
            lead["mensaje"] = generar_mensaje_plantilla(
                lead.get("nombre", ""), lead.get("tipo", ""), lead.get("zona", ""))
        if not lead.get("whatsapp_link") and lead.get("telefono"):
            lead["whatsapp_link"] = link_whatsapp(lead["telefono"], lead["mensaje"], prefijo)
        if not lead.get("google_maps"):
            lead["google_maps"] = fuentes.enlace_ficha_google(
                lead.get("nombre", ""), lead.get("direccion") or lead.get("zona", ""))
        pendientes.append(lead)

    return sorted(pendientes, key=lambda i: (i.get("fecha", ""), i.get("nombre", "")))


# ---------------------------------------------------------------------------
# Línea de comandos
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Prospector de negocios sin página web")
    parser.add_argument("--query", default="",
                        help='Tipo de negocio, ej: "peluquerias". Si lo omites, busca '
                             'cualquier negocio de la zona')
    parser.add_argument("--zona", help='Código postal o barrio, ej: "28038 Madrid" o "Vallecas Madrid"')
    parser.add_argument("--fuente", choices=("auto", "osm", "google"), default="auto",
                        help="De dónde sacar los negocios: osm (gratis, sin API key) o google "
                             "(mejores datos, requiere key). Por defecto: google si hay key, osm si no")
    parser.add_argument("--listar-tipos", action="store_true",
                        help="Muestra los tipos de negocio que la fuente OSM entiende con precisión y sale")
    parser.add_argument("--max", type=int, default=20, help="Máximo de negocios a analizar (default 20)")
    parser.add_argument("--region", default="ES", help="Código de país para sesgar la búsqueda (default ES)")
    parser.add_argument("--usar-ia", action="store_true", help="Generar mensajes con Claude en vez de plantilla fija")
    parser.add_argument("--modelo", default="claude-haiku-4-5-20251001", help="Modelo de Anthropic a usar si --usar-ia")
    parser.add_argument("--prefijo", default="34", help="Prefijo telefónico del país para los links de WhatsApp (default 34, España)")
    parser.add_argument("--revisar-pendientes", action="store_true",
                        help="Vuelve a incluir negocios ya vistos que siguen pendientes de contactar")
    args = parser.parse_args()

    if args.listar_tipos:
        print("Tipos que la fuente OSM reconoce (cualquier otra cosa se busca por nombre):\n")
        for clave in sorted(fuentes.TIPOS_OSM):
            etiquetas = ", ".join(f"{k}={v}" for k, v in fuentes.TIPOS_OSM[clave])
            print(f"  {clave:<20} -> {etiquetas}")
        print('\nTambién puedes pasar la etiqueta directamente: --query "shop=beauty"')
        return

    if not args.zona:
        parser.error("--zona es obligatorio (o usa --listar-tipos)")

    try:
        # flush para que los avisos de error (stderr) no salgan desordenados
        resultado = procesar_busqueda(args, log=lambda t: print(t, flush=True))
    except (ValueError, fuentes.FuenteNoDisponible) as e:
        print(f"\n{e}", file=sys.stderr)
        sys.exit(1)

    leads_nuevos = resultado["leads"]
    conteo = resultado["conteo"]

    descartes = [
        (conteo["conocidos"], "ya estaban en el histórico"),
        (conteo["con_web"], "ya tienen web"),
        (conteo["sin_telefono"], "sin teléfono localizable"),
        (conteo["cerrados"], "cerrados"),
    ]
    resumen = ", ".join(f"{n} {motivo}" for n, motivo in descartes if n)
    if resumen:
        print(f"  -> Descartados: {resumen}.")

    if not leads_nuevos:
        print("\nNingún negocio nuevo sin web en esta búsqueda. Prueba otra zona u otro tipo de negocio.")
        if resultado["fuente"] == "osm":
            print("Si la zona debería tener más negocios, puede que estén poco mapeados en "
                  "OpenStreetMap: prueba --listar-tipos o una zona más amplia.")
        return

    print(f"\n{len(leads_nuevos)} negocios sin web encontrados y añadidos a {CSV_PATH.name}:\n")
    for lead in leads_nuevos:
        print(f"- {lead['nombre']} ({lead['direccion'] or 'sin dirección'})")
        print(f"  Tel: {lead['telefono']}")
        print(f"  Ficha en Google: {lead['google_maps']}")
        print(f"  Mensaje: {lead['mensaje']}")
        print(f"  WhatsApp: {lead['whatsapp_link']}\n")

    if not resultado["web_confirmada"]:
        print(
            "AVISO: con la fuente OSM, 'sin web' significa que en OpenStreetMap no hay\n"
            "web mapeada, no que el negocio no la tenga. Antes de escribir a uno, busca\n"
            "su nombre en Google 5 segundos: si ya tiene web, márcalo con --descartar.\n"
        )

    print(
        "Estos negocios quedan como 'pendiente_revision' en leads_db.json.\n"
        "Cuando contactes a uno, márcalo para que no vuelva a salir:\n"
        '    python3 marcar_contactado.py "Nombre del negocio"'
    )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nCancelado.", file=sys.stderr)
        sys.exit(130)
