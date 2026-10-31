# -*- coding: utf-8 -*-
"""Pruebas de la generacion por bloques.

Se prueba con un modelo falso: lo que hay que verificar es la ORQUESTACION
-cuantas llamadas, con que contexto, que se une- y eso no necesita Ollama.
Que el modelo escriba bien es otra medicion, y se hace corriendolo.
"""
from __future__ import annotations

import synthesis.guion_por_bloques as gb


class ModeloFalso:
    """Devuelve arco y escenas segun lo que le pidan. Guarda los prompts."""

    def __init__(self, n_arco=12, escenas_vacias=False, falla_en=None):
        self.prompts: list[str] = []
        self.n_arco = n_arco
        self.escenas_vacias = escenas_vacias
        self.falla_en = falla_en or []

    def __call__(self, prompt, options=None):
        self.prompts.append(prompt)
        if len(self.prompts) in self.falla_en:
            raise RuntimeError("modelo caido")
        if "NO escribas el guion todavia" in prompt:
            return {"title": "Un titulo",
                    "arco": ["pasa algo %d" % i for i in range(self.n_arco)]}
        # Cuantas escenas se pidieron en este bloque
        n = int(prompt.split("EXACTAMENTE ")[1].split(" escenas")[0])
        if self.escenas_vacias:
            return {"scenes": []}
        return {"scenes": [{"narration": "palabra%d " % (len(self.prompts))
                                         * 60,
                            "visual_description": "algo",
                            "search_keywords": "desk office laptop papers"}
                           for _ in range(n)]}


# ── tamaño de bloque ─────────────────────────────────────────────────────

def test_gb_01a_el_bloque_sale_de_las_palabras_no_de_un_numero_fijo():
    """300 palabras por llamada es el techo MEDIDO; el bloque se deriva."""
    assert gb.escenas_por_bloque(67) == 4      # 300/67 = 4
    assert gb.escenas_por_bloque(150) == 2     # 300/150 = 2
    assert gb.escenas_por_bloque(30) == 5      # 300/30 = 10, topado en 5


def test_gb_01b_el_bloque_nunca_sale_del_rango_util():
    """Menos de 2 son doce viajes; mas de 5 vuelve a rozar el techo."""
    for pal in (1, 5, 10, 67, 200, 500, 5000):
        assert gb.MIN_ESCENAS_BLOQUE <= gb.escenas_por_bloque(pal) \
            <= gb.MAX_ESCENAS_BLOQUE


def test_gb_01c_palabras_invalidas_no_revientan():
    assert gb.escenas_por_bloque(0) == gb.MAX_ESCENAS_BLOQUE
    assert gb.escenas_por_bloque(-5) == gb.MAX_ESCENAS_BLOQUE


# ── orquestacion ─────────────────────────────────────────────────────────

def test_gb_02a_devuelve_TODAS_las_escenas_pedidas():
    """Es el punto entero del modulo: el monolitico entregaba 12 escenas de
    17 palabras cuando se pedian de 67. MEDIDO: 204 palabras de 804."""
    m = ModeloFalso()
    r = gb.generar(m, "un tema", n=12, palabras_escena=67)
    assert len(r["scenes"]) == 12


def test_gb_02b_primero_el_arco_y_despues_los_bloques():
    """Sin plan comun cada bloque reinventa la historia y repite hechos."""
    m = ModeloFalso()
    gb.generar(m, "un tema", n=12, palabras_escena=67)
    assert "NO escribas el guion todavia" in m.prompts[0]
    # 12 escenas en bloques de 4 = 3 llamadas, mas la del arco
    assert len(m.prompts) == 4
    for p in m.prompts[1:]:
        assert "ESCRIBE UNICAMENTE LAS ESCENAS" in p


def test_gb_02c_cada_bloque_ve_el_arco_completo():
    """Saber donde esta parado es lo que evita que el bloque 3 vuelva a
    contar lo del 1."""
    m = ModeloFalso()
    gb.generar(m, "un tema", n=12, palabras_escena=67)
    for p in m.prompts[1:]:
        assert "ARCO COMPLETO" in p
        assert "pasa algo 11" in p       # la ultima linea del arco tambien


def test_gb_02d_el_segundo_bloque_recibe_lo_ya_escrito():
    """Sin la cola del bloque anterior, el siguiente abre en frio."""
    m = ModeloFalso()
    gb.generar(m, "un tema", n=12, palabras_escena=67)
    assert "YA SE ESCRIBIO" not in m.prompts[1]     # el primero no tiene que
    assert "YA SE ESCRIBIO" in m.prompts[2]
    assert "palabra2" in m.prompts[2]               # texto del bloque 1


def test_gb_02e_no_pide_mas_escenas_que_las_que_faltan():
    """El ultimo bloque suele ser mas corto: 12 en bloques de 5 son 5+5+2."""
    m = ModeloFalso()
    r = gb.generar(m, "un tema", n=12, palabras_escena=30)
    assert len(r["scenes"]) == 12
    assert "ESCENAS 11 A 12" in m.prompts[-1]


def test_gb_02f_marca_como_se_genero():
    """Un guion por bloques y uno monolitico no se comportan igual; medirlos
    juntos sin distinguirlos esconde cual de los dos fallo."""
    r = gb.generar(ModeloFalso(), "un tema", n=6, palabras_escena=67)
    assert r["metodo_generacion"] == "bloques"
    assert r["escenas_por_bloque"] == 4


# ── fallas ───────────────────────────────────────────────────────────────

