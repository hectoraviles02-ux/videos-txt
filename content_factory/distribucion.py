# -*- coding: utf-8 -*-
"""Distribucion trazable: publica una PIEZA y la registra en content.db (E2).

Cambio SDD: content-factory-produccion-y-distribucion.

Flujo seguro con ESTADOS DE PUBLICACION (distintos del estado de la pieza):

  reconciliar-o-saltar (ANTES de llamar al canal)
    - publicaciones.estado == CONFIRMADA                 -> ya_publicada
    - publicaciones.estado == RECONCILIACION_PENDIENTE   -> reconciliar (sin llamar al canal)
    - hay evidencia remota en el ledger pero no en BD    -> reconciliar desde ledger (sin canal)
  si no hay evidencia remota:
    - pieza PRODUCIDA|LISTA, API confirma video_id
    - LEDGER append-only (evidencia remota durable, ANTES de tocar la BD)
    - [PRODUCIDA->LISTA] -> publicaciones(RECONCILIACION_PENDIENTE,id,url) -> LISTA->PUBLICADA
      -> publicaciones.estado = CONFIRMADA

PROPIEDAD CLAVE (demostrada en pruebas): un fallo local POSTERIOR al exito remoto
NUNCA produce una segunda llamada al canal: la evidencia remota (ledger / fila
RECONCILIACION_PENDIENTE) se consulta ANTES de publicar, y en ese caso se RECONCILIA,
no se republica.

v5 (Fase A de la fabrica editorial):
  - COMPUERTA DE APROBACION: antes de llamar al canal se exige una aprobacion VIGENTE
    de operador que cubra el archivo, el texto, el canal y el horario QUE EL PUBLICADOR
    DECLARA (atributos `ruta_mp4`, `texto`, `programada_para` del callable). Un
    publicador que no los declara se rechaza: no se puede verificar que sube.
  - PROGRAMADA != PUBLICADA: si el publicador programa, la fila queda PROGRAMADA y la
    pieza NO pasa a PUBLICADA hasta `confirmar_programada` con evidencia.
  - REINTENTO INSEGURO: un error del canal que no sea `RechazoDelCanal` (p. ej. se
    corto la conexion) puede haber publicado. Queda fila PENDIENTE y NO se reintenta
    hasta `liberar_intento` con evidencia de que no salio.

El publicador es inyectable (mock en pruebas; adaptador real en produccion). Este
modulo NO importa el publicador real ni publica nada por si mismo.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from content_factory import db

CANAL_DEFECTO = "facebook"


class PublicacionError(RuntimeError):
    """La pieza no esta en un estado publicable."""


class SinAprobacionValida(PublicacionError):
    """No hay aprobacion vigente de operador que cubra lo que el publicador va a subir."""


class RechazoDelCanal(RuntimeError):
    """El canal rechazo SIN publicar (seguro reintentar). Lo lanza el publicador.

    Cualquier otra excepcion se trata como resultado INCIERTO: pudo haber publicado.
    """


class Ledger:
    """Registro append-only de confirmaciones remotas (evidencia durable).

    Se escribe ANTES de tocar la BD; si la BD falla, la siguiente corrida lo ve y
    RECONCILIA en vez de republicar. Conserva id remoto y url/permalink.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def _registros(self):
        if not self.path.is_file():
            return []
        out = []
        for ln in self.path.read_text(encoding="utf-8").splitlines():
            if ln.strip():
                try:
                    out.append(json.loads(ln))
                except ValueError:
                    pass
        return out

    def obtener(self, pieza_id: int, canal: str):
        """Ultimo registro remoto para esta pieza/canal, o None."""
        hit = None
        for r in self._registros():
            if r.get("pieza_id") == pieza_id and r.get("canal") == canal:
                hit = r
        return hit

    def ya_anotada(self, pieza_id: int, canal: str) -> bool:
        return self.obtener(pieza_id, canal) is not None

    def anota(self, pieza_id: int, canal: str, identificador_remoto: str,
              url: str | None = None, programada_para: str | None = None) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        rec = {"pieza_id": pieza_id, "canal": canal,
               "identificador_remoto": identificador_remoto, "url": url,
               "ts": time.strftime("%Y-%m-%d %H:%M:%S")}
        if programada_para:
            rec["programada_para"] = programada_para
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _estado_pieza(conn, pieza_id):
    fila = conn.execute("SELECT estado FROM piezas WHERE id = ?", (pieza_id,)).fetchone()
    return fila["estado"] if fila is not None else None


