# videos-txt

Herramientas personales para convertir fuentes (videos, artículos, PDF) en texto trazable y producir reels cortos con datos verificables.

- **Extracción** (`scripts/procesar_url.py`, `procesar_video.py`, `procesar_texto.py`): URL → Markdown + JSON con método, confianza y marcas de tiempo. Nada se inventa: lo que no está en la fuente se marca `[PENDIENTE]`.
- **Motor «foto real»** (`scripts/foto_real/`): busca fotos y videos con licencia libre en Wikimedia Commons (lee la licencia de cada archivo, descarta NC/ND y retratos con restricción), y arma reels 9:16 cortados al ritmo de la voz, con el crédito de cada imagen en pantalla.
- **Pizarra, expediente 3D y otros motores** (`scripts/`), fábrica de contenidos (`content_factory/`), bot de aprobación por Telegram (`telegram_bot/`).

## Credenciales

Ninguna credencial vive en el código. Todo se lee de variables de entorno (archivo `.env`, que **no** se versiona). Copia `.env.example` a `.env` y pon tus propias llaves.

## Qué no está aquí

Este repositorio publica solo código y pruebas. No incluye originales descargados, extracciones de terceros, guiones, voces, videos, bases de datos ni registros de uso. Algunos módulos internos que mencionan casos o instituciones específicas se omitieron a propósito, así que ciertas importaciones pueden faltar.

## Estado (31-oct-2026)

Es una publicación parcial de un proyecto personal. Medido en esta copia: 650 pruebas pasan, 51 fallan y 35 dan error,
casi todas porque faltan módulos internos omitidos (por ejemplo `telegram_bot/handlers.py`, `synthesis/gate_fidelidad.py`,
`content_factory/topics/scorer.py`) o archivos de datos locales que no se publican. Lo que sí funciona solo:
`scripts/foto_real/` (medios con licencia y motor de render), los extractores de `scripts/procesar_*.py` y la mayor parte de
`content_factory/`. Algunos motores necesitan entornos aparte (Chatterbox para la voz, Blender para el expediente 3D).

## Licencia

MIT. Ver `LICENSE`. Las fotos y videos que el motor descarga conservan su propia licencia (Wikimedia Commons) y su crédito
se muestra en pantalla.