def test_gb_03a_arco_corto_se_completa_antes_de_escribir():
    """El modelo tambien se queda corto en el arco: pedidas 12, llegan 5.
    Una escena sin linea de arco se inventa sola y repite lo ya contado."""
    m = ModeloFalso(n_arco=5)
    r = gb.generar(m, "un tema", n=12, palabras_escena=67)
    assert len(r["arco"]) == 12
    assert len(r["scenes"]) == 12


def test_gb_03a2_arco_VACIO_se_detiene_no_se_rellena():
    """REGRESION del defecto del 2026-08-22, que corrio en silencio.

    `_call_ollama` exige la clave `scenes`; el arco devuelve {title, arco} y
    se reintentaba con un prompt "mas estricto" que lo convertia en escenas.
    Este modulo recibia arco vacio, lo rellenaba con doce "continua la
    historia", y los tres bloques corrian SIN PLAN. Salida: JSON valido, 12
    escenas presentes, hechos repetidos entre bloques y ni una cifra en un
    documental sobre un robo de 81 millones. Ni una excepcion.

    Un arco vacio no es un arco corto: no hay nada que guie. Se detiene.
    """
    m = ModeloFalso(n_arco=0)
    try:
        gb.generar(m, "un tema", n=12, palabras_escena=67)
        assert False, "un arco vacio debe detener, no rellenarse"
    except gb.ArcoVacioError:
        pass
    # Y se detiene ANTES de gastar las llamadas de los bloques.
    assert len(m.prompts) == 1


def test_gb_03a3_el_plan_entra_por_su_propio_canal():
    """El arco NO devuelve escenas, asi que no puede pasar por el validador
    de guiones. Se le da un canal aparte o vuelve el defecto de arriba."""
    llamadas_plan: list[str] = []

    def plan(prompt, options=None):
        llamadas_plan.append(prompt)
        return {"title": "T", "arco": ["a%d" % i for i in range(12)]}

    m = ModeloFalso()
    r = gb.generar(m, "un tema", n=12, palabras_escena=67, llamar_plan=plan)
    assert len(llamadas_plan) == 1
    assert "NO escribas el guion todavia" in llamadas_plan[0]
    # El canal normal solo escribio bloques, nunca el plan.
    assert all("ESCRIBE UNICAMENTE" in p for p in m.prompts)
    assert r["arco"][0] == "a0"


def test_gb_03b_arco_largo_se_recorta():
    m = ModeloFalso(n_arco=30)
    r = gb.generar(m, "un tema", n=6, palabras_escena=67)
    assert len(r["arco"]) == 6


def test_gb_03c_un_bloque_caido_no_tira_el_guion():
    """Un guion al que le faltan tres escenas todavia sirve."""
    m = ModeloFalso(falla_en=[2])        # cae el primer bloque, no el arco
    r = gb.generar(m, "un tema", n=12, palabras_escena=67)
    assert len(r["scenes"]) == 8         # 12 menos las 4 del bloque caido
    assert r["title"] == "Un titulo"


def test_gb_03d_si_falla_el_arco_se_detiene():
    """Sin plan los bloques se repiten entre si: el resultado es PEOR que el
    monolitico, no solo mas corto. Aqui si conviene fallar."""
    m = ModeloFalso(falla_en=[1])
    try:
        gb.generar(m, "un tema", n=12, palabras_escena=67)
        assert False, "deberia haber levantado"
    except RuntimeError:
        pass


def test_gb_03e_bloque_vacio_no_revienta():
    m = ModeloFalso(escenas_vacias=True)
    r = gb.generar(m, "un tema", n=12, palabras_escena=67)
    assert r["scenes"] == []


# ── hechos repetidos: el defecto propio de los bloques ───────────────────

def test_gb_04a_detecta_el_hecho_contado_dos_veces():
    """Despues de descomponer, contar palabras deja de discriminar -se
    cumple por construccion-. Lo que hay que vigilar es la repeticion."""
    g = {"scenes": [
        {"narration": "los hackers vaciaron la cuenta del banco central"},
        {"narration": "nadie sabia nada todavia"},
        {"narration": "esa noche los hackers vaciaron la cuenta y huyeron"},
    ]}
    avisos = gb.hechos_repetidos(g)
    assert avisos
    assert "1 y 3" in avisos[0]
    assert "reescribe la 3" in avisos[0]


def test_gb_04b_un_guion_sin_repeticiones_no_avisa():
    g = {"scenes": [
        {"narration": "la impresora del banco central acaba de apagarse"},
        {"narration": "en Manila un casino recibe cinco transferencias"},
    ]}
    assert gb.hechos_repetidos(g) == []


def test_gb_04c_las_muletillas_comunes_no_cuentan_como_hecho():
    """'de la de la' se repite en cualquier texto largo y no es un hecho:
    si contara, el detector avisaria siempre y no serviria de nada."""
    g = {"scenes": [{"narration": "de la de la de la"},
                    {"narration": "de la de la de la"}]}
    assert gb.hechos_repetidos(g) == []


def test_gb_04d_avisa_una_sola_vez_por_par_de_escenas():
    """Dos escenas casi iguales generarian decenas de avisos identicos y el
    bucle de correccion recibiria ruido en vez de un defecto accionable."""
    texto = "los investigadores encontraron la falla en el sistema swift"
    g = {"scenes": [{"narration": texto}, {"narration": texto}]}
    assert len(gb.hechos_repetidos(g)) == 1


def test_gb_04e_guion_vacio_no_revienta():
    assert gb.hechos_repetidos({}) == []
    assert gb.hechos_repetidos({"scenes": []}) == []
