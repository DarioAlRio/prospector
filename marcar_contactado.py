#!/usr/bin/env python3
"""
Gestiona el estado de los negocios en leads_db.json para que prospector.py
no te los vuelva a sacar en próximas búsquedas.

Uso:
    python3 marcar_contactado.py "Peluqueria Ana"        # -> contactado
    python3 marcar_contactado.py "Peluqueria Ana" --descartar
    python3 marcar_contactado.py --pendientes            # lista lo que falta por contactar
"""
import argparse
import sys

import prospector

DB_PATH = prospector.DB_PATH


def mostrar_pendientes() -> None:
    pendientes = prospector.listar_pendientes()
    if not pendientes:
        print("No hay negocios pendientes de contactar.")
        return
    print(f"{len(pendientes)} negocios pendientes de contactar:\n")
    for info in pendientes:
        fecha = info.get("fecha", "?")
        zona = info.get("zona", "?")
        print(f"- {info.get('nombre', '(sin nombre)')}  [{info.get('telefono', 'sin tel')}]  {zona}  ({fecha})")


def main():
    parser = argparse.ArgumentParser(description="Marca negocios como contactados en leads_db.json")
    parser.add_argument("nombre", nargs="?", help="Nombre o parte del nombre del negocio")
    parser.add_argument("--descartar", action="store_true",
                        help="Márcalo como descartado en vez de contactado (no te interesa)")
    parser.add_argument("--pendientes", action="store_true",
                        help="Lista los negocios pendientes de contactar y sale")
    args = parser.parse_args()

    if not DB_PATH.exists():
        print(f"No existe {DB_PATH.name} todavía. Haz una búsqueda primero.", file=sys.stderr)
        sys.exit(1)

    if args.pendientes:
        mostrar_pendientes()
        return

    if not args.nombre:
        parser.error("indica el nombre del negocio, o usa --pendientes para ver la lista")

    nuevo_estado = "descartado" if args.descartar else "contactado"
    db = prospector.load_db()
    buscado = args.nombre.lower()
    encontrados = [pid for pid, info in db.items() if buscado in info.get("nombre", "").lower()]

    if not encontrados:
        print(f"No se encontró ningún negocio que contenga '{args.nombre}' en {DB_PATH.name}.", file=sys.stderr)
        sys.exit(1)

    if len(encontrados) > 1:
        print(f"'{args.nombre}' coincide con {len(encontrados)} negocios:")
        for pid in encontrados:
            print(f"  - {db[pid].get('nombre')} (estado actual: {db[pid].get('estado')})")
        respuesta = input(f"¿Marcar los {len(encontrados)} como '{nuevo_estado}'? [s/N] ").strip().lower()
        if respuesta not in ("s", "si", "sí", "y", "yes"):
            print("Cancelado. Afina el nombre para que coincida con uno solo.")
            return

    for info in prospector.marcar_estado(args.nombre, nuevo_estado):
        print(f"Marcado como {nuevo_estado}: {info.get('nombre')}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nCancelado.", file=sys.stderr)
        sys.exit(130)