def _asegurar_publicada(conn, pieza_id: int, fecha: str, detalle: str) -> None:
    """Transiciona la pieza hasta PUBLICADA pasando por LISTA si hace falta.

    Idempotente: si ya esta PUBLICADA, no hace nada. Nunca salta pasos.
    """
    estado = _estado_pieza(conn, pieza_id)
    if estado == "PUBLICADA":
        return
    if estado == "PRODUCIDA":
        db.transicionar(conn, pieza_id, "PRODUCIDA", "LISTA", fecha,
                        "lista para distribucion: artefacto renderizado presente")
        estado = "LISTA"
    if estado == "LISTA":
        db.transicionar(conn, pieza_id, "LISTA", "PUBLICADA", fecha, detalle)
        return
    raise PublicacionError(
        "no se puede llevar la pieza %d a PUBLICADA desde %s" % (pieza_id, estado))


def reconciliar(conn, pieza_id: int, *, canal: str = CANAL_DEFECTO, fecha: str,
                identificador_remoto: str | None = None, url: str | None = None,
                programada_para: str | None = None) -> dict:
    """Completa en BD una publicacion que ya ocurrio en el canal. NO llama al canal.

    - Si no hay fila de publicacion pero se pasa `identificador_remoto` (evidencia
      del ledger), crea la fila en RECONCILIACION_PENDIENTE.
    - Lleva la pieza a PUBLICADA (via LISTA) y marca la publicacion CONFIRMADA.
    - Idempotente: si ya esta CONFIRMADA y la pieza PUBLICADA, no repite nada.
    """
    pub = db.obtener_publicacion(conn, pieza_id, canal)
    if pub is None:
        if not identificador_remoto:
            raise PublicacionError(
                "no hay nada que reconciliar para la pieza %d: ni fila ni id remoto" % pieza_id)
        db.registrar_publicacion(
            conn, pieza_id=pieza_id, canal=canal, publicada_en=fecha,
            identificador_remoto=identificador_remoto, url=url,
            estado_pub=db.PUB_RECONCILIACION_PENDIENTE, programada_para=programada_para)
        pub = db.obtener_publicacion(conn, pieza_id, canal)

    if pub["programada_para"]:
        # el canal la acepto PROGRAMADA: se completa la BD, pero NO es publicada.
        if _estado_pieza(conn, pieza_id) == "PRODUCIDA":
            db.transicionar(conn, pieza_id, "PRODUCIDA", "LISTA", fecha,
                            "lista: programada en %s para %s" % (canal, pub["programada_para"]))
        if pub["estado"] != db.PUB_PROGRAMADA:
            db.actualizar_estado_publicacion(conn, pieza_id, canal, db.PUB_PROGRAMADA)
        return {"resultado": "programada_reconciliada", "pieza_id": pieza_id,
                "identificador_remoto": pub["identificador_remoto"],
                "programada_para": pub["programada_para"]}

    if pub["estado"] == db.PUB_CONFIRMADA and _estado_pieza(conn, pieza_id) == "PUBLICADA":
        return {"resultado": "ya_reconciliada", "pieza_id": pieza_id,
                "identificador_remoto": pub["identificador_remoto"]}

    _asegurar_publicada(conn, pieza_id, fecha,
                        "publicado en %s con video_id=%s (reconciliado)"
                        % (canal, pub["identificador_remoto"]))
    db.actualizar_estado_publicacion(conn, pieza_id, canal, db.PUB_CONFIRMADA)
    return {"resultado": "reconciliada", "pieza_id": pieza_id,
            "identificador_remoto": pub["identificador_remoto"]}


