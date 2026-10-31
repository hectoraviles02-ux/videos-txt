# -*- coding: utf-8 -*-
"""Registra una pieza de TikTok que ya quedó PROGRAMADA a mano en TikTok Studio (30-oct-2026).

Uso (después de ver la publicación con el reloj azul en Studio > Publicaciones):
    python scripts/registrar_programado_studio.py <pieza_id> "AAAA-MM-DD HH:MM"

No sube nada. Pasa por la misma compuerta que cualquier publicación: exige la aprobación VIGENTE
de operador (botón APROBAR de Telegram) para ese mismo MP4 y texto. Deja la fila PROGRAMADA en la BD y
en el ledger, y después invalida la aprobación para que ningún script la vuelva a usar.
"""
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from content_factory import db, distribucion  # noqa: E402
from telegram_bot import aprobacion_botones as ab  # noqa: E402


def main(pieza_id: int, cuando: str) -> int:
    datetime.strptime(cuando, "%Y-%m-%d %H:%M")
    conn = db.conectar(db.RUTA_PRODUCCION, produccion=True)
    db.crear_esquema(conn)
    carpeta, pub, video = ab.localizar(conn, pieza_id)
    if video["canal"] != "tiktok":
        sys.exit("la pieza %d es de %s, no de TikTok" % (pieza_id, video["canal"]))

    def _ya_programada():
        return {"video_id": "studio:%d:%s" % (pieza_id, cuando.replace(" ", "T")),
                "url": "tiktok studio programado %s" % cuando}

    _ya_programada.ruta_mp4 = str(carpeta / video["archivo"])
    _ya_programada.texto = ab.texto_de(pub, video)
    _ya_programada.programada_para = cuando
    en = datetime.now().astimezone().isoformat()
    r = distribucion.publicar_pieza(conn, pieza_id, publicador=_ya_programada,
                                    ledger=distribucion.Ledger(ab.LEDGER), canal="tiktok", fecha=en)
    print(r)
    if r["resultado"] in ("programada", "ya_programada"):
        ap = db.aprobacion_vigente(conn, pieza_id)
        if ap is not None:
            db.invalidar_aprobacion(conn, int(ap["id"]), en, "ya programada en TikTok Studio para %s" % cuando)
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main(int(sys.argv[1]), sys.argv[2]))
