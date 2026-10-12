#!/usr/bin/env python3
"""
app.py
======

Interfaz web del prospector. Levanta un servidor local y abre una página
donde buscar negocios, revisar los mensajes uno a uno (editándolos si quieres)
y abrir el WhatsApp de cada uno con un clic.

Usa exactamente la misma lógica que la línea de comandos (prospector.py) y el
mismo histórico (leads_db.json), así que puedes mezclar ambas cosas.

Uso:
    python3 app.py            -> http://127.0.0.1:5000
    python3 app.py --puerto 8080

Es un servidor LOCAL, pensado para tu máquina. No lo expongas a internet:
no tiene autenticación y da acceso a tu histórico de contactos.
"""

import argparse
import os
import webbrowser

from flask import Flask, jsonify, render_template, request

import fuentes
import prospector

app = Flask(__name__)
# Sin esto, Flask cachea la plantilla y los retoques de la interfaz no se ven
# hasta reiniciar el servidor.
app.config["TEMPLATES_AUTO_RELOAD"] = True


@app.get("/")
def inicio():
    return render_template("index.html")


@app.get("/api/config")
def config():
    """Lo que la interfaz necesita saber para pintarse: qué fuentes hay
    disponibles y si se pueden generar mensajes con IA."""
    prospector.cargar_dotenv()
    return jsonify({
        "hay_key_google": bool(os.environ.get("GOOGLE_PLACES_API_KEY")),
        "hay_key_anthropic": bool(os.environ.get("ANTHROPIC_API_KEY")),
        "tipos": sorted(fuentes.TIPOS_OSM),
        "max_google": fuentes.MAX_TOTAL_GOOGLE,
    })


@app.post("/api/buscar")
def buscar():
    datos = request.get_json(silent=True) or {}
    # El tipo de negocio puede ir vacío: eso significa "cualquier negocio de la zona"
    query = (datos.get("query") or "").strip()
    zona = (datos.get("zona") or "").strip()
    if not zona:
        return jsonify({"error": "Hace falta la zona."}), 400

    try:
        maximo = max(1, min(int(datos.get("max", 20)), 60))
    except (TypeError, ValueError):
        maximo = 20

    busqueda = prospector.Busqueda(
        query=query,
        zona=zona,
        fuente=datos.get("fuente", "auto"),
        max=maximo,
        region=(datos.get("region") or "ES").upper(),
        prefijo=str(datos.get("prefijo") or "34"),
        usar_ia=bool(datos.get("usar_ia")),
        revisar_pendientes=bool(datos.get("revisar_pendientes")),
    )

    # La traza que el CLI imprime la recogemos para enseñarla en la página:
    # ahí es donde salen los avisos de "Overpass saturado" o "solo 7 tienen teléfono".
    traza = []
    try:
        resultado = prospector.procesar_busqueda(busqueda, log=traza.append)
    except ValueError as e:                          # configuración incompleta
        return jsonify({"error": str(e)}), 400
    except fuentes.FuenteNoDisponible as e:          # la fuente falló: no es "no hay nada"
        return jsonify({"error": str(e), "traza": traza}), 503
    except Exception as e:                           # noqa: BLE001 - que la página no se cuelgue
        app.logger.exception("fallo en la búsqueda")
        return jsonify({"error": f"Fallo inesperado: {e}"}), 500

    resultado["traza"] = traza
    return jsonify(resultado)


@app.post("/api/estado")
def cambiar_estado():
    datos = request.get_json(silent=True) or {}
    identificador = datos.get("id")
    estado = datos.get("estado")

    if estado not in ("contactado", "descartado", "pendiente_revision"):
        return jsonify({"error": f"Estado no válido: {estado}"}), 400
    if not identificador:
        return jsonify({"error": "Falta el id del negocio."}), 400

    afectados = prospector.marcar_estado(identificador, estado, por_id=True)
    if not afectados:
        return jsonify({"error": "Ese negocio ya no está en el histórico."}), 404
    return jsonify({"ok": True, "negocios": afectados})


@app.get("/api/pendientes")
def pendientes():
    return jsonify({"pendientes": prospector.listar_pendientes()})


def main():
    parser = argparse.ArgumentParser(description="Interfaz web del prospector")
    parser.add_argument("--puerto", type=int, default=5000)
    parser.add_argument("--sin-abrir", action="store_true", help="No abrir el navegador solo")
    args = parser.parse_args()

    url = f"http://127.0.0.1:{args.puerto}"
    print(f"Prospector en marcha: {url}   (Ctrl+C para parar)")
    if not args.sin_abrir:
        webbrowser.open(url)

    # use_reloader=False para no abrir el navegador dos veces ni duplicar el proceso
    app.run(host="127.0.0.1", port=args.puerto, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
