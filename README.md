# videos-txt

Herramientas personales para convertir fuentes (videos, artículos, PDF) en texto trazable y producir reels cortos con datos verificables.

- **Extracción** (`scripts/procesar_url.py`, `procesar_video.py`, `procesar_texto.py`): URL → Markdown + JSON con método, confianza y marcas de tiempo. Nada se inventa: lo que no está en la fuente se marca `[PENDIENTE]`.
- **Motor «foto real»** (`scripts/foto_real/`): busca fotos y videos con licencia libre en Wikimedia Commons (lee la licencia de cada archivo, descarta NC/ND y retratos con restricción), y arma reels 9:16 cortados al ritmo de la voz, con el crédito de cada imagen en pantalla.
- **Pizarra, expediente 3D y otros motores** (`scripts/`), fábrica de contenidos (`content_factory/`), bot de aprobación por Telegram (`telegram_bot/`).

## Credenciales

Ninguna credencial vive en el código. Todo se lee de variables de entorno (archivo `.env`, que **no** se versiona). Copia `.env.example` a `.env` y pon tus propias llaves.

## Qué no está aquí

Este repositorio publica solo código y pruebas. No incluye originales descargados, extracciones de terceros, guiones, voces, videos, bases de datos ni registros de uso. Algunos módulos internos que mencionan casos o instituciones específicas se omitieron a propósito, así que ciertas importaciones pueden faltar.
