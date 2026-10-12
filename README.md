# Prospector de negocios sin web

Automatización para buscar negocios en una zona/código postal, comprobar si
tienen página web (según Google) y generar un mensaje personalizado + link
de WhatsApp listo para enviar con un clic.

Requiere **Python 3.10 o superior**.

## Instalación

Usa un entorno virtual en `.venv` con las dependencias de `requirements.txt`
(cómo crearlo, más abajo). Para usarlo (PowerShell, desde la carpeta del proyecto):

```powershell
.\.venv\Scripts\python.exe prospector.py --query "peluquerias" --zona "28038 Madrid"
```

O actívalo una vez y luego usa `python` a secas:

```powershell
.\.venv\Scripts\Activate.ps1
```

Si algún día lo montas de cero:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## De dónde salen los negocios

Hay dos fuentes y se elige con `--fuente`. Por defecto usa Google si tienes
API key configurada, y OpenStreetMap si no.

| | **OpenStreetMap** (`--fuente osm`) | **Google Places** (`--fuente google`) |
| --- | --- | --- |
| Coste / registro | Gratis, sin cuenta ni tarjeta | API key + facturación activada |
| Cobertura | Buena en calles principales, floja en negocios pequeños | Prácticamente todos |
| Teléfono | Solo si alguien lo mapeó (~1 de cada 10) | Casi siempre |
| "¿Tiene web?" | **No fiable**: si falta, puede ser que nadie la mapeó | Fiable |
| Valoraciones | No tiene | Sí (las usa `--usar-ia` en el mensaje) |

Traducido: **OSM sirve para empezar hoy sin gastar nada**, pero da pocos leads
por búsqueda y tendrás que comprobar a mano si el negocio ya tiene web. Google
da muchos más leads y ya filtrados de verdad, pero pide darse de alta.

Por eso el CSV lleva una columna `web_confirmada`: vale `si` con Google y `no`
con OSM.

## Configuración

**Con OpenStreetMap no hay que configurar nada.** Funciona tal cual.

Si algún día quieres la fuente de Google:

1. Entra en https://console.cloud.google.com/ y crea un proyecto.
2. Activa la facturación del proyecto (obligatorio aunque no llegues a
   pagar: hay crédito gratuito mensual, de sobra para uso personal).
3. En "APIs y servicios" habilita **Places API (New)** — la nueva, no la
   "Places API" antigua: esa ya no está disponible para proyectos nuevos.
4. En "Credenciales" crea una API key.
5. Copia `.env.example` a `.env` y pégala ahí:

```bash
cp .env.example .env
```

```
GOOGLE_PLACES_API_KEY=tu_key_de_google
ANTHROPIC_API_KEY=tu_key_de_anthropic   # opcional, solo si usas --usar-ia
NOMINATIM_CONTACT=tu@email.com          # opcional, buena práctica con OSM
```

El script carga el `.env` solo. Si prefieres variables de entorno normales
(`export ...`), también valen y tienen prioridad sobre el `.env`.

## Uso: la interfaz web

Es la forma cómoda de usarlo. Levanta un servidor local y abre la página:

```bash
.\.venv\Scripts\python.exe app.py
```

> Ojo en esta máquina: `python` y `python3` del PATH son los **alias de la
> Microsoft Store**, que no ejecutan nada (dicen "no se encontró Python" y
> salen). Usa siempre la ruta del `.venv` como arriba, o actívalo antes con
> `.\.venv\Scripts\Activate.ps1`.

Si cambias el código, **reinicia el servidor**: un proceso viejo sigue
sirviendo la versión antigua y parece que los arreglos no hacen efecto.

Desde ahí puedes buscar, **editar cada mensaje antes de enviarlo** (el enlace de
WhatsApp se actualiza solo mientras escribes), abrir la **ficha del negocio en
Google** para ver si ya tiene web, y marcar contactado o descartado sin tocar la
terminal. Los pendientes de búsquedas anteriores salen listados abajo.

El enlace a la ficha va también en `leads.csv` (columna `google_maps`) y en la
salida por consola. Con la fuente de Google es la URL exacta de la ficha; con
OpenStreetMap se compone una búsqueda de Maps con el nombre y la dirección, que
normalmente cae en la ficha correcta pero puede fallar en negocios con nombre
muy genérico.

Usa el mismo `leads_db.json` que la línea de comandos, así que puedes mezclar
las dos formas.

> Es un servidor **local**, sin contraseña: no lo expongas a internet, da acceso
> a tu histórico de contactos.

## Uso: línea de comandos

```bash
# Búsqueda básica (sin API key, vía OpenStreetMap)
python3 prospector.py --query "peluquerias" --zona "28038 Madrid" --max 20

# Sin tipo de negocio: cualquier negocio de la zona
python3 prospector.py --zona "Retiro, Madrid" --max 40

# Por barrio en vez de código postal (a OSM le van mejor los barrios con nombre)
python3 prospector.py --query "centros de estetica" --zona "Puente de Vallecas, Madrid"

# Con mensajes generados por Claude (más específicos, cuestan un poco de API)
python3 prospector.py --query "clinicas dentales" --zona "28018 Madrid" --usar-ia

# Forzando la fuente
python3 prospector.py --query "peluquerias" --zona "28038 Madrid" --fuente google
```

Con OSM la búsqueda es más precisa si el tipo de negocio está en la lista de
tipos conocidos (si no, busca por nombre, que falla más):

```bash
python3 prospector.py --listar-tipos
```

También puedes pasar la etiqueta de OSM directamente: `--query "shop=beauty"`.

Opciones:

| Opción | Qué hace |
| --- | --- |
| `--query` | Tipo de negocio. **Si lo omites, busca cualquier negocio de la zona** |
| `--zona` | Código postal o barrio (obligatorio) |
| `--fuente` | `osm`, `google` o `auto` (default: google si hay key, osm si no) |
| `--listar-tipos` | Muestra los tipos de negocio que OSM reconoce con precisión |
| `--max` | Máximo de negocios a analizar (default 20; Google no da más de 60 por búsqueda) |
| `--region` | Código de país para sesgar la búsqueda (default `ES`) |
| `--usar-ia` | Genera los mensajes con Claude en vez de la plantilla fija |
| `--modelo` | Modelo de Anthropic si usas `--usar-ia` (default `claude-haiku-4-5-20251001`) |
| `--prefijo` | Prefijo telefónico del país para los links (default `34`) |
| `--revisar-pendientes` | Vuelve a sacar los negocios que ya salieron pero aún no has contactado |

Resultado:
- `leads.csv`: negocios sin web encontrados, con teléfono, mensaje y link de WhatsApp.
- `leads_db.json`: histórico interno para no repetir negocios ya vistos entre ejecuciones.

## Qué negocios se descartan

De los resultados se filtran automáticamente:

- Los que **ya tienen web** (estado `tiene_web`).
- Los **cerrados permanentemente** o temporalmente (estado `descartado`;
  este dato solo lo da Google).
- Los que **no tienen teléfono público** (estado `sin_telefono`) — no hay
  forma de mandarles el WhatsApp. Para esos tendrías que buscar el contacto
  a mano (Instagram, formulario de la ficha de Google, etc.).

Con OSM esto último se lleva a la mayoría: en una búsqueda típica de barrio
puedes ver 70 peluquerías mapeadas y que solo 7 tengan teléfono. El script te
lo dice por pantalla y pone las aprovechables primero, para que `--max` no se
gaste en las que iba a descartar igualmente.

Todos quedan anotados en `leads_db.json`, así que en la siguiente búsqueda de
la misma zona ni siquiera se vuelven a consultar (no gastas cuota de API).

## Flujo recomendado

1. Corres el script para una zona/tipo de negocio.
2. Miras `leads.csv` o la salida por consola.
3. Para cada lead que te interese, abres el `whatsapp_link` (ya lleva el
   mensaje escrito) y le das a enviar tú mismo desde tu WhatsApp.
4. **Si vas por OSM**: antes de escribir, busca el nombre del negocio en Google
   5 segundos. Si resulta que sí tiene web, márcalo con `--descartar` y a otra
   cosa. Este paso no hace falta con la fuente de Google.
5. Cuando lo contactes, márcalo como hecho:

```bash
python3 marcar_contactado.py "Peluqueria Ana"
```

Así en la siguiente búsqueda de la misma zona no te vuelve a salir.

Otros comandos de gestión:

```bash
# Ver qué te queda por contactar
python3 marcar_contactado.py --pendientes

# Un negocio que no te interesa: fuera para siempre
python3 marcar_contactado.py "Bar Paco" --descartar
```

## Tests

```bash
.\.venv\Scripts\python.exe test_prospector.py
```

No usan red ni gastan API (tanto Google como OSM están simulados) y no tocan
tus `leads.csv` / `leads_db.json` reales.

## Ficheros

| Fichero | Qué es |
| --- | --- |
| `app.py` + `templates/` | La interfaz web |
| `prospector.py` | El núcleo: filtra, escribe mensajes y links, guarda el histórico |
| `fuentes.py` | De dónde salen los negocios (OpenStreetMap y Google Places) |
| `marcar_contactado.py` | Lo mismo que la web, desde la terminal |
| `test_prospector.py` | Tests, sin red ni claves |

La web y la terminal comparten el mismo núcleo (`procesar_busqueda` en
`prospector.py`), así que no hay dos versiones de la lógica que puedan
desincronizarse.

Variable de entorno útil: `PROSPECTOR_DATA_DIR` mueve `leads.csv` y
`leads_db.json` a otra carpeta (por defecto van junto al código).

## Por qué no envía mensajes solo

A propósito no envía nada automáticamente:

- **Legal**: la LSSICE regula las comunicaciones comerciales electrónicas no
  solicitadas en España; mandar centenares de mensajes automáticos sin
  ningún filtro humano es la forma más fácil de meterte en un lío o de que
  WhatsApp banee tu número por comportamiento de spam/bot.
- **Calidad**: revisar cada mensaje 2 segundos antes de mandarlo evita meteduras
  de pata (nombre mal puesto, negocio cerrado, etc.) y sube mucho la tasa de
  respuesta frente a un mensaje 100% genérico.

Si más adelante quieres subir el volumen, el siguiente paso natural es la
API oficial de WhatsApp Business (Meta) o un CRM tipo Twilio, pero eso ya
implica registro de empresa y aprobación de plantillas por Meta.

## Coste y límites

**OSM**: gratis. Nominatim y Overpass son servidores comunitarios y
compartidos, así que a veces responden `429` (saturados); el script reintenta
solo y prueba un espejo. Si haces muchas búsquedas seguidas te tocará esperar
un par de minutos: no los machaques.

**Google**: cada búsqueda es **una sola llamada** a Text Search (New), que ya
devuelve web, teléfono, rating y estado (la API antigua obligaba a una llamada
extra por cada negocio). Con el crédito gratuito mensual da para muchísimas
búsquedas de este tamaño. Máximo 60 resultados por búsqueda.

## Notas sobre datos de contacto

La Google Places API da teléfono con bastante fiabilidad, pero **no da emails**
casi nunca, así que el flujo va todo por WhatsApp.

`leads.csv` y `leads_db.json` llevan datos de contacto de negocios reales:
están en el `.gitignore` a propósito, no los subas a ningún repo público.