def _exigir_aprobacion(conn, pieza_id: int, publicador, canal: str, fecha: str) -> None:
    """Compuerta v5. Rechaza (y deja evento) si la aprobacion no cubre el envio."""
    ruta = getattr(publicador, "ruta_mp4", None)
    texto = getattr(publicador, "texto", None)
    if not ruta or texto is None:
        motivo = "el publicador no declara que archivo y que texto sube: no se puede verificar"
    else:
        v = db.verificar_aprobacion(conn, pieza_id, ruta_mp4=ruta, texto=texto, canal=canal,
                                    en=fecha, programada_para=getattr(publicador, "programada_para", None))
        if v["ok"]:
            return
        motivo = v["motivo"]
    db.registrar_evento(conn, fecha, "publicacion RECHAZADA pieza %d en %s: %s" % (pieza_id, canal, motivo))
    raise SinAprobacionValida("pieza %d, canal %s: %s" % (pieza_id, canal, motivo))


def publicar_pieza(conn, pieza_id: int, *, publicador, ledger: Ledger,
                   canal: str = CANAL_DEFECTO, fecha: str) -> dict:
    """Publica (o programa) la pieza por `publicador()` y lo registra con seguridad.

    `publicador`: callable sin argumentos que publica y devuelve {'video_id': ...}
    (o lanza). DEBE declarar `ruta_mp4` y `texto` (y `programada_para` si programa):
    contra eso se verifica la aprobacion de operador. Resultado: `ya_publicada |
    ya_programada | intento_sin_confirmar | reconciliada | programada_reconciliada |
    error_api | resultado_incierto | publicada | programada | publicado_pendiente_bd |
    en_bandeja | ya_en_bandeja` (los dos últimos: publicador con `destino = "bandeja"`).
    Sin aprobacion valida lanza `SinAprobacionValida` y NO llama al canal.
    """
    if _estado_pieza(conn, pieza_id) is None:
        raise ValueError("la pieza %d no existe" % pieza_id)
    programada_para = getattr(publicador, "programada_para", None)

    # 1) RECONCILIAR-O-SALTAR antes de publicar. Nunca se re-llama al canal si ya
    #    hay evidencia de que la pieza fue publicada.
    pub = db.obtener_publicacion(conn, pieza_id, canal)
    if pub is not None:
        if pub["estado"] == db.PUB_CONFIRMADA:
            return {"resultado": "ya_publicada", "pieza_id": pieza_id,
                    "identificador_remoto": pub["identificador_remoto"]}
        if pub["estado"] == db.PUB_PROGRAMADA:
            return {"resultado": "ya_programada", "pieza_id": pieza_id,
                    "identificador_remoto": pub["identificador_remoto"],
                    "programada_para": pub["programada_para"]}
        if pub["estado"] == db.PUB_EN_BANDEJA:
            # ya hay un borrador en la bandeja del creador: no se sube otro.
            return {"resultado": "ya_en_bandeja", "pieza_id": pieza_id,
                    "identificador_remoto": pub["identificador_remoto"]}
        if pub["estado"] == db.PUB_PENDIENTE:
            # intento anterior con resultado INCIERTO: pudo haber salido. No se reintenta.
            return {"resultado": "intento_sin_confirmar", "pieza_id": pieza_id,
                    "error": pub["detalle_error"]}
        if pub["estado"] == db.PUB_RECONCILIACION_PENDIENTE:
            return reconciliar(conn, pieza_id, canal=canal, fecha=fecha)
    reg = ledger.obtener(pieza_id, canal)
    if reg is not None:
        # el canal confirmo antes (ledger) pero la BD no tiene fila CONFIRMADA:
        # se reconcilia SIN volver a llamar al canal.
        return reconciliar(conn, pieza_id, canal=canal, fecha=fecha,
                           identificador_remoto=reg.get("identificador_remoto"),
                           url=reg.get("url"), programada_para=reg.get("programada_para"))

    # 2) COMPUERTA: aprobacion vigente de operador para lo que el publicador va a subir.
    _exigir_aprobacion(conn, pieza_id, publicador, canal, fecha)

    # 3) estado publicable (PUBLICADA: ya salio en otro canal; este es uno mas)
    estado = _estado_pieza(conn, pieza_id)
    if estado not in ("PRODUCIDA", "LISTA", "PUBLICADA"):
        raise PublicacionError(
            "la pieza %d esta en %s; solo se publica desde PRODUCIDA, LISTA o PUBLICADA"
            % (pieza_id, estado))

    # 4) API del canal. Un RECHAZO seguro no deja fila (se puede reintentar). Cualquier
    #    otro fallo es INCIERTO: queda fila PENDIENTE que bloquea el reintento.
    try:
        resp = publicador()
    except RechazoDelCanal as e:
        db.registrar_evento(
            conn, fecha, "fallo al publicar pieza %d en %s: %s" % (pieza_id, canal, str(e)[:160]))
        return {"resultado": "error_api", "pieza_id": pieza_id, "error": str(e)[:200]}
    except Exception as e:  # noqa: BLE001 - el motivo queda en la fila y en el evento
        return _incierto(conn, pieza_id, canal, fecha, "excepcion: %s" % str(e)[:160])

    vid = str((resp or {}).get("video_id", "")).strip()
    url = (resp or {}).get("url") or ("/reel/%s/" % vid if vid else None)
    if not vid:
        return _incierto(conn, pieza_id, canal, fecha, "el publicador no devolvio video_id")

    # 5) EVIDENCIA REMOTA DURABLE antes de tocar la BD (sobrevive a un fallo de BD).
    ledger.anota(pieza_id, canal, vid, url, programada_para)

    if programada_para:
        try:
            if _estado_pieza(conn, pieza_id) == "PRODUCIDA":
                db.transicionar(conn, pieza_id, "PRODUCIDA", "LISTA", fecha,
                                "lista: programada en %s para %s" % (canal, programada_para))
            db.registrar_publicacion(
                conn, pieza_id=pieza_id, canal=canal, publicada_en=fecha,
                identificador_remoto=vid, url=url, estado_pub=db.PUB_PROGRAMADA,
                programada_para=programada_para)
        except Exception as e:  # noqa: BLE001
            return {"resultado": "publicado_pendiente_bd", "pieza_id": pieza_id,
                    "identificador_remoto": vid, "error": str(e)[:200]}
        return {"resultado": "programada", "pieza_id": pieza_id, "identificador_remoto": vid,
                "programada_para": programada_para}

    if getattr(publicador, "destino", None) == "bandeja":
        # 31-oct-2026: el canal recibió un BORRADOR en la bandeja del creador (TikTok sin auditar).
        # Queda EN_BANDEJA: NO es publicada; se confirma después con confirmar_programada + evidencia.
        try:
            if _estado_pieza(conn, pieza_id) == "PRODUCIDA":
                db.transicionar(conn, pieza_id, "PRODUCIDA", "LISTA", fecha,
                                "lista: borrador en la bandeja de %s" % canal)
            db.registrar_publicacion(
                conn, pieza_id=pieza_id, canal=canal, publicada_en=fecha,
                identificador_remoto=vid, url=url, estado_pub=db.PUB_EN_BANDEJA)
        except Exception as e:  # noqa: BLE001
            return {"resultado": "publicado_pendiente_bd", "pieza_id": pieza_id,
                    "identificador_remoto": vid, "error": str(e)[:200]}
        return {"resultado": "en_bandeja", "pieza_id": pieza_id, "identificador_remoto": vid}

    # 6) BD en orden seguro. La fila se escribe (RECONCILIACION_PENDIENTE) ANTES de
    #    la transicion a PUBLICADA; al terminar, se marca CONFIRMADA.
    try:
        if _estado_pieza(conn, pieza_id) == "PRODUCIDA":
            db.transicionar(conn, pieza_id, "PRODUCIDA", "LISTA", fecha,
                            "lista para distribucion: artefacto renderizado presente")
        db.registrar_publicacion(
            conn, pieza_id=pieza_id, canal=canal, publicada_en=fecha,
            identificador_remoto=vid, url=url, estado_pub=db.PUB_RECONCILIACION_PENDIENTE)
        _asegurar_publicada(conn, pieza_id, fecha,
                            "publicado en %s con video_id=%s" % (canal, vid))
        db.actualizar_estado_publicacion(conn, pieza_id, canal, db.PUB_CONFIRMADA)
    except Exception as e:  # noqa: BLE001
        return {"resultado": "publicado_pendiente_bd", "pieza_id": pieza_id,
                "identificador_remoto": vid, "error": str(e)[:200]}

    return {"resultado": "publicada", "pieza_id": pieza_id, "identificador_remoto": vid}


def _incierto(conn, pieza_id: int, canal: str, fecha: str, motivo: str) -> dict:
    """Deja fila PENDIENTE: el canal pudo haber publicado. Bloquea el reintento."""
    db.registrar_publicacion(conn, pieza_id=pieza_id, canal=canal, publicada_en=fecha,
                             estado_pub=db.PUB_PENDIENTE, detalle_error=motivo[:200])
    db.registrar_evento(conn, fecha, "resultado INCIERTO al publicar pieza %d en %s: %s. "
                        "No se reintenta sin revisar el canal (liberar_intento)" % (pieza_id, canal, motivo))
    return {"resultado": "resultado_incierto", "pieza_id": pieza_id, "error": motivo[:200]}


def liberar_intento(conn, pieza_id: int, *, canal: str, fecha: str, evidencia: str) -> None:
    """Quita el bloqueo de un intento INCIERTO tras comprobar que NO salio en el canal.

    `evidencia` es obligatoria (que se reviso y donde). Si el canal SI lo publico, no
    se libera: se usa `reconciliar` con su identificador remoto.
    """
    if not evidencia or not evidencia.strip():
        raise PublicacionError("liberar un intento exige evidencia de que no se publico")
    pub = db.obtener_publicacion(conn, pieza_id, canal)
    if pub is None or pub["estado"] != db.PUB_PENDIENTE:
        raise PublicacionError("la pieza %d no tiene un intento PENDIENTE en %s" % (pieza_id, canal))
    with conn:
        conn.execute("DELETE FROM publicaciones WHERE pieza_id = ? AND canal = ? AND estado = ?",
                     (pieza_id, canal, db.PUB_PENDIENTE))
        conn.execute("INSERT INTO eventos (pieza_id, de_estado, a_estado, en, detalle) "
                     "VALUES (?, NULL, NULL, ?, ?)",
                     (pieza_id, fecha, "intento PENDIENTE en %s liberado: %s" % (canal, evidencia.strip())))


def confirmar_programada(conn, pieza_id: int, *, canal: str, fecha: str, evidencia: str,
                         url: str | None = None) -> dict:
    """PROGRAMADA o EN_BANDEJA -> CONFIRMADA (publicada) con evidencia leida del canal. Pieza -> PUBLICADA."""
    if not evidencia or not evidencia.strip():
        raise PublicacionError("confirmar una programada exige evidencia leida del canal")
    pub = db.obtener_publicacion(conn, pieza_id, canal)
    if pub is None or pub["estado"] not in (db.PUB_PROGRAMADA, db.PUB_EN_BANDEJA):
        raise PublicacionError("la pieza %d no tiene publicacion PROGRAMADA ni EN_BANDEJA en %s" % (pieza_id, canal))
    _asegurar_publicada(conn, pieza_id, fecha, "publicada en %s (programada para %s): %s"
                        % (canal, pub["programada_para"], evidencia.strip()))
    db.actualizar_estado_publicacion(conn, pieza_id, canal, db.PUB_CONFIRMADA, url=url, publicada_en=fecha)
    return {"resultado": "confirmada", "pieza_id": pieza_id,
            "identificador_remoto": pub["identificador_remoto"]}
